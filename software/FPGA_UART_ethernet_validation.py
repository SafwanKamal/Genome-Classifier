from __future__ import annotations

import argparse
import queue
import time
from pathlib import Path

import serial
from scapy.all import AsyncSniffer, Ether, get_if_hwaddr, sendp
from send_ethernet_RX_test import build_frame
from model_vectors import load_vectors

from decode_ethernet_result import (
    DEFAULT_ROUTING_THRESHOLD,
    ETHERTYPE,
    EXPECTED_SOURCE_MAC,
    ResultPacket,
    decode_result_packet,
)


REQUEST_HEADER = 0x5A
RESPONSE_HEADER = 0xA5
FEATURE_NUMBER = 16


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF

    for byte in data:
        crc ^= byte << 8

        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF

    return crc


def build_request(features: bytes) -> bytes:
    if len(features) != FEATURE_NUMBER:
        raise ValueError(
            f"Expected {FEATURE_NUMBER} feature bytes, "
            f"received {len(features)}"
        )

    packet_without_crc = bytes([
        REQUEST_HEADER,
        FEATURE_NUMBER,
    ]) + features

    crc = crc16_ccitt(packet_without_crc)

    return packet_without_crc + crc.to_bytes(
        2,
        byteorder="big",
    )


def read_exact(
    serial_port: serial.Serial,
    byte_count: int,
    timeout: float,
) -> bytes:
    received = bytearray()
    deadline = time.monotonic() + timeout

    while len(received) < byte_count:
        if time.monotonic() >= deadline:
            break

        chunk = serial_port.read(
            byte_count - len(received)
        )

        if chunk:
            received.extend(chunk)

    return bytes(received)


def read_UART_score(
    serial_port: serial.Serial,
    timeout: float,
) -> tuple[int, bytes]:
    response = read_exact(
        serial_port,
        byte_count=5,
        timeout=timeout,
    )

    if len(response) != 5:
        raise TimeoutError(
            "Expected 5 UART response bytes, "
            f"received {len(response)}"
        )

    if response[0] != RESPONSE_HEADER:
        raise ValueError(
            f"Expected UART response header "
            f"{RESPONSE_HEADER:02X}, "
            f"received {response[0]:02X}"
        )

    score = int.from_bytes(
        response[1:5],
        byteorder="big",
        signed=True,
    )

    return score, response


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Drive saved V2 vectors through UART and/or Ethernet requests "
            "and verify both response scores against golden values."
        )
    )

    parser.add_argument(
        "--port",
        help="FPGA USB-UART port, for example COM4.",
    )

    parser.add_argument(
        "--interface",
        help="Scapy/Npcap Ethernet capture interface name.",
    )

    parser.add_argument(
        "--vectors",
        type=Path,
        default=Path(
            "simulation/model_v2_h8_core_vectors.mem"
        ),
    )

    parser.add_argument(
        "--count",
        type=int,
        default=10,
    )

    parser.add_argument(
        "--baud-rate",
        type=int,
        default=115_200,
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=2.0,
    )

    parser.add_argument(
        "--routing-threshold",
        type=int,
        default=DEFAULT_ROUTING_THRESHOLD,
    )

    parser.add_argument("--request-path", choices=("uart", "ethernet", "both"),
                        default="uart", help="Request transport; both compares the same vectors through both inputs.")
    parser.add_argument("--sequence", type=int, default=1000,
                        help="First Ethernet request sequence number.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview feature vectors, golden scores, and request bytes without hardware access.")
    args = parser.parse_args()
    if not args.dry_run and (not args.port or not args.interface):
        parser.error("--port and --interface are required for a physical test")
    if not 0 <= args.sequence <= 0xFFFFFFFF or args.sequence + args.count - 1 > 0xFFFFFFFF:
        parser.error("Ethernet request sequences must fit in 32 bits")
    return args


def main() -> None:
    args = parse_args()
    if args.count <= 0 or args.timeout <= 0:
        raise ValueError("count and timeout must be positive")
    vectors = load_vectors(args.vectors, args.count)
    paths = ("uart", "ethernet") if args.request_path == "both" else (args.request_path,)

    if args.dry_run:
        for index, (features, score) in enumerate(vectors):
            frame = build_frame("02:00:00:00:00:02", args.sequence + index, features)
            print(f"{index + 1:3d}: features={list(int.from_bytes(bytes([b]), signed=True) for b in features)} "
                  f"expected={score} classification={int(score >= 0)} "
                  f"deep_review={int(score >= args.routing_threshold)}")
            print(f"     UART={build_request(features).hex(' ')}")
            print(f"     Ethernet={frame.hex(' ')}")
        return

    result_queue: queue.Queue[ResultPacket] = queue.Queue()
    def capture_packet(packet) -> None:
        result = decode_result_packet(packet, args.routing_threshold)
        if result is not None:
            result_queue.put(result)

    sniffer = AsyncSniffer(
        iface=args.interface,
        filter=f"ether proto 0x{ETHERTYPE:04x} and ether src {EXPECTED_SOURCE_MAC}",
        prn=capture_packet, store=False,
    )
    source_mac = get_if_hwaddr(args.interface) if "ethernet" in paths else None
    previous_uart_sequence = None
    passed = 0
    print(f"UART port: {args.port}; Ethernet interface: {args.interface}")
    print(f"Vectors: {args.vectors}; tests: {args.count}; request paths: {', '.join(paths)}")
    sniffer.start()
    try:
        time.sleep(0.5)  # Allow Npcap to install the capture filter.
        with serial.Serial(args.port, args.baud_rate, timeout=0.05) as serial_port:
            time.sleep(0.25)
            serial_port.reset_input_buffer()
            for index, (features, expected_score) in enumerate(vectors):
                for path in paths:
                    serial_port.reset_input_buffer()
                    if path == "uart":
                        serial_port.write(build_request(features))
                        serial_port.flush()
                    else:
                        frame = build_frame(source_mac, args.sequence + index, features)
                        sendp(Ether(frame), iface=args.interface, verbose=False)

                    uart_score, _ = read_UART_score(serial_port, args.timeout)
                    try:
                        result = result_queue.get(timeout=args.timeout)
                    except queue.Empty as error:
                        raise TimeoutError(f"Vector {index + 1}, {path}: no Ethernet response") from error
                    if uart_score != expected_score or result.score != expected_score:
                        raise RuntimeError(f"Vector {index + 1}, {path}: expected={expected_score}, "
                                           f"UART={uart_score}, Ethernet={result.score}")
                    if result.classification != int(expected_score >= 0) or \
                            result.deep_review != int(expected_score >= args.routing_threshold):
                        raise RuntimeError(f"Vector {index + 1}, {path}: incorrect result flags")
                    if path == "ethernet":
                        if result.sequence_number != args.sequence + index:
                            raise RuntimeError(f"Vector {index + 1}: Ethernet request sequence was not echoed")
                    else:
                        if previous_uart_sequence is not None and \
                                result.sequence_number != (previous_uart_sequence + 1) & 0xFFFFFFFF:
                            raise RuntimeError(f"Vector {index + 1}: non-continuous UART result sequence")
                        previous_uart_sequence = result.sequence_number
                    passed += 1
                    print(f"{index + 1:3d} {path:8s}: sequence={result.sequence_number:10d} "
                          f"expected={expected_score:6d} UART={uart_score:6d} "
                          f"Ethernet={result.score:6d} PASS", flush=True)
    finally:
        if sniffer.running:
            sniffer.stop()
    print(f"\nPASS: {passed}/{args.count * len(paths)} requests matched the committed V2 vectors")


if __name__ == "__main__":
    main()
