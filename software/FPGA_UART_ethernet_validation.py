from __future__ import annotations

import argparse
import queue
import time
from pathlib import Path

import serial
from scapy.all import AsyncSniffer

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
VECTOR_BYTES = FEATURE_NUMBER + 4


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


def load_vectors(
    vector_path: Path,
    count: int,
) -> list[tuple[bytes, int]]:
    vectors: list[tuple[bytes, int]] = []

    for line_number, line in enumerate(
        vector_path.read_text(
            encoding="utf-8"
        ).splitlines(),
        start=1,
    ):
        line = line.strip()

        if not line:
            continue

        try:
            vector = bytes.fromhex(line)
        except ValueError as error:
            raise ValueError(
                f"Invalid hexadecimal vector on line "
                f"{line_number}"
            ) from error

        if len(vector) != VECTOR_BYTES:
            raise ValueError(
                f"Vector line {line_number} contains "
                f"{len(vector)} bytes; expected {VECTOR_BYTES}"
            )

        features = vector[:FEATURE_NUMBER]
        expected_score = int.from_bytes(
            vector[FEATURE_NUMBER:],
            byteorder="big",
            signed=True,
        )

        vectors.append((features, expected_score))

        if len(vectors) == count:
            break

    if len(vectors) != count:
        raise ValueError(
            f"Requested {count} vectors, but "
            f"{vector_path} contains only {len(vectors)}"
        )

    return vectors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Drive saved V2 vectors through FPGA UART and "
            "verify the matching raw-Ethernet result packets."
        )
    )

    parser.add_argument(
        "--port",
        required=True,
        help="FPGA USB-UART port, for example COM4.",
    )

    parser.add_argument(
        "--interface",
        required=True,
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

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.count <= 0:
        raise ValueError("--count must be greater than zero")

    vectors = load_vectors(
        args.vectors,
        args.count,
    )

    result_queue: queue.Queue[ResultPacket] = queue.Queue()

    def capture_packet(packet) -> None:
        result = decode_result_packet(
            packet,
            args.routing_threshold,
        )

        if result is not None:
            result_queue.put(result)

    capture_filter = (
        f"ether proto 0x{ETHERTYPE:04x} "
        f"and ether src {EXPECTED_SOURCE_MAC}"
    )

    sniffer = AsyncSniffer(
        iface=args.interface,
        filter=capture_filter,
        prn=capture_packet,
        store=False,
    )

    previous_sequence: int | None = None
    passed = 0

    print(f"UART port: {args.port}")
    print(f"Ethernet interface: {args.interface}")
    print(f"Vectors: {args.vectors}")
    print(f"Tests: {args.count}")

    sniffer.start()

    try:
        # Give Npcap time to install the capture filter.
        time.sleep(0.5)

        with serial.Serial(
            port=args.port,
            baudrate=args.baud_rate,
            timeout=0.05,
        ) as serial_port:
            time.sleep(0.25)
            serial_port.reset_input_buffer()

            for test_index, (
                features,
                expected_score,
            ) in enumerate(vectors, start=1):
                request = build_request(features)

                serial_port.reset_input_buffer()
                serial_port.write(request)
                serial_port.flush()

                UART_score, _ = read_UART_score(
                    serial_port,
                    args.timeout,
                )

                try:
                    ethernet_result = result_queue.get(
                        timeout=args.timeout
                    )
                except queue.Empty as error:
                    raise TimeoutError(
                        f"Test {test_index}: no matching "
                        "Ethernet result was captured"
                    ) from error

                if UART_score != expected_score:
                    raise RuntimeError(
                        f"Test {test_index}: UART score "
                        f"{UART_score} does not match expected "
                        f"score {expected_score}"
                    )

                if ethernet_result.score != expected_score:
                    raise RuntimeError(
                        f"Test {test_index}: Ethernet score "
                        f"{ethernet_result.score} does not match "
                        f"expected score {expected_score}"
                    )

                expected_classification = int(
                    expected_score >= 0
                )
                expected_deep_review = int(
                    expected_score >= args.routing_threshold
                )

                if (
                    ethernet_result.classification
                    != expected_classification
                ):
                    raise RuntimeError(
                        f"Test {test_index}: incorrect "
                        "Ethernet classification"
                    )

                if (
                    ethernet_result.deep_review
                    != expected_deep_review
                ):
                    raise RuntimeError(
                        f"Test {test_index}: incorrect "
                        "Ethernet routing decision"
                    )

                if previous_sequence is not None:
                    expected_sequence = (
                        previous_sequence + 1
                    ) & 0xFFFFFFFF

                    if (
                        ethernet_result.sequence_number
                        != expected_sequence
                    ):
                        raise RuntimeError(
                            f"Test {test_index}: expected "
                            f"Ethernet sequence {expected_sequence}, "
                            f"received "
                            f"{ethernet_result.sequence_number}"
                        )

                previous_sequence = (
                    ethernet_result.sequence_number
                )
                passed += 1

                print(
                    f"{test_index:3d}: "
                    f"sequence="
                    f"{ethernet_result.sequence_number:10d}  "
                    f"expected={expected_score:6d}  "
                    f"UART={UART_score:6d}  "
                    f"Ethernet={ethernet_result.score:6d}  "
                    "PASS"
                )
    finally:
        sniffer.stop()

    print(
        f"\nPASS: {passed}/{args.count} UART and Ethernet "
        "results matched the committed V2 vectors"
    )


if __name__ == "__main__":
    main()
