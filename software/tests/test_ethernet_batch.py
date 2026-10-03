import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ethernet_batch_protocol import build_batch, decode_batch, BOARD_MAC
from ethernet_batch_rate import main


def response(sequence, scores, threshold=-1833):
    return (bytes([255])*6 + BOARD_MAC + b'\x88\xb5\x01\x02' +
            struct.pack('>IBB', sequence, len(scores), 0) +
            b''.join(struct.pack('>iBB', score, int(score>=0), int(score>=threshold)) for score in scores))


class BatchProtocolTests(unittest.TestCase):
    def test_frame_sizes_sequence_and_features(self):
        for count in (1, 32):
            features = [bytes((i+j)&255 for j in range(16)) for i in range(count)]
            frame = build_batch('02:00:00:00:00:02', 123, features)
            self.assertEqual(len(frame), max(60, 24+count*16))
            self.assertEqual(frame[14:24], b'RB01'+struct.pack('>IBB',123,count,0))
            self.assertEqual(frame[24:24+count*16], b''.join(features))

    def test_signed_scores_and_flags(self):
        scores = [-2147483648, -1834, -1833, -1, 0, 2147483647]
        self.assertEqual(decode_batch(response(100, scores), -1833), (100, scores))

    def test_bad_results_and_unrelated_frames(self):
        frame = response(100, [-1000])
        self.assertIsNone(decode_batch(bytes(60), -1833))
        with self.assertRaises(ValueError):
            decode_batch(frame[:-1], -1833)
        with self.assertRaises(ValueError):
            decode_batch(frame[:-1]+b'\x00', -1833)

    def test_invalid_request_dimensions_and_sequence(self):
        for rows, sequence in (([],0), ([bytes(16)]*33,0), ([bytes(15)],0), ([bytes(16)]*2,0xffffffff)):
            with self.assertRaises(ValueError):
                build_batch('02:00:00:00:00:02',sequence,rows)

    def test_host_partial_batch_and_timeout_cleanup(self):
        for timeout in (False, True):
            with self.subTest(timeout=timeout), tempfile.TemporaryDirectory() as directory:
                report = Path(directory)/'report.json'
                names = [f'f{i}' for i in range(16)]
                data = pd.DataFrame([{'split':'test', **{name:r for name in names}} for r in (-8,0,8)])
                model = Mock(manifest={'feature_order':names, 'routing_threshold':-1833})
                model.score.side_effect=lambda features:features.sum(axis=1)
                socket, iface = Mock(), Mock()
                iface.l2socket.return_value.return_value=socket
                callbacks={}
                sniffer=Mock(running=True)
                def create_sniffer(**kwargs):
                    callbacks.update(kwargs)
                    sniffer.start.side_effect=kwargs['started_callback']
                    return sniffer
                def send(frame):
                    if timeout:
                        return
                    sequence, count, _ = struct.unpack('>IBB', frame[18:24])
                    values=np.frombuffer(frame[24:24+count*16],dtype=np.int8).reshape(count,16)
                    callbacks['prn'](response(sequence, values.astype(np.int64).sum(axis=1).tolist()))
                socket.send.side_effect=send
                with patch('sys.argv',['batch','--interface','mock','--count','3','--batch-size','2','--timeout','0.01','--report',str(report)]), \
                        patch('ethernet_batch_rate.BatchModelReference',return_value=model), \
                        patch('ethernet_batch_rate.pd.read_parquet',return_value=data), \
                        patch('ethernet_batch_rate.get_if_hwaddr',return_value='02:00:00:00:00:02'), \
                        patch('ethernet_batch_rate.resolve_iface',return_value=iface), \
                        patch('ethernet_batch_rate.AsyncSniffer',side_effect=create_sniffer):
                    with self.assertRaises(SystemExit) as stopped:
                        main()
                self.assertEqual(stopped.exception.code, int(timeout))
                result=json.loads(report.read_text())
                if timeout:
                    self.assertIsNone(result['completed_variants_per_second'])
                    self.assertEqual(result['missing'],3)
                else:
                    self.assertEqual(result['correct'],3)
                    self.assertGreater(result['completed_variants_per_second'],0)
                    self.assertEqual(socket.send.call_count,2)
                socket.close.assert_called_once()
                sniffer.stop.assert_called_once()


if __name__ == '__main__':
    unittest.main()
