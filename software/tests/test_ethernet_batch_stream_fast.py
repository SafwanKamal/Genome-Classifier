import json
import queue
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ethernet_batch_stream_rate_fast as fast
from ethernet_batch_protocol import BOARD_MAC


def reply(sequence=100, score=2, flags=b'\x01\x01'):
    return b'\xff'*6 + BOARD_MAC + b'\x88\xb5\x01\x02' + struct.pack('>IBB', sequence, 1, 0) + struct.pack('>i', score) + flags


class CaptureSocket:
    def __init__(self):
        self.frames = queue.Queue()
        self.closed = False
        self.raw_calls = 0

    def select(self, sockets, timeout):
        if self.frames.empty():
            time.sleep(0.001)
            return []
        return sockets

    def recv_raw(self):
        self.raw_calls += 1
        return self.frames.get_nowait()

    def recv(self):
        raise AssertionError('Packet dissection must not run')

    def close(self):
        self.closed = True


class FastTests(unittest.TestCase):
    def test_raw_capture_and_timestamp_without_packet_conversion(self):
        class RawBytes(bytes):
            def __bytes__(self):
                raise AssertionError('Packet reconstruction must not run')
        sock, replies = CaptureSocket(), queue.Queue()
        timing = {key: [] for key in ('callback_decode', 'capture_to_callback', 'capture_timestamp')}
        timing['capture_timestamp_unavailable'] = 0
        sock.frames.put((object, RawBytes(reply()), 123.0))
        capture = fast.RawCapture(sock, replies, 0, timing)
        with patch.object(fast, 'precise_wall_time', return_value=123.0001):
            capture.start()
            self.assertTrue(capture.ready.wait(1))
            decoded, arrived, error = replies.get(timeout=1)
            capture.stop()
        self.assertEqual(decoded, (100, [2]))
        self.assertIsNone(error)
        self.assertGreater(arrived, 0)
        self.assertAlmostEqual(timing['capture_to_callback'][0], 0.0001)
        self.assertEqual(timing['capture_timestamp'], [123.0])
        self.assertFalse(capture.thread.is_alive())

    def test_bad_flags_propagate_capture_error(self):
        sock, replies = CaptureSocket(), queue.Queue()
        sock.frames.put((object, reply(flags=b'\x00\x01'), None))
        capture = fast.RawCapture(sock, replies, 0)
        capture.start()
        decoded, _, error = replies.get(timeout=1)
        capture.stop()
        self.assertIsNone(decoded)
        self.assertIn('flags disagree', error)

    def test_missing_timestamp_and_idle_shutdown(self):
        sock, replies = CaptureSocket(), queue.Queue()
        timing = {key: [] for key in ('callback_decode', 'capture_to_callback', 'capture_timestamp')}
        timing['capture_timestamp_unavailable'] = 0
        sock.frames.put((object, reply(), None))
        capture = fast.RawCapture(sock, replies, 0, timing)
        capture.start()
        replies.get(timeout=1)
        capture.stop()
        self.assertEqual(timing['capture_timestamp_unavailable'], 1)
        self.assertFalse(capture.thread.is_alive())

    def test_refill_before_golden_check_and_wrong_score_has_no_rate(self):
        for wrong in (False, True):
            with self.subTest(wrong=wrong):
                replies, sent = queue.Queue(), []
                class Expected:
                    def __len__(self): return 2
                    def __getitem__(self, index):
                        # The replacement request must already be sent.
                        if index == 0:
                            self.assert_sent()
                        return 2
                    def assert_sent(self):
                        if sent != [b'a', b'b']:
                            raise AssertionError('Golden checking delayed refill')
                class Sender:
                    def send(self, frame):
                        begin = len(sent)
                        sent.append(frame)
                        replies.put(((100+begin, [3 if wrong else 2]), time.perf_counter(), None))
                result = fast.run_window(Sender(), [(0,b'a',1),(1,b'b',1)], replies,
                                         Expected(), 100, 1, 0.01)
                self.assertEqual(result['received'], 2)
                self.assertEqual(result['correct'], 0 if wrong else 2)
                self.assertEqual(result['completed_seconds'] is None, wrong)

    def test_invalid_count_and_capture_error_do_not_refill(self):
        for item in (((100, []), time.perf_counter(), None),
                     (None, time.perf_counter(), 'capture failed')):
            replies, sent = queue.Queue(), []
            class Sender:
                def send(self, frame):
                    sent.append(frame)
                    replies.put(item)
            result = fast.run_window(Sender(), [(0,b'a',1),(1,b'b',1)], replies,
                                     [2,2], 100, 1, 0.01)
            self.assertEqual(sent, [b'a'])
            self.assertIsNotNone(result['error'])
            self.assertIsNone(result['completed_seconds'])

    def test_four_initial_credits_then_refill_before_golden_check(self):
        replies, sent = queue.Queue(), []
        class Expected:
            def __len__(self): return 5
            def __getitem__(self, index):
                if index == 0 and len(sent) != 5:
                    raise AssertionError('Four-credit replacement delayed by golden check')
                return 2
        class Sender:
            def send(self, frame):
                begin = len(sent)
                sent.append(frame)
                replies.put(((100+begin, [2]), time.perf_counter(), None))
        original_get = replies.get
        first_get = []
        def get(**kwargs):
            if not first_get:
                first_get.append(len(sent))
            return original_get(**kwargs)
        replies.get = get
        batches = [(i, bytes([i]), 1) for i in range(5)]
        result = fast.run_window(Sender(), batches, replies, Expected(), 100, 4, 0.01)
        self.assertEqual(first_get, [4])
        self.assertEqual(len(sent), 5)
        self.assertEqual(result['correct'], 5)
        self.assertGreater(result['completed_seconds'], 0)

    def test_cli_rejects_eight_credits_before_opening_interface(self):
        with patch.object(sys, 'argv', ['fast','--interface','Ethernet','--window','8',
                                       '--report','unused.json']), \
             patch.object(fast, 'resolve_iface') as resolve, patch('sys.stderr'):
            with self.assertRaises(SystemExit) as raised:
                fast.main()
        self.assertEqual(raised.exception.code, 2)
        resolve.assert_not_called()

    def test_duplicates_unrelated_and_timeout_do_not_grant_credits(self):
        replies, sent = queue.Queue(), []
        class Sender:
            def send(self, frame):
                sent.append(frame)
                if len(sent) == 1:
                    replies.put(((999, [2]), time.perf_counter(), None))
                    replies.put(((100, [2]), time.perf_counter(), None))
                    replies.put(((100, [2]), time.perf_counter(), None))
        result = fast.run_window(Sender(), [(0,b'a',1),(1,b'b',1),(2,b'c',1)],
                                 replies, [2,2,2], 100, 1, 0.01)
        self.assertEqual(sent, [b'a',b'b'])
        self.assertEqual(result['duplicates'], 1)
        self.assertEqual(result['unrelated_replies'], 1)
        self.assertIn('Timed out', result['error'])
        self.assertIsNone(result['completed_seconds'])

    def test_main_uses_filter_closes_sockets_and_records_identity(self):
        for malformed in (False, True):
            with self.subTest(malformed=malformed), tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                manifest, input_file, report = (folder/name for name in ('model.json','data.parquet','report.json'))
                manifest.write_text('{}')
                input_file.write_bytes(b'input identity')
                capture = CaptureSocket()
                calls = []
                class Sender:
                    closed = False
                    def send(self, frame):
                        capture.frames.put((object, reply(100, flags=b'\x00\x01' if malformed else b'\x01\x01'), 123.0))
                    def close(self): self.closed = True
                sender = Sender()
                def open_capture(**kwargs):
                    calls.append(kwargs)
                    return capture
                iface = SimpleNamespace(name='NIC', description='test adapter', network_name='adapter id',
                                        l2socket=lambda: lambda **kwargs: sender,
                                        l2listen=lambda: open_capture)
                model = SimpleNamespace(manifest={'feature_order':['f'], 'routing_threshold':0},
                                        score=lambda features: np.array([2]))
                # One protocol feature vector still needs sixteen bytes.
                model.manifest['feature_order'] = [f'f{i}' for i in range(16)]
                frame = pd.DataFrame({'split':['test'], **{f'f{i}':[0] for i in range(16)}})
                argv = ['fast','--interface','Ethernet','--count','1','--sequence','100',
                        '--manifest',str(manifest),'--input',str(input_file),'--report',str(report),
                        '--design-label','200MHz']
                with patch.object(sys,'argv',argv), patch.object(fast,'BatchModelReference',return_value=model), \
                     patch.object(fast.pd,'read_parquet',return_value=frame), \
                     patch.object(fast,'get_if_hwaddr',return_value='02:00:00:00:00:02'), \
                     patch.object(fast,'resolve_iface',return_value=iface), patch('builtins.print'):
                    with self.assertRaises(SystemExit) as raised:
                        fast.main()
                self.assertEqual(raised.exception.code, int(malformed))
                self.assertTrue(sender.closed)
                self.assertTrue(capture.closed)
                self.assertEqual(calls[0]['filter'], 'ether proto 0x88b5 and ether src 02:00:00:00:00:01')
                result = json.loads(report.read_text())
                self.assertEqual(result['identity']['design_label'], '200MHz')
                self.assertEqual(len(result['identity']['manifest_sha256']), 64)
                self.assertEqual(result['correct'], 0 if malformed else 1)
                if malformed:
                    self.assertIsNone(result['completed_variants_per_second'])


if __name__ == '__main__': unittest.main()
