"""RB01 request and version-1/type-2 result framing; no hardware dependencies."""
import struct

BOARD_MAC = bytes.fromhex('020000000001')


def build_batch(source_mac, sequence, features):
    if not 1 <= len(features) <= 32:
        raise ValueError('A batch must contain 1..32 variants')
    if not 0 <= sequence <= 0xffffffff - len(features) + 1:
        raise ValueError('Batch sequence range must fit uint32')
    if any(len(row) != 16 for row in features):
        raise ValueError('Each variant requires 16 feature bytes')
    source = bytes.fromhex(source_mac.replace(':', ''))
    if len(source) != 6:
        raise ValueError('Source MAC must have six bytes')
    frame = (bytes([255])*6 + source + b'\x88\xb5RB01' +
             struct.pack('>IBB', sequence, len(features), 0) + b''.join(features))
    return frame.ljust(60, b'\0')


def decode_batch(frame, threshold):
    if len(frame) < 22 or frame[6:12] != BOARD_MAC or frame[12:16] != b'\x88\xb5\x01\x02':
        return None
    sequence, count, reserved = struct.unpack('>IBB', frame[16:22])
    if not 1 <= count <= 32 or reserved != 0 or len(frame) < 22+count*6:
        raise ValueError('Malformed batch response')
    scores = []
    for i in range(count):
        score, classification, deep = struct.unpack('>iBB', frame[22+i*6:28+i*6])
        if (classification, deep) != (int(score >= 0), int(score >= threshold)):
            raise ValueError('Batch result flags disagree with score')
        scores.append(score)
    return sequence, scores
