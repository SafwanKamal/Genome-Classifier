"""Windowed batch validator for the separate streamed Ethernet firmware."""
import argparse
import json
import math
import queue
import threading
import time
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scapy.all import AsyncSniffer, get_if_hwaddr
from scapy.interfaces import resolve_iface
from batch_model_reference import BatchModelReference
from ethernet_batch_protocol import build_batch, decode_batch


if sys.platform == 'win32':
    import ctypes
    from ctypes import wintypes
    _precise_filetime = ctypes.WinDLL('kernel32').GetSystemTimePreciseAsFileTime
    _precise_filetime.argtypes = [ctypes.POINTER(wintypes.FILETIME)]
    _precise_filetime.restype = None


def precise_wall_time():
    if sys.platform != 'win32':
        return time.time()
    stamp = wintypes.FILETIME()
    _precise_filetime(ctypes.byref(stamp))
    ticks = (stamp.dwHighDateTime << 32) | stamp.dwLowDateTime
    return (ticks-116444736000000000)/10000000


def capture_delivery_delay(packet, callback_wall_time):
    """Compare epoch capture time with epoch callback time, never perf_counter."""
    try:
        captured = float(packet.time)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
    return callback_wall_time-captured if math.isfinite(captured) else None


def run_window(socket, batches, replies, expected, sequence, window, timeout, timing=None):
    pending, seen = {}, set()
    next_batch = received = correct = duplicates = unrelated = 0
    mismatches = []
    finished = None
    started = time.perf_counter()
    send_times = {}
    last_arrival = None
    while next_batch < len(batches) or pending:
        while next_batch < len(batches) and len(pending) < window:
            begin, request, count = batches[next_batch]
            pending[sequence+begin] = (begin, count, time.monotonic()+timeout)
            before_send = time.perf_counter() if timing is not None else None
            socket.send(request)
            if timing is not None:
                after_send = time.perf_counter()
                send_times[sequence+begin] = before_send
                timing['send_call'].append(after_send-before_send)
                if last_arrival is not None:
                    timing['reply_to_refill'].append(before_send-last_arrival)
                timing['send_started'].append(before_send-started)
            next_batch += 1
        deadline = min(value[2] for value in pending.values())
        before_wait = time.perf_counter() if timing is not None else None
        try:
            decoded, arrived, error = replies.get(timeout=max(0, deadline-time.monotonic()))
        except queue.Empty:
            missing = sorted(pending)
            return dict(received=received, correct=correct, duplicates=duplicates,
                        unrelated_replies=unrelated, mismatches=mismatches,
                        error=f'Timed out waiting for batches {missing}', completed_seconds=None)
        finally:
            if timing is not None:
                timing['queue_wait'].append(time.perf_counter()-before_wait)
        if error:
            return dict(received=received, correct=correct, duplicates=duplicates,
                        unrelated_replies=unrelated, mismatches=mismatches,
                        error=error, completed_seconds=None)
        base, scores = decoded
        if base in seen:
            duplicates += 1
            continue
        if base not in pending:
            unrelated += 1
            continue
        begin, count, _ = pending.pop(base)
        if timing is not None:
            timing['send_to_reply'].append(arrived-send_times[base])
            timing['reply_to_dequeue'].append(time.perf_counter()-arrived)
            timing['reply_arrived'].append(arrived-started)
            last_arrival = arrived
        if len(scores) != count:
            return dict(received=received, correct=correct, duplicates=duplicates,
                        unrelated_replies=unrelated, mismatches=mismatches,
                        error='Reply batch count differs from request', completed_seconds=None)
        seen.add(base)
        received += count
        for i, score in enumerate(scores):
            if score == int(expected[begin+i]):
                correct += 1
            else:
                mismatches.append(dict(sequence=base+i, expected=int(expected[begin+i]), score=score))
        finished = max(finished or arrived, arrived)
    return dict(received=received, correct=correct, duplicates=duplicates,
                unrelated_replies=unrelated, mismatches=mismatches, error=None,
                completed_seconds=finished-started if correct==len(expected) else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interface', required=True)
    parser.add_argument('--manifest', type=Path, default=Path('genomic-dataset-pipeline/artifacts/model_v4_batch_seed_7/export_manifest.json'))
    parser.add_argument('--input', type=Path, default=Path('genomic-dataset-pipeline/data/processed/variants_model_int8.parquet'))
    parser.add_argument('--split', default='test')
    parser.add_argument('--count', type=int, default=1000)
    parser.add_argument('--batch-size', type=int, default=32)
    parser.add_argument('--window', type=int, choices=(1,2), default=2)
    parser.add_argument('--sequence', type=int, default=5000000)
    parser.add_argument('--timeout', type=float, default=2)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--timing', action='store_true', help='Record host send, reply and refill timing; adds measurement overhead')
    args=parser.parse_args()
    if args.count<1 or not 1<=args.batch_size<=32 or args.timeout<=0:
        parser.error('count/timeout must be positive; batch size must be 1..32')
    if not 0<=args.sequence<=0xffffffff-args.count+1:
        parser.error('Sequence range must fit uint32')
    model=BatchModelReference(args.manifest)
    frame=pd.read_parquet(args.input)
    rows=frame.loc[frame['split'].eq(args.split)].head(args.count)
    if len(rows)!=args.count:
        parser.error('Not enough variants in the requested split')
    features=rows[model.manifest['feature_order']].to_numpy(dtype=np.int64)
    if np.any(features < -128) or np.any(features > 127):
        raise ValueError('Features outside signed INT8 range')
    expected=model.score(features)
    source='02:00:00:00:00:02' if args.dry_run else get_if_hwaddr(args.interface)
    batches=[]
    for begin in range(0,args.count,args.batch_size):
        chunk=features[begin:begin+args.batch_size]
        request=build_batch(source,args.sequence+begin,[bytes(int(v)&255 for v in row) for row in chunk])
        batches.append((begin,request,len(chunk)))
    if args.dry_run:
        print(f'Prepared {len(batches)} frames for {args.count} variants; window={args.window}')
        return
    replies=queue.Queue()
    ready=threading.Event()
    timing = {key: [] for key in ('send_call', 'queue_wait', 'send_to_reply',
              'reply_to_dequeue', 'reply_to_refill', 'send_started', 'reply_arrived', 'callback_decode',
              'capture_to_callback', 'capture_timestamp')} if args.timing else None
    if timing is not None:
        timing['capture_timestamp_unavailable'] = 0
    def receive(packet):
        callback_wall = precise_wall_time() if timing is not None else None
        callback_started = time.perf_counter() if timing is not None else None
        try:
            decoded=decode_batch(bytes(packet),model.manifest['routing_threshold'])
            if decoded is not None:
                replies.put((decoded,time.perf_counter(),None))
                if timing is not None:
                    delay = capture_delivery_delay(packet, callback_wall)
                    if delay is None:
                        timing['capture_timestamp_unavailable'] += 1
                    else:
                        timing['capture_to_callback'].append(delay)
                        timing['capture_timestamp'].append(float(packet.time))
        except ValueError as error:
            replies.put((None,time.perf_counter(),str(error)))
        finally:
            if timing is not None:
                timing['callback_decode'].append(time.perf_counter()-callback_started)
    sniffer=AsyncSniffer(iface=args.interface,filter='ether proto 0x88b5 and ether src 02:00:00:00:00:01',
                         prn=receive,store=False,started_callback=ready.set)
    iface=resolve_iface(args.interface)
    socket=iface.l2socket()(iface=iface)
    try:
        sniffer.start()
        if not ready.wait(args.timeout):raise TimeoutError('Capture did not become ready')
        observed_start=time.perf_counter()
        result=run_window(socket,batches,replies,expected,args.sequence,args.window,args.timeout,timing)
        observed_seconds=time.perf_counter()-observed_start
    finally:
        try:
            if sniffer.running:sniffer.stop()
        finally:socket.close()
    seconds=result['completed_seconds']
    result.update(requested=args.count,missing=args.count-result['received'],
                  batch_size=args.batch_size,window=args.window,split=args.split,
                  manifest=str(args.manifest),first_sequence=args.sequence,
                  completed_variants_per_second=args.count/seconds if seconds else None,
                  timing_scope='first send to last correct host-captured reply; excludes preparation/capture startup')
    if timing is not None:
        result['host_timing'] = summarize_timing(timing, observed_seconds)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    raise SystemExit(int(result['error'] is not None or result['correct']!=args.count or result['duplicates']!=0))


def summarize_timing(timing, observed_seconds):
    def stats(values):
        return dict(samples=len(values), total_seconds=float(sum(values)),
                    median_us=float(np.median(values)*1e6) if values else None,
                    p95_us=float(np.percentile(values,95)*1e6) if values else None,
                    max_us=float(max(values)*1e6) if values else None)
    result = {key: stats(timing[key]) for key in ('send_call', 'queue_wait',
              'send_to_reply', 'reply_to_dequeue', 'reply_to_refill', 'callback_decode')}
    for source, name in (('send_started','send_spacing'),('reply_arrived','reply_spacing')):
        result[name] = stats(np.diff(timing[source]).tolist())
    capture_delays = timing.get('capture_to_callback', [])
    result['capture_to_callback'] = stats(capture_delays)
    result['capture_to_callback']['min_us'] = min(capture_delays)*1e6 if capture_delays else None
    result['capture_to_callback']['negative_samples'] = sum(value<0 for value in capture_delays)
    result['capture_to_callback']['timestamp_unavailable'] = timing.get('capture_timestamp_unavailable', 0)
    result['capture_spacing'] = stats(np.diff(timing.get('capture_timestamp', [])).tolist())
    result['capture_callback_clock'] = 'GetSystemTimePreciseAsFileTime' if sys.platform=='win32' else 'time.time'
    result['observer_loop_seconds'] = observed_seconds
    result['main_thread_other_seconds'] = max(0, observed_seconds-
        result['send_call']['total_seconds']-result['queue_wait']['total_seconds'])
    result['scope'] = ('Host wall-clock observations, not FPGA or wire timestamps. Queue waits include hardware, '
                       'network and capture delay. Callback timing excludes Scapy dissection before callback. '
                       'Other main-thread time includes validation, orchestration, scheduling and measurement. '
                       'Capture-to-callback compares packet.time with precise wall time at callback entry; includes '
                       'capture delivery, scheduling and pre-callback Scapy work. Capture timestamp location '
                       'depends on the driver and is not an FPGA timestamp. Negative samples may indicate '
                       'timestamp clock mismatch; wall-clock adjustments can affect this measurement. '
                       'Durations overlap; do not add latency/callback totals to send/wait totals.')
    return result


if __name__=='__main__':main()
