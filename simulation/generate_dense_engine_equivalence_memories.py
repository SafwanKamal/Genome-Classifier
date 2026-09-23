"""Random weight and bias files for dense_engine_equivalence_tb.sv.

The files are committed; rerun this only to change or add a shape.
Packing matches export_fpga.pack_lanes: lane 0 in the most significant bits.
"""

from pathlib import Path

import numpy as np

SIMULATION_DIR = Path(__file__).resolve().parent

# (input_number, output_number, mac_lanes, weight_width, acc_width, bias_limit)
SHAPES = [
    (5, 6, 4, 8, 32, 1 << 20),     # partial last output group
    (1, 3, 4, 8, 32, 1 << 20),     # single input, single partial group
    (64, 151, 64, 8, 25, 1 << 16), # the System One classifier head shape
]


def pack_lanes(values, lane_width):
    mask = (1 << lane_width) - 1
    packed = 0
    for value in values:
        packed = (packed << lane_width) | (int(value) & mask)
    return packed


def write_shape(input_number, output_number, mac_lanes, weight_width, acc_width, bias_limit, rng):
    group_number = (output_number + mac_lanes - 1) // mac_lanes
    padded_outputs = group_number * mac_lanes

    weight_limit = (1 << (weight_width - 1)) - 1
    weights = rng.integers(-weight_limit, weight_limit + 1, size=(padded_outputs, input_number))
    biases = rng.integers(-bias_limit, bias_limit + 1, size=padded_outputs)

    # lanes past the last real output are zero, as export_fpga writes them
    weights[output_number:] = 0
    biases[output_number:] = 0

    name = f"dense_engine_equivalence_{input_number}x{output_number}x{mac_lanes}"
    weight_digits = (mac_lanes * weight_width + 3) // 4
    bias_digits = (mac_lanes * acc_width + 3) // 4

    with open(SIMULATION_DIR / f"{name}_weights.mem", "w") as f:
        for group in range(group_number):
            lanes = slice(group * mac_lanes, (group + 1) * mac_lanes)
            for input_index in range(input_number):
                word = pack_lanes(weights[lanes, input_index], weight_width)
                f.write(f"{word:0{weight_digits}x}\n")

    with open(SIMULATION_DIR / f"{name}_biases.mem", "w") as f:
        for group in range(group_number):
            lanes = slice(group * mac_lanes, (group + 1) * mac_lanes)
            word = pack_lanes(biases[lanes], acc_width)
            f.write(f"{word:0{bias_digits}x}\n")

    print(f"wrote {name}_weights.mem and {name}_biases.mem")


def main():
    rng = np.random.default_rng(20260923)
    for shape in SHAPES:
        write_shape(*shape, rng)


if __name__ == "__main__":
    main()
