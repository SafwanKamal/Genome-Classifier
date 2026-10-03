import queue
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ethernet_batch_stream_rate import run_window, summarize_timing, capture_delivery_delay, precise_wall_time


class WindowTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform=='win32', 'Windows timestamp conversion')
    def test_precise_filetime_conversion_preserves_fraction(self):
        ticks = 116444736000000000+123456789*10000000+1234560
        def fill(pointer):
            pointer._obj.dwHighDateTime=ticks>>32
            pointer._obj.dwLowDateTime=ticks & 0xffffffff
        with patch('ethernet_batch_stream_rate._precise_filetime',side_effect=fill):
            self.assertAlmostEqual(precise_wall_time(), 123456789.123456, places=6)

    def test_capture_timestamp_uses_wall_clock_and_keeps_negative_values(self):
        self.assertAlmostEqual(capture_delivery_delay(SimpleNamespace(time=1700000000.0), 1700000000.001), 0.001, places=6)
        self.assertLess(capture_delivery_delay(SimpleNamespace(time=1700000000.002), 1700000000.001), 0)
        for packet in (SimpleNamespace(), SimpleNamespace(time=None), SimpleNamespace(time=float('nan'))):
            self.assertIsNone(capture_delivery_delay(packet, 1700000000.001))

    def test_capture_summary_does_not_hide_invalid_clock_order(self):
        timing = {key: [] for key in ('send_call', 'queue_wait', 'send_to_reply',
                  'reply_to_dequeue', 'reply_to_refill', 'send_started', 'reply_arrived', 'callback_decode')}
        timing.update(capture_to_callback=[0.001,-0.0001], capture_timestamp=[10,10.002], capture_timestamp_unavailable=1)
        result = summarize_timing(timing, 0.01)
        self.assertEqual(result['capture_to_callback']['samples'], 2)
        self.assertEqual(result['capture_to_callback']['negative_samples'], 1)
        self.assertEqual(result['capture_to_callback']['timestamp_unavailable'], 1)
        self.assertAlmostEqual(result['capture_spacing']['median_us'], 2000)

    def run_batches(self, window=2, mode='normal', diagnostic=False):
        expected = [10, 11, 12, 13, 14]
        batches = [(0, b'a', 2), (2, b'b', 2), (4, b'c', 1)]
        replies = queue.Queue()
        sent = []
        first_read = []
        original_get = replies.get

        def get(**kwargs):
            if not first_read:
                first_read.append(len(sent))
            return original_get(**kwargs)

        replies.get = get

        class Socket:
            def send(self, request):
                begin, _, count = batches[len(sent)]
                sent.append(request)
                if mode == 'missing':
                    return
                scores = expected[begin:begin+count].copy()
                if mode == 'wrong':
                    scores[0] += 1
                if mode == 'short':
                    scores.pop()
                item = ((100+begin, scores), time.perf_counter(), None)
                if mode == 'reversed' and len(sent) == 1:
                    self.held = item
                else:
                    replies.put(item)
                    if mode == 'reversed' and len(sent) == 2:
                        replies.put(self.held)
                if mode == 'duplicate' and len(sent) == 1:
                    replies.put(item)

        timing = {key: [] for key in ('send_call', 'queue_wait', 'send_to_reply',
                  'reply_to_dequeue', 'reply_to_refill', 'send_started', 'reply_arrived', 'callback_decode')} if diagnostic else None
        observed_start = time.perf_counter()
        result = run_window(Socket(), batches, replies, expected, 100, window, 0.01, timing)
        if diagnostic:
            result['host_timing'] = summarize_timing(timing, time.perf_counter()-observed_start)
        return result, sent, first_read

    def test_diagnostic_samples_and_timeout(self):
        result, _, _ = self.run_batches(diagnostic=True)
        timing = result['host_timing']
        for key in ('send_call', 'queue_wait', 'send_to_reply', 'reply_to_dequeue'):
            self.assertEqual(timing[key]['samples'], 3)
            self.assertGreaterEqual(timing[key]['total_seconds'], 0)
        self.assertEqual(timing['send_spacing']['samples'], 2)
        self.assertEqual(timing['reply_to_refill']['samples'], 1)
        self.assertIsNone(timing['callback_decode']['median_us'])
        result, _, _ = self.run_batches(diagnostic=True, mode='missing')
        self.assertEqual(result['host_timing']['queue_wait']['samples'], 1)
        self.assertIsNone(result['completed_seconds'])

    def test_window_and_partial_final_batch(self):
        for window in (1, 2):
            with self.subTest(window=window):
                result, sent, first_read = self.run_batches(window)
                self.assertEqual(first_read, [window])
                self.assertEqual(sent, [b'a', b'b', b'c'])
                self.assertEqual(result['correct'], 5)
                self.assertGreater(result['completed_seconds'], 0)

    def test_reordered_replies_and_duplicates(self):
        for mode in ('reversed', 'duplicate'):
            with self.subTest(mode=mode):
                result, _, _ = self.run_batches(mode=mode)
                self.assertEqual(result['correct'], 5)
                self.assertEqual(result['duplicates'], int(mode == 'duplicate'))

    def test_missing_has_no_completion_rate(self):
        result, sent, _ = self.run_batches(mode='missing')
        self.assertEqual(len(sent), 2)
        self.assertEqual(result['received'], 0)
        self.assertIn('Timed out', result['error'])
        self.assertIsNone(result['completed_seconds'])

    def test_wrong_scores_and_short_reply(self):
        result, _, _ = self.run_batches(mode='wrong')
        self.assertEqual(result['received'], 5)
        self.assertEqual(result['correct'], 2)
        self.assertEqual(len(result['mismatches']), 3)
        self.assertIsNone(result['completed_seconds'])
        result, _, _ = self.run_batches(mode='short')
        self.assertIn('count differs', result['error'])
        self.assertIsNone(result['completed_seconds'])


if __name__ == '__main__':
    unittest.main()
