"""User-run stop-and-wait Ethernet batch correctness and completion benchmark."""
import argparse
import json
import queue
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scapy.all import AsyncSniffer, get_if_hwaddr
from scapy.interfaces import resolve_iface

from batch_model_reference import BatchModelReference
from ethernet_batch_protocol import build_batch, decode_batch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interface', required=True)
    parser.add_argument('--manifest', type=Path, default=Path('genomic-dataset-pipeline/artifacts/model_v4_batch_seed_7/export_manifest.json'))
    parser.add_argument('--input', type=Path, default=Path('genomic-dataset-pipeline/data/processed/variants_model_int8.parquet'))
    parser.add_argument('--split', default='test')
    parser.add_argument('--count', type=int, default=1000)
    parser.add_argument('--batch-size', type=int, default=32)
    parser.add_argument('--sequence', type=int, default=4000000)
    parser.add_argument('--timeout', type=float, default=2)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.count < 1 or not 1 <= args.batch_size <= 32 or args.timeout <= 0:
        parser.error('count/timeout must be positive; batch size must be 1..32')
    if not 0 <= args.sequence <= 0xffffffff-args.count+1:
        parser.error('Sequence range must fit uint32')
    model = BatchModelReference(args.manifest)
    frame = pd.read_parquet(args.input)
    rows = frame.loc[frame['split'].eq(args.split)].head(args.count)
    if len(rows) != args.count:
        parser.error('Not enough variants in the requested split')
    features = rows[model.manifest['feature_order']].to_numpy(dtype=np.int64)
    if np.any(features < -128) or np.any(features > 127):
        raise ValueError('Features outside signed INT8 range')
    expected = model.score(features)
    source_mac = '02:00:00:00:00:02' if args.dry_run else get_if_hwaddr(args.interface)
    batches = []
    for begin in range(0, args.count, args.batch_size):
        chunk = features[begin:begin+args.batch_size]
        raw = [bytes(int(v)&255 for v in row) for row in chunk]
        batches.append((begin, build_batch(source_mac, args.sequence+begin, raw), len(chunk)))
    if args.dry_run:
        print(f'Prepared {len(batches)} frames for {args.count} variants; first frame {len(batches[0][1])} bytes')
        return
    replies = queue.Queue()
    ready = threading.Event()
    threshold = model.manifest['routing_threshold']
    def receive(packet):
        try:
            decoded = decode_batch(bytes(packet), threshold)
            if decoded is not None:
                replies.put((decoded, time.perf_counter(), None))
        except ValueError as error:
            replies.put((None, time.perf_counter(), str(error)))
    sniffer = AsyncSniffer(iface=args.interface, filter='ether proto 0x88b5 and ether src 02:00:00:00:00:01',
                          prn=receive, store=False, started_callback=ready.set)
    iface = resolve_iface(args.interface)
    socket = iface.l2socket()(iface=iface)
    correct, received, unrelated, mismatches, failure = 0, 0, 0, [], None
    started = finished = None
    try:
        sniffer.start()
        if not ready.wait(args.timeout):
            raise TimeoutError('Capture did not become ready')
        started = time.perf_counter()
        for begin, request, count in batches:
            socket.send(request)
            deadline = time.monotonic()+args.timeout
            while True:
                try:
                    decoded, arrived, error = replies.get(timeout=max(0, deadline-time.monotonic()))
                except queue.Empty:
                    raise TimeoutError(f'Missing batch at sequence {args.sequence+begin}')
                if error:
                    raise ValueError(error)
                sequence, scores = decoded
                if sequence != args.sequence+begin:
                    unrelated += 1
                    continue
                if len(scores) != count:
                    raise ValueError('Reply batch count differs from request')
                received += count
                for i, score in enumerate(scores):
                    if score == int(expected[begin+i]):
                        correct += 1
                    else:
                        mismatches.append({'sequence': sequence+i, 'expected': int(expected[begin+i]), 'score': score})
                finished = arrived
                break
    except (TimeoutError, ValueError, OSError) as error:
        failure = str(error)
    finally:
        if sniffer.running:
            sniffer.stop()
        socket.close()
    seconds = finished-started if correct==args.count and started is not None else None
    report = {'requested': args.count, 'received': received, 'correct': correct,
              'missing': args.count-received, 'mismatches': mismatches, 'unrelated_replies': unrelated,
              'error': failure, 'batch_size': args.batch_size, 'split': args.split,
              'manifest': str(args.manifest), 'first_sequence': args.sequence,
              'completed_seconds': seconds,
              'completed_variants_per_second': args.count/seconds if seconds else None,
              'timing_scope': 'first batch send to last host-captured reply; includes waits between batches; excludes preparation/capture startup'}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
    raise SystemExit(int(failure is not None or correct != args.count))


if __name__ == '__main__':
    main()
