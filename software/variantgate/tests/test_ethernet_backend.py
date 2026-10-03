from pathlib import Path
import tempfile
import json
import unittest
from unittest.mock import Mock, patch

import pandas as pd
from scapy.all import Ether, Raw

from software.variantgate.backends.ethernet_backend import EthernetBackend
from software.variantgate.backends.numpy_backend import NumPyBackend
from software.variantgate.cli import build_parser, run_triage
from software.variantgate.manifest import load_model_manifest
from software.variantgate.schemas import VariantRecord

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "reports/checkpoint_v2/export_manifest.json"
POLICY = ROOT / "reports/checkpoint_v2/routing_policy.json"


def reply(sequence, score, classification=None, threshold=-276):
    payload = (b"\x01\x01" + sequence.to_bytes(4, "big") +
               score.to_bytes(4, "big", signed=True) +
               bytes([int(score >= 0) if classification is None else classification,
                      int(score >= threshold)]) + bytes(4))
    return Ether(src="02:00:00:00:00:01", type=0x88b5) / Raw(payload)


class FakeSniffer:
    def __init__(self, **kwargs):
        self.callback = kwargs["prn"]
        self.ready = kwargs["started_callback"]
        self.running = False

    def start(self):
        self.running = True
        self.ready()

    def stop(self):
        self.running = False


class EthernetBackendTests(unittest.TestCase):
    def setUp(self):
        self.socket = Mock()
        iface = Mock()
        iface.l2socket.return_value.return_value = self.socket
        self.sniffers = []

        def capture(**kwargs):
            sniffer = FakeSniffer(**kwargs)
            self.sniffers.append(sniffer)
            return sniffer

        for name, options in [
            ("get_if_hwaddr", {"return_value": "02:00:00:00:00:02"}),
            ("resolve_iface", {"return_value": iface}),
            ("AsyncSniffer", {"side_effect": capture}),
        ]:
            patcher = patch("software.variantgate.backends.ethernet_backend." + name, **options)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.record = VariantRecord("test-variant", "GENE", tuple(range(-8, 8)))

    def test_sequence_matching_signed_features_and_persistent_socket(self):
        def respond(frame):
            sequence = int.from_bytes(frame[18:22], "big")
            self.sniffers[0].callback(reply(sequence - 1, 99))
            self.sniffers[0].callback(reply(sequence, -124))

        self.socket.send.side_effect = respond
        with EthernetBackend("Ethernet", sequence=200) as backend:
            results = [backend.score_one(self.record), backend.score_one(self.record)]
        frames = [call.args[0] for call in self.socket.send.call_args_list]
        self.assertEqual([int.from_bytes(f[18:22], "big") for f in frames], [200, 201])
        self.assertEqual(frames[0][22:38], bytes(v & 255 for v in self.record.features))
        self.assertEqual([r.score for r in results], [-124, -124])
        self.assertEqual(results[0].variant_key, self.record.variant_key)
        self.assertEqual(results[0].prediction, 0)
        self.assertEqual(len(self.sniffers), 1)
        self.assertFalse(self.sniffers[0].running)
        self.socket.close.assert_called_once()

    def test_missing_reply_times_out_and_closes(self):
        with self.assertRaisesRegex(TimeoutError, "sequence 300"):
            with EthernetBackend("Ethernet", timeout=0.01, sequence=300) as backend:
                backend.score_one(self.record)
        self.socket.close.assert_called_once()

    def test_selected_model_routing_threshold(self):
        self.socket.send.side_effect = lambda f: self.sniffers[0].callback(
            reply(int.from_bytes(f[18:22], "big"), -1000, threshold=-1914))
        with EthernetBackend("Ethernet", routing_threshold=-1914) as backend:
            self.assertEqual(backend.score_one(self.record).score, -1000)

    def test_invalid_flags_fail(self):
        self.socket.send.side_effect = lambda f: self.sniffers[0].callback(
            reply(int.from_bytes(f[18:22], "big"), -124, classification=1))
        with self.assertRaisesRegex(ValueError, "flags disagree"):
            with EthernetBackend("Ethernet") as backend:
                backend.score_one(self.record)

    def test_triage_comparison_routing_and_manifest(self):
        manifest = load_model_manifest(MANIFEST)
        numpy = NumPyBackend(manifest)

        def respond(frame):
            features = tuple(v if v < 128 else v - 256 for v in frame[22:38])
            score = numpy.score_one(VariantRecord("mock", None, features)).score
            self.sniffers[0].callback(reply(int.from_bytes(frame[18:22], "big"), score))

        self.socket.send.side_effect = respond
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "input.parquet"
            out = Path(directory) / "triage"
            rows = []
            for i, features in enumerate([tuple(range(-8, 8)), (127,) * 16, (-127,) * 16]):
                rows.append({"variant_key": f"variant-{i}", "gene": "GENE", "split": "test",
                             **dict(zip(manifest.feature_order, features))})
            pd.DataFrame(rows).to_parquet(data, index=False)
            args = build_parser().parse_args([
                "triage", "--input", str(data), "--manifest", str(MANIFEST),
                "--backend", "compare-ethernet", "--interface", "Ethernet",
                "--routing-policy", str(POLICY), "--split", "test", "--output-dir", str(out),
            ])
            run_triage(args)
            summary = json.loads((out / "summary.json").read_text())
            run = json.loads((out / "run_manifest.json").read_text())
            scores = pd.read_parquet(out / "scores.parquet")
            self.assertTrue(summary["bit_exact_comparison"])
            self.assertEqual(summary["bit_exact_mismatches"], 0)
            self.assertEqual(summary["variant_number"], 3)
            self.assertTrue(scores["score"].eq(scores["reference_score"]).all())
            self.assertTrue(scores["deep_review_required"].eq(scores["score"] >= -276).all())
            self.assertEqual(run["backend"]["ethernet_interface"], "Ethernet")
            self.assertEqual(run["backend"]["first_sequence"], 2000000)
            self.assertIsNone(run["backend"]["serial_port"])
            self.assertIsNone(run["backend"]["baud_rate"])
            self.assertEqual(run["backend"]["timeout_seconds"], 2)


if __name__ == "__main__":
    unittest.main()
