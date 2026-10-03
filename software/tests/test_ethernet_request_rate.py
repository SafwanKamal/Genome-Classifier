from pathlib import Path
import sys
import unittest
import json
import tempfile
from unittest.mock import Mock, patch, call

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ethernet_request_rate import summarize, send_frames, main
from decode_ethernet_result import ResultPacket


def reply(sequence, score, deep_review=None):
    return ResultPacket("02:00:00:00:00:01", sequence, score, int(score >= 0),
                        int(score >= -276) if deep_review is None else deep_review)


class RateReportTests(unittest.TestCase):
    def test_completion_time_uses_correct_replies_and_requires_all(self):
        for missing in (False, True):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'report.json'
                callbacks = {}
                sniffer = Mock(running=True)
                def create_sniffer(**kwargs):
                    callbacks.update(kwargs)
                    return sniffer
                def send(*args):
                    callbacks['prn'](reply(100, 216))
                    if not missing:
                        callbacks['prn'](reply(101, -484))
                with patch('sys.argv', ['rate', '--interface', 'mock', '--sender', 'raw',
                          '--count', '2', '--sequence', '100', '--report', str(output)]), \
                        patch('ethernet_request_rate.load_vectors', return_value=[(bytes(16), 216), (bytes(16), -484)]), \
                        patch('ethernet_request_rate.get_if_hwaddr', return_value='02:00:00:00:00:02'), \
                        patch('ethernet_request_rate.AsyncSniffer', side_effect=create_sniffer), \
                        patch('ethernet_request_rate.decode_result_packet', side_effect=lambda p, t: p), \
                        patch('ethernet_request_rate.send_frames', side_effect=send), \
                        patch('ethernet_request_rate.time.sleep'), \
                        patch('ethernet_request_rate.time.perf_counter', side_effect=[10, 10.1, 10.2, 10.3]):
                    main()
                report = json.loads(output.read_text())
                if missing:
                    self.assertIsNone(report['completed_variants_per_second'])
                else:
                    self.assertAlmostEqual(report['all_correct_completion_seconds'], 0.2)
                    self.assertAlmostEqual(report['completed_variants_per_second'], 10)

    def test_complete_response_set(self):
        result = summarize({100: 216, 101: -484, 102: -211},
                           [reply(100, 216), reply(101, -484), reply(102, -211)], -276)
        self.assertEqual(result["correct_unique"], 3)
        self.assertEqual(result["missing_sequences"], [])
        self.assertEqual(result["mismatches"], [])

    def test_duplicates_do_not_hide_loss(self):
        result = summarize({100: 216, 101: -484}, [reply(100, 216), reply(100, 216)], -276)
        self.assertEqual(result["received_unique"], 1)
        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(result["missing_sequences"], [101])

    def test_wrong_score_and_flags_are_rejected(self):
        result = summarize({100: -211, 101: -484},
                           [reply(100, -212), reply(101, -484, deep_review=1)], -276)
        self.assertEqual(result["received_unique"], 2)
        self.assertEqual(result["correct_unique"], 0)
        self.assertEqual(len(result["mismatches"]), 2)

    def test_unrelated_and_out_of_order_replies(self):
        result = summarize({100: 216, 101: -484},
                           [reply(99, 0), reply(101, -484), reply(100, 216)], -276)
        self.assertEqual(result["unrelated_replies"], 1)
        self.assertEqual(result["received_unique"], 2)
        self.assertEqual(result["out_of_order"], 1)


class RawSenderTests(unittest.TestCase):
    def test_bytes_order_and_no_zero_interval_sleep(self):
        socket = Mock()
        iface = Mock()
        iface.l2socket.return_value.return_value = socket
        frames = [b"first frame", b"second frame"]
        with patch("ethernet_request_rate.resolve_iface", return_value=iface), \
                patch("ethernet_request_rate.time.sleep") as sleep:
            send_frames(frames, "Ethernet", 0, "raw")
        self.assertEqual(socket.send.call_args_list, [call(frame) for frame in frames])
        socket.close.assert_called_once()
        sleep.assert_not_called()

    def test_requested_pacing_is_preserved(self):
        socket = Mock()
        iface = Mock()
        iface.l2socket.return_value.return_value = socket
        with patch("ethernet_request_rate.resolve_iface", return_value=iface), \
                patch("ethernet_request_rate.time.sleep") as sleep:
            send_frames([b"a", b"b"], "Ethernet", 0.001, "raw")
        self.assertEqual(sleep.call_args_list, [call(0.001), call(0.001)])

    def test_socket_closes_on_send_failure(self):
        socket = Mock()
        socket.send.side_effect = OSError("injection failed")
        iface = Mock()
        iface.l2socket.return_value.return_value = socket
        with patch("ethernet_request_rate.resolve_iface", return_value=iface):
            with self.assertRaises(OSError):
                send_frames([b"a"], "Ethernet", 0, "raw")
        socket.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
