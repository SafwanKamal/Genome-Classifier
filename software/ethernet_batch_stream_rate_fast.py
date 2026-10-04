"""Raw-byte batch capture with reply credits refilled before golden validation."""
import argparse
import json
import math
import queue
import threading
import time
import hashlib
import platform
from pathlib import Path

import numpy as np
import pandas as pd
from scapy.all import get_if_hwaddr
from scapy import __version__ as scapy_version
from scapy.interfaces import resolve_iface
from batch_model_reference import BatchModelReference
from ethernet_batch_protocol import build_batch, decode_batch
from ethernet_batch_stream_rate import summarize_timing, precise_wall_time


class RawCapture:
    """Select the capture socket, then receive bytes without creating Packets."""
    def __init__(self, socket, replies, threshold, timing=None):
        self.socket, self.replies = socket, replies
        self.threshold, self.timing = threshold, timing
        self.ready, self.stopped = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self._run, name='raw-batch-capture', daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stopped.set()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise TimeoutError('Raw capture did not stop')

    def _run(self):
        self.ready.set()
        try:
            while not self.stopped.is_set():
                if not self.socket.select([self.socket], 0.05):
                    continue
                _, frame, captured = self.socket.recv_raw()
                if frame is None:
                    continue
                callback_wall = precise_wall_time() if self.timing is not None else None
                callback_started = time.perf_counter() if self.timing is not None else None
                decoded = decode_batch(frame, self.threshold)
                if decoded is not None:
                    self.replies.put((decoded, time.perf_counter(), None))
                    if self.timing is not None:
                        if captured is None or not math.isfinite(float(captured)):
                            self.timing['capture_timestamp_unavailable'] += 1
                        else:
                            self.timing['capture_to_callback'].append(callback_wall-float(captured))
                            self.timing['capture_timestamp'].append(float(captured))
                if self.timing is not None:
                    self.timing['callback_decode'].append(time.perf_counter()-callback_started)
        except Exception as error:
            self.replies.put((None, time.perf_counter(), f'Raw capture failed: {error}'))


def run_window(socket, batches, replies, expected, sequence, window, timeout, timing=None):
    pending, seen = {}, set()
    next_batch = received = correct = duplicates = unrelated = 0
    mismatches = []
    finished = None
    started = time.perf_counter()
    send_times = {}
    last_arrival = None
    def refill():
        nonlocal next_batch
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

    refill()
    while pending:
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
        # Sequence, count and protocol flags are checked before returning credit.
        # Golden checking stays mandatory, but cannot delay its replacement send.
        refill()
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
    parser.add_argument('--window', type=int, choices=(1,2,4), default=2)
    parser.add_argument('--sequence', type=int, default=5000000)
    parser.add_argument('--timeout', type=float, default=2)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--timing', action='store_true', help='Record host send, reply and refill timing; adds measurement overhead')
    parser.add_argument('--design-label', default='', help='User label identifying the programmed design')
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
    timing = {key: [] for key in ('send_call', 'queue_wait', 'send_to_reply',
              'reply_to_dequeue', 'reply_to_refill', 'send_started', 'reply_arrived', 'callback_decode',
              'capture_to_callback', 'capture_timestamp')} if args.timing else None
    if timing is not None:
        timing['capture_timestamp_unavailable'] = 0
    iface=resolve_iface(args.interface)
    socket=iface.l2socket()(iface=iface)
    capture_socket = None
    receiver = None
    try:
        capture_socket = iface.l2listen()(iface=iface,
            filter='ether proto 0x88b5 and ether src 02:00:00:00:00:01')
        receiver = RawCapture(capture_socket, replies, model.manifest['routing_threshold'], timing)
        receiver.start()
        if not receiver.ready.wait(args.timeout):raise TimeoutError('Capture did not become ready')
        observed_start=time.perf_counter()
        result=run_window(socket,batches,replies,expected,args.sequence,args.window,args.timeout,timing)
        observed_seconds=time.perf_counter()-observed_start
    finally:
        try:
            if receiver is not None:
                receiver.stop()
        finally:
            if capture_socket is not None and (receiver is None or not receiver.thread.is_alive()):
                capture_socket.close()
            socket.close()
    seconds=result['completed_seconds']
    result.update(requested=args.count,missing=args.count-result['received'],
                  batch_size=args.batch_size,window=args.window,split=args.split,
                  manifest=str(args.manifest),first_sequence=args.sequence,
                  completed_variants_per_second=args.count/seconds if seconds else None,
                  timing_scope=('first send to last decoded host-captured reply; golden checks overlap transfer, '
                                'final validation and preparation/startup excluded; rate published only if all scores correct'),
                  identity=dict(hostname=platform.node(), python=platform.python_version(),
                                scapy=scapy_version,
                                interface_requested=args.interface,
                                interface_name=str(iface.name), interface_description=str(iface.description),
                                interface_network_name=str(iface.network_name),
                                capture_backend=type(capture_socket).__name__, sender_backend=type(socket).__name__,
                                design_label=args.design_label,
                                manifest_sha256=hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
                                input_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest()))
    if timing is not None:
        result['host_timing'] = summarize_timing(timing, observed_seconds)
        result['host_timing']['scope'] = (
            'Host observations, not FPGA or wire timestamps. Queue waits include hardware, network and '
            'capture delay. Raw receiver entry follows recv_raw; no Scapy packet dissection or reconstruction '
            'occurs. callback_decode includes decoding and queue publication. Capture-to-callback compares '
            'driver capture epoch time with precise wall time after recv_raw and includes driver delivery and '
            'thread scheduling. Negative samples can indicate clock mismatch or wall-clock adjustment. '
            'Other main-thread time includes golden checks, orchestration and measurement. Durations overlap; '
            'do not add latency and callback totals to send and wait totals.')
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    raise SystemExit(int(result['error'] is not None or result['correct']!=args.count or result['duplicates']!=0))


if __name__=='__main__':main()
