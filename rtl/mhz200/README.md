# V4 classifier: isolated 200 MHz target

This directory contains a new implementation; the existing RTL files remain
unchanged. The model, memory packing, 128 DSP multipliers, score interpretation
and Ethernet packet format are retained. **200 MHz is a target, not yet a
verified routed operating frequency.** No bitstream was generated.

## Changes

- `batch_model_200_core.sv`: register the balanced four-product reduction in
  two stages, then separate accumulation, completed-result capture, rounding
  and saturation. Delay bias and group/valid tags with their corresponding
  data. Quantize only the completed 32-output group rather than broadcasting
  full-width arithmetic to 256 output registers.
- Add a register between the weight BRAM read and multiplication, with matching
  input, bias and control delays, to shorten the BRAM-to-DSP path.
- Keep a separate next-layer activation array. Copy it to the input array
  only after all output groups complete, so later groups still read the
  original inputs of that layer.
- `variant_triage_top_ethernet_200.sv`: use a separate 200 MHz clock for the
  classifier, request mailbox destination, result mailbox source, request
  tracking and classifier reset release. Ethernet clocks remain 50 MHz at
  phases 0/180/36 degrees. The external board clock remains 100 MHz.

The original single-model branch is retained in the copied top; this variant
is validated with `BATCH_MODEL=1`, using the V4 model.

## Vivado setup

Use top **`variant_triage_top_ethernet_200`**. In an existing streamed project,
the new RTL files to add are just the two `.sv` files in this directory.
Retain the existing Ethernet modules and `score_clock_domain_crosser.sv`.
Retain `batch_weights.mem` and `batch_biases.mem` as project sources, from
`genomic-dataset-pipeline/artifacts/model_v4_batch_seed_7/memory`.

Create a **new** Clocking Wizard named `clk_wiz_ethernet_200`:

| Setting | Value |
|---|---|
| Input | 100 MHz |
| `clk_out1` | 50 MHz, 0 degrees |
| `clk_out2` | 50 MHz, 180 degrees |
| `clk_out3` | 50 MHz, 36 degrees |
| `clk_out4` | 200 MHz, 0 degrees |
| Reset / locked | Active-high reset, locked output enabled |

Reuse `constraints/variant_triage_ethernet_only.xdc`; do not change its board
input constraint from 10 ns to 5 ns. The Clocking Wizard supplies the generated
200 MHz constraint. Its top instance remains named `clock_generator`, matching
the existing XDC hierarchy.

Alternatively, this command in the Vivado Tcl Console creates a separate
project with the files and Clocking Wizard configured automatically:

```tcl
source {C:/Users/user/OneDrive - Texas Tech University/Desktop/Code/FPGA Project/Genome Classifier/scripts/setup_model_v4_200.tcl}
```

It creates `build/ethernet_classifier_v4_200/ethernet_classifier_v4_200.xpr`.
It does not launch synthesis, implementation or bitstream generation. Run
Synthesis and Implementation in Vivado. Check that the core clock is 200 MHz
and all routed setup/hold constraints are met before generating a bitstream.

## Functional verification, October 3, 2026

- XSim core test: **1,000/1,000 golden V4 scores matched**, 635 cycles per
  variant, versus 617 cycles for the original streamed core.
- XSim Ethernet integration: **75 exact scores** across batch sizes
  32, 32, 3, 1 and 7; overlapping frames, bank wrap and reply CRC checked.
  Invalid CRC, marker, count and truncated requests are rejected.
- Simulated queued start spacing: 652 to 653 core cycles, mean 652.02.
  At the target clock this corresponds to approximately **306,740 variants/s**
  before host/transport stalls, versus the original queued compute ceiling of
  159,236/s. It is a theoretical ceiling, not a measured board result or a
  promise of CPU parity.

Simulation scripts: `scripts/test_batch_model_200_core.tcl` and
`scripts/test_ethernet_batch_200.tcl`. The integration test uses behavioral
clock models, not a timing simulation of the routed design.
Core-only synthesis check: `scripts/synthesize_batch_model_200_core.tcl`.
The final core-only synthesis at a 5 ns period meets setup with **+0.444 ns**
worst slack and hold with **+0.079 ns** worst slack. Resource use is **128 DSPs,
4,849 LUTs, 8,022 registers and 16.5 BRAM tiles**. Reports are
`reports/model_v4_200_core_synthesis_timing.rpt` and
`reports/model_v4_200_core_utilization.rpt`. An initial version missed setup
by 0.180 ns on the BRAM-to-DSP path; the added matched register stage resolves
that failure in synthesis. These estimates exclude the full Ethernet top and
physical routing.
The full routed timing report remains the acceptance criterion for 200 MHz.
