from __future__ import annotations

import argparse
import struct
from dataclasses import dataclass

from scapy.all import Ether, get_if_list, sniff, show_interfaces


ETHERTYPE = 0x88B5
EXPECTED_SOURCE_MAC = "02:00:00:00:00:01"
PROTOCOL_VERSION = 1
MESSAGE_RESULT = 1
DEFAULT_ROUTING_THRESHOLD = -276
RESULT_PAYLOAD_BYTES = 16


@dataclass
class ResultPacket:
    source_mac: str
    sequence_number: int
    score: int
    classification: int
    deep_review: int

    @property
    def classification_name(self) -> str:
        return "pathogenic-side" if self.classification else "benign-side"

    @property
    def routing_name(self) -> str:
        return "deep review" if self.deep_review else "light review"


def decode_result_packet(packet, routing_threshold: int) -> ResultPacket | None:
    if not packet.haslayer(Ether):
        return None

    ethernet = packet[Ether]

    if ethernet.type != ETHERTYPE:
        return None

    if ethernet.src.lower() != EXPECTED_SOURCE_MAC:
        return None

    payload = bytes(ethernet.payload)

    if len(payload) < RESULT_PAYLOAD_BYTES:
        print(
            f"Rejected short result payload: "
            f"received {len(payload)} bytes, expected at least {RESULT_PAYLOAD_BYTES}."
        )
        return None

    version = payload[0]
    message_type = payload[1]
    sequence_number = struct.unpack(">I", payload[2:6])[0]
    score = struct.unpack(">i", payload[6:10])[0]
    classification = payload[10]
    deep_review = payload[11]
    reserved = payload[12:16]

    if version != PROTOCOL_VERSION:
        print(f"Rejected packet: unsupported protocol version {version}.")
        return None

    if message_type != MESSAGE_RESULT:
        print(f"Rejected packet: unsupported message type {message_type}.")
        return None

    if classification not in (0, 1):
        print(f"Rejected packet: invalid classification value {classification}.")
        return None

    if deep_review not in (0, 1):
        print(f"Rejected packet: invalid routing value {deep_review}.")
        return None

    if reserved != bytes(4):
        print(f"Warning: reserved bytes are not zero: {reserved.hex(' ')}")

    expected_classification = int(score >= 0)
    expected_deep_review = int(score >= routing_threshold)

    if classification != expected_classification:
        print(
            "Warning: classification does not match score: "
            f"score={score}, packet={classification}, expected={expected_classification}"
        )

    if deep_review != expected_deep_review:
        print(
            "Warning: routing decision does not match score: "
            f"score={score}, packet={deep_review}, expected={expected_deep_review}"
        )

    return ResultPacket(
        source_mac=ethernet.src,
        sequence_number=sequence_number,
        score=score,
        classification=classification,
        deep_review=deep_review,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Decode FPGA Ethernet result packets with EtherType 0x88B5."
    )
    parser.add_argument(
        "--interface",
        help="Capture interface name. Use --list-interfaces to find it.",
    )
    parser.add_argument(
        "--list-interfaces",
        action="store_true",
        help="Print available capture interfaces and exit.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=0,
        help="Number of valid FPGA result packets to decode; 0 means run until Ctrl+C.",
    )
    parser.add_argument(
        "--routing-threshold",
        type=int,
        default=DEFAULT_ROUTING_THRESHOLD,
        help=f"Expected deep-review threshold; default: {DEFAULT_ROUTING_THRESHOLD}.",
    )
    args = parser.parse_args()

    if args.list_interfaces:
        show_interfaces()
        return

    if not args.interface:
        parser.error("--interface is required unless --list-interfaces is used.")

    if args.interface not in get_if_list():
        print(
            f"Warning: '{args.interface}' was not found in Scapy's interface list. "
            "Check the spelling from --list-interfaces."
        )

    capture_filter = (
        f"ether proto 0x{ETHERTYPE:04x} and ether src {EXPECTED_SOURCE_MAC}"
    )

    received_count = 0
    previous_sequence: int | None = None

    print(f"Listening on: {args.interface}")
    print(f"Capture filter: {capture_filter}")
    print("Press Ctrl+C to stop.\n")

    def process_packet(packet) -> None:
        nonlocal received_count, previous_sequence

        result = decode_result_packet(packet, args.routing_threshold)

        if result is None:
            return

        if previous_sequence is None:
            sequence_status = "first captured packet"
        elif result.sequence_number == previous_sequence + 1:
            sequence_status = "continuous"
        elif result.sequence_number == previous_sequence:
            sequence_status = "duplicate"
        else:
            sequence_status = (
                f"gap or reset; previous={previous_sequence}, "
                f"current={result.sequence_number}"
            )

        print(
            f"sequence={result.sequence_number:10d}  "
            f"score={result.score:6d}  "
            f"classification={result.classification_name:16s}  "
            f"routing={result.routing_name:12s}  "
            f"[{sequence_status}]"
        )

        previous_sequence = result.sequence_number
        received_count += 1

        if args.count and received_count >= args.count:
            raise KeyboardInterrupt

    try:
        sniff(
            iface=args.interface,
            filter=capture_filter,
            prn=process_packet,
            store=False,
        )
    except KeyboardInterrupt:
        print(f"\nStopped after decoding {received_count} valid FPGA result packet(s).")


if __name__ == "__main__":
    main()