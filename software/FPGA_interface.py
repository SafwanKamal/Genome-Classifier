import random
import time

import serial


SERIAL_PORT = "COM8"
BAUD_RATE = 115_200
READ_TIMEOUT_SECONDS = 2.0

FEATURE_NUMBER = 16
TEST_NUMBER = 100
RANDOM_SEED = 42

REQUEST_HEADER = 0x5A
RESPONSE_HEADER = 0xA5

QSHIFT = 4

HIDDEN_WEIGHTS = [
    [1, -2, 3, -4, 5, -6, 7, -8,
     9, -10, 11, -12, 13, -14, 15, -16],

    [1, 1, 1, 1, 1, 1, 1, 1,
     1, 1, 1, 1, 1, 1, 1, 1],

    [1, -1, 1, -1, 1, -1, 1, -1,
     1, -1, 1, -1, 1, -1, 1, -1],

    [-16, 15, -14, 13, -12, 11, -10, 9,
     -8, 7, -6, 5, -4, 3, -2, 1],
]

HIDDEN_BIASES = [25, -10, 100, 0]

OUTPUT_WEIGHTS = [1, -2, 3, -4]
OUTPUT_BIAS = 25


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


def build_request(features: list[int]) -> bytes:
    if len(features) != FEATURE_NUMBER:
        raise ValueError(
            f"Expected {FEATURE_NUMBER} features, received {len(features)}"
        )

    if any(value < -128 or value > 127 for value in features):
        raise ValueError("Every feature must be in the signed INT8 range")

    payload = bytes(value & 0xFF for value in features)

    packet_without_crc = bytes([
        REQUEST_HEADER,
        FEATURE_NUMBER,
    ]) + payload

    crc = crc16_ccitt(packet_without_crc)

    return packet_without_crc + crc.to_bytes(2, byteorder="big")


def dense_score(
    features: list[int],
    weights: list[int],
    bias: int,
) -> int:
    return bias + sum(
        feature * weight
        for feature, weight in zip(features, weights)
    )


def relu_quantize(value: int) -> int:
    if value <= 0:
        return 0

    rounded = value + (1 << (QSHIFT - 1))
    scaled = rounded >> QSHIFT

    return min(scaled, 127)


def software_model(
    features: list[int],
) -> tuple[int, list[int], list[int]]:
    hidden_scores = [
        dense_score(features, weights, bias)
        for weights, bias in zip(HIDDEN_WEIGHTS, HIDDEN_BIASES)
    ]

    quantized_scores = [
        relu_quantize(score)
        for score in hidden_scores
    ]

    output_score = dense_score(
        quantized_scores,
        OUTPUT_WEIGHTS,
        OUTPUT_BIAS,
    )

    return output_score, hidden_scores, quantized_scores


def read_exact(serial_port: serial.Serial, byte_number: int) -> bytes:
    received = bytearray()
    deadline = time.monotonic() + READ_TIMEOUT_SECONDS

    while len(received) < byte_number and time.monotonic() < deadline:
        chunk = serial_port.read(byte_number - len(received))

        if chunk:
            received.extend(chunk)

    if len(received) != byte_number:
        raise TimeoutError(
            f"Expected {byte_number} response bytes, "
            f"but received {len(received)}"
        )

    return bytes(received)


def read_response(serial_port: serial.Serial) -> tuple[int, bytes]:
    response = read_exact(serial_port, 5)

    if response[0] != RESPONSE_HEADER:
        raise ValueError(
            "Invalid response header: "
            f"expected {RESPONSE_HEADER:02X}, "
            f"received {response[0]:02X}"
        )

    score = int.from_bytes(
        response[1:5],
        byteorder="big",
        signed=True,
    )

    return score, response


def make_test_vectors() -> list[list[int]]:
    random_generator = random.Random(RANDOM_SEED)

    tests = [
        [0] * FEATURE_NUMBER,
        list(range(FEATURE_NUMBER)),
        [1] * FEATURE_NUMBER,
        [127] * FEATURE_NUMBER,
        [-128] * FEATURE_NUMBER,
        [
            127 if i % 2 == 0 else -128
            for i in range(FEATURE_NUMBER)
        ],
    ]

    while len(tests) < TEST_NUMBER:
        tests.append([
            random_generator.randint(-128, 127)
            for _ in range(FEATURE_NUMBER)
        ])

    return tests


def main() -> None:
    test_vectors = make_test_vectors()

    print(
        f"Running {len(test_vectors)} complete-network hardware tests "
        f"on {SERIAL_PORT}"
    )
    print(f"Random seed: {RANDOM_SEED}")

    with serial.Serial(
        port=SERIAL_PORT,
        baudrate=BAUD_RATE,
        timeout=0.05,
    ) as serial_port:
        serial_port.reset_input_buffer()

        for test_index, features in enumerate(test_vectors, start=1):
            request = build_request(features)

            expected_score, hidden_scores, quantized_scores = (
                software_model(features)
            )

            serial_port.reset_input_buffer()
            serial_port.write(request)
            serial_port.flush()

            try:
                fpga_score, response = read_response(serial_port)
            except (TimeoutError, ValueError) as error:
                print(f"\nFAIL on test {test_index}")
                print(f"Features:         {features}")
                print(f"Request bytes:    {request.hex(' ').upper()}")
                print(f"Hidden scores:    {hidden_scores}")
                print(f"Quantized scores: {quantized_scores}")
                print(f"Expected score:   {expected_score}")
                raise RuntimeError(str(error)) from error

            if fpga_score != expected_score:
                print(f"\nFAIL on test {test_index}")
                print(f"Features:         {features}")
                print(f"Request bytes:    {request.hex(' ').upper()}")
                print(f"Response bytes:   {response.hex(' ').upper()}")
                print(f"Hidden scores:    {hidden_scores}")
                print(f"Quantized scores: {quantized_scores}")
                print(f"Expected score:   {expected_score}")
                print(f"FPGA score:       {fpga_score}")
                return

            if test_index == 1 or test_index % 10 == 0:
                print(
                    f"Passed {test_index}/{len(test_vectors)} tests"
                )

    print(
        f"PASS: FPGA and Python matched for all "
        f"{len(test_vectors)} tests"
    )


if __name__ == "__main__":
    main()