from __future__ import annotations

import time

import serial

from software.variantgate.backends.base import InferenceBackend
from software.variantgate.schemas import FEATURE_NUMBER, ScoreResult, VariantRecord


REQUEST_HEADER = 0x5A
RESPONSE_HEADER = 0xA5


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


def build_request(record: VariantRecord) -> bytes:
    payload = bytes(value & 0xFF for value in record.features)
    packet_without_crc = bytes([
        REQUEST_HEADER,
        FEATURE_NUMBER,
    ]) + payload
    crc = crc16_ccitt(packet_without_crc)

    return packet_without_crc + crc.to_bytes(2, byteorder="big")


class UARTBackend(InferenceBackend):
    def __init__(
        self,
        port: str,
        baud_rate: int = 115_200,
        timeout: float = 2.0,
    ) -> None:
        if not port:
            raise ValueError("A serial port is required")

        if baud_rate <= 0:
            raise ValueError("baud_rate must be positive")

        if timeout <= 0:
            raise ValueError("timeout must be positive")

        self._port = port
        self._baud_rate = baud_rate
        self._timeout = timeout
        self._serial: serial.Serial | None = None

    @property
    def name(self) -> str:
        return "fpga_uart"

    @property
    def port(self) -> str:
        return self._port

    @property
    def baud_rate(self) -> int:
        return self._baud_rate

    def open(self) -> None:
        if self._serial is not None:
            return

        self._serial = serial.Serial(
            port=self._port,
            baudrate=self._baud_rate,
            timeout=min(self._timeout, 0.05),
        )

        time.sleep(0.25)
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()

    def close(self) -> None:
        if self._serial is None:
            return

        self._serial.close()
        self._serial = None

    def _require_open(self) -> serial.Serial:
        if self._serial is None:
            raise RuntimeError("UART backend is not open")

        return self._serial

    def _read_exact(self, byte_count: int) -> bytes:
        serial_port = self._require_open()
        received = bytearray()
        deadline = time.monotonic() + self._timeout

        while len(received) < byte_count:
            if time.monotonic() >= deadline:
                break

            chunk = serial_port.read(byte_count - len(received))

            if chunk:
                received.extend(chunk)

        if len(received) != byte_count:
            raise TimeoutError(
                f"Expected {byte_count} response bytes, "
                f"received {len(received)}"
            )

        return bytes(received)

    def _read_score(self) -> int:
        response = self._read_exact(5)

        if response[0] != RESPONSE_HEADER:
            raise ValueError(
                "Invalid FPGA response header: "
                f"expected {RESPONSE_HEADER:02X}, "
                f"received {response[0]:02X}; "
                f"response={response.hex(' ').upper()}"
            )

        return int.from_bytes(
            response[1:5],
            byteorder="big",
            signed=True,
        )

    def score_one(self, record: VariantRecord) -> ScoreResult:
        serial_port = self._require_open()
        request = build_request(record)
        serial_port.reset_input_buffer()

        start_ns = time.perf_counter_ns()
        serial_port.write(request)
        serial_port.flush()
        score = self._read_score()
        end_ns = time.perf_counter_ns()

        return ScoreResult(
            variant_key=record.variant_key,
            gene=record.gene,
            score=score,
            prediction=int(score >= 0),
            backend=self.name,
            latency_ns=end_ns - start_ns,
        )
