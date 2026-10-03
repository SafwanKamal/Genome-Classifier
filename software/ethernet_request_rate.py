"""Send paced RX01 requests without waiting for replies; report host-observed loss."""
from __future__ import annotations

import argparse
import profile
from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import pstats
import time

from scapy.all import AsyncSniffer, Ether, get_if_hwaddr, sendp
from scapy.interfaces import resolve_iface

from model_vectors import load_vectors
from decode_ethernet_result import (
    DEFAULT_ROUTING_THRESHOLD, ETHERTYPE, EXPECTED_SOURCE_MAC,
    ResultPacket, decode_result_packet,
)
from send_ethernet_RX_test import build_frame


def send_frames(frames, interface: str, interval: float, sender: str) -> None:
    if sender == "scapy":
        sendp(frames, iface=interface, inter=interval, verbose=False)
        return
    # Prebuilt bytes avoid Scapy's per-packet layer cloning and serialization.
    iface = resolve_iface(interface)
    socket = iface.l2socket()(iface=iface)
    try:
        for frame in frames:
            socket.send(frame)
            if interval > 0:
                time.sleep(interval)
    finally:
        socket.close()


def summarize(expected: dict[int, int], replies: list[ResultPacket], threshold: int) -> dict:
    seen: set[int] = set()
    correct: set[int] = set()
    duplicates = unrelated = out_of_order = 0
    mismatches = []
    previous = None
    for reply in replies:
        seq = reply.sequence_number
        if seq not in expected:
            unrelated += 1
            continue
        if seq in seen:
            duplicates += 1
        else:
            if previous is not None and seq < previous:
                out_of_order += 1
            previous = seq
        seen.add(seq)
        score = expected[seq]
        if (reply.score, reply.classification, reply.deep_review) == (
                score, int(score >= 0), int(score >= threshold)):
            correct.add(seq)
        else:
            mismatches.append({"sequence": seq, "expected_score": score,
                               "score": reply.score, "classification": reply.classification,
                               "deep_review": reply.deep_review})
    return {"requested": len(expected), "received_unique": len(seen),
            "correct_unique": len(correct), "missing_sequences": sorted(expected.keys() - seen),
            "duplicates": duplicates, "out_of_order": out_of_order,
            "unrelated_replies": unrelated, "mismatches": mismatches}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", help="Scapy/Npcap wired interface, e.g. Ethernet")
    parser.add_argument("--vectors", type=Path, default=Path("simulation/model_v2_h8_core_vectors.mem"))
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--sequence", type=int, default=100_000)
    parser.add_argument("--interval", type=float, default=0.01,
                        help="Requested seconds between sends; 0 sends as fast as Scapy can")
    parser.add_argument("--sender", choices=("scapy", "raw"), default="scapy",
                        help="scapy: existing sendp; raw: send prebuilt bytes through one socket")
    parser.add_argument("--timeout", type=float, default=2.0,
                        help="Additional capture seconds after the send call completes")
    parser.add_argument("--routing-threshold", type=int, default=DEFAULT_ROUTING_THRESHOLD)
    parser.add_argument("--report", type=Path, help="Save measured results as JSON")
    parser.add_argument("--profile-send", type=Path,
                        help="Save a sending-thread profile; profiling affects timing")
    parser.add_argument("--dry-run", action="store_true", help="Preview requests without hardware access")
    args = parser.parse_args()
    if args.count < 1 or args.interval < 0 or args.timeout <= 0:
        parser.error("count and timeout must be positive; interval must be nonnegative")
    if args.sequence < 0 or args.sequence + args.count - 1 > 0xFFFFFFFF:
        parser.error("request sequences must fit in 32 bits")
    if not args.dry_run and not args.interface:
        parser.error("--interface is required for a physical test")
    vectors = load_vectors(args.vectors, args.count)
    expected = {args.sequence + i: score for i, (_, score) in enumerate(vectors)}
    source_mac = "02:00:00:00:00:02" if args.dry_run else get_if_hwaddr(args.interface)
    frames = [build_frame(source_mac, args.sequence + i, features)
              for i, (features, _) in enumerate(vectors)]
    if args.sender == "scapy":
        frames = [Ether(frame) for frame in frames]
    if args.dry_run:
        print(f"Prepared {len(frames)} vector requests, sequences "
              f"{args.sequence} through {args.sequence + args.count - 1}; interval={args.interval:g} s")
        print(f"First request ({len(bytes(frames[0]))} bytes): {bytes(frames[0]).hex(' ')}")
        print(f"Golden score range: {min(expected.values())} to {max(expected.values())}")
        return 0

    captured: queue.Queue[tuple[ResultPacket, float]] = queue.Queue()
    def receive(packet) -> None:
        result = decode_result_packet(packet, args.routing_threshold)
        if result is not None:
            captured.put((result, time.perf_counter()))
    sniffer = AsyncSniffer(iface=args.interface,
                          filter=f"ether proto 0x{ETHERTYPE:04x} and ether src {EXPECTED_SOURCE_MAC}",
                          prn=receive, store=False)
    print(f"Sending {args.count} requests on {args.interface}; interval={args.interval:g} s; "
          f"sender={args.sender}", flush=True)
    sniffer.start()
    try:
        time.sleep(0.5)  # Allow Npcap to install the capture filter.
        # Python 3.12 cProfile also captures the sniffer thread on this runtime.
        # profile.Profile.runcall uses the thread-specific sys.setprofile hook.
        profiler = profile.Profile(time.perf_counter) if args.profile_send else None
        started = time.perf_counter()
        if profiler:
            profiler.runcall(send_frames, frames, args.interface, args.interval, args.sender)
        else:
            send_frames(frames, args.interface, args.interval, args.sender)
        send_seconds = time.perf_counter() - started
        time.sleep(args.timeout)
    finally:
        if sniffer.running:
            sniffer.stop()
    if profiler:
        args.profile_send.parent.mkdir(parents=True, exist_ok=True)
        profiler.dump_stats(str(args.profile_send))
        print("Send-only profile (cumulative times overlap; profiling affects timing):")
        pstats.Stats(profiler).strip_dirs().sort_stats("cumtime").print_stats(15)
    replies = []
    correct_arrival_times = {}
    while not captured.empty():
        result, arrived = captured.get_nowait()
        replies.append(result)
        score = expected.get(result.sequence_number)
        if score is not None and (result.score, result.classification, result.deep_review) == (
                score, int(score >= 0), int(score >= args.routing_threshold)):
            correct_arrival_times.setdefault(result.sequence_number, arrived)
    report = summarize(expected, replies, args.routing_threshold)
    completion_seconds = (max(correct_arrival_times.values()) - started
                          if report['correct_unique'] == args.count else None)
    report.update({"captured_at_utc": datetime.now(timezone.utc).isoformat(),
                   "interface": args.interface, "vectors": str(args.vectors),
                   "first_sequence": args.sequence, "interval_seconds": args.interval,
                   "sender": args.sender,
                   "all_correct_completion_seconds": completion_seconds,
                   "completed_variants_per_second": (
                       args.count / completion_seconds if completion_seconds else None),
                   "completion_timing_scope": "send start to last correct host-captured reply; excludes preparation/capture startup",
                   "capture_tail_seconds": args.timeout, "send_call_seconds": send_seconds,
                   "send_profile": str(args.profile_send) if args.profile_send else None,
                   "send_profiler": "profile.Profile; sending thread" if args.profile_send else None,
                   "host_send_call_requests_per_second": args.count / send_seconds})
    print(f"Received {report['received_unique']}/{args.count}; correct {report['correct_unique']}; "
          f"missing {len(report['missing_sequences'])}; duplicates {report['duplicates']}; "
          f"out of order {report['out_of_order']}; mismatches {len(report['mismatches'])}")
    print(f"Send call: {send_seconds:.3f} s, {args.count / send_seconds:.1f} requests/s "
          "(host observed, not a wire-rate measurement)")
    if completion_seconds:
        print(f"All correct replies: {completion_seconds:.3f} s, "
              f"{args.count / completion_seconds:.1f} completed variants/s (host observed)")
    if report["missing_sequences"]:
        print(f"Missing sequences: {report['missing_sequences']}")
    if report["mismatches"]:
        print(f"Mismatches: {report['mismatches']}")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Saved {args.report}")
    return int(bool(report["missing_sequences"] or report["mismatches"] or
                    report["duplicates"] or report["out_of_order"]))


if __name__ == "__main__":
    raise SystemExit(main())
