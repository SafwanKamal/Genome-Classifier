from __future__ import annotations

import queue
import threading
import time

from scapy.all import AsyncSniffer, get_if_hwaddr
from scapy.interfaces import resolve_iface

from software.decode_ethernet_result import (
    DEFAULT_ROUTING_THRESHOLD, ETHERTYPE, EXPECTED_SOURCE_MAC,
    ResultPacket, decode_result_packet,
)
from software.send_ethernet_RX_test import build_frame
from software.variantgate.backends.base import InferenceBackend
from software.variantgate.schemas import ScoreResult, VariantRecord


class EthernetBackend(InferenceBackend):
    """One outstanding RX01 request, with a persistent raw socket and capture."""

    def __init__(self, interface: str, timeout: float = 2.0,
                 sequence: int = 2_000_000,
                 routing_threshold: int = DEFAULT_ROUTING_THRESHOLD) -> None:
        if not interface:
            raise ValueError("An Ethernet interface is required")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if not 0 <= sequence <= 0xFFFFFFFF:
            raise ValueError("sequence must fit in 32 bits")
        self._interface = interface
        self._timeout = timeout
        self._sequence = sequence
        self._routing_threshold = routing_threshold
        self._socket = None
        self._sniffer = None
        self._source_mac = ""
        self._replies: queue.Queue[ResultPacket] = queue.Queue()

    @property
    def name(self) -> str:
        return "fpga_ethernet"

    def _receive(self, packet) -> None:
        result = decode_result_packet(packet, self._routing_threshold)
        if result is not None:
            self._replies.put(result)

    def open(self) -> None:
        if self._socket is not None:
            return
        self._source_mac = get_if_hwaddr(self._interface)
        iface = resolve_iface(self._interface)
        self._socket = iface.l2socket()(iface=iface)
        ready = threading.Event()
        self._sniffer = AsyncSniffer(
            iface=self._interface,
            filter=f"ether proto 0x{ETHERTYPE:04x} and ether src {EXPECTED_SOURCE_MAC}",
            prn=self._receive, store=False, started_callback=ready.set,
        )
        try:
            self._sniffer.start()
            if not ready.wait(self._timeout):
                raise TimeoutError("Ethernet capture did not become ready")
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        try:
            if self._sniffer is not None and self._sniffer.running:
                self._sniffer.stop()
        finally:
            self._sniffer = None
            if self._socket is not None:
                self._socket.close()
                self._socket = None

    def score_one(self, record: VariantRecord) -> ScoreResult:
        if self._socket is None:
            raise RuntimeError("Ethernet backend is not open")
        sequence = self._sequence
        self._sequence = (sequence + 1) & 0xFFFFFFFF
        request = build_frame(self._source_mac, sequence,
                              bytes(value & 0xFF for value in record.features))
        start_ns = time.perf_counter_ns()
        deadline = time.monotonic() + self._timeout
        self._socket.send(request)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"No Ethernet result for sequence {sequence}")
            try:
                result = self._replies.get(timeout=remaining)
            except queue.Empty as error:
                raise TimeoutError(f"No Ethernet result for sequence {sequence}") from error
            if result.sequence_number == sequence:
                break
        if (result.classification, result.deep_review) != (
                int(result.score >= 0), int(result.score >= self._routing_threshold)):
            raise ValueError(f"Ethernet result flags disagree with score for sequence {sequence}")
        return ScoreResult(
            variant_key=record.variant_key, gene=record.gene,
            score=result.score, prediction=result.classification,
            backend=self.name, latency_ns=time.perf_counter_ns() - start_ns,
        )
