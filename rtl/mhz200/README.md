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

## User board tests, October 3, 2026

The user supplied these reports after programming the new design:

| Report | Correct / requested | Completion time | Variants/s |
|---|---|---|---|
| `reports/model_v4_200_smoke.json` (window 1) | 100 / 100 | 1.7065 ms | 58,599 |
| `reports/model_v4_200_full_timing.json` (window 2, instrumentation) | 27,477 / 27,477 | 193.9651 ms | 141,660 |
| `reports/model_v4_200_full.json` (window 2, no instrumentation) | 27,477 / 27,477 | 178.6501 ms | 153,803 |

All three have zero missing results, duplicates and mismatches, and no error.
Against the previous desktop instrumented result of 127,499 variants/s,
the instrumented new run improves by **11.1%**. The uninstrumented run is
**20.6%** above that previous result, but instrumentation differs; do not
attribute that entire difference to the hardware change. These are single
runs, and the reports do not record host/NIC identity or FPGA clock identity.
Keep the same desktop/adapter and repeat both designs without instrumentation
to establish a median speedup.

The new instrumented run has median send-to-reply latency **420.2 us**,
send-call duration **70.4 us**, capture-to-callback delay **74.9 us**, and
reply-to-refill delay **27.8 us**. Send calls occupy about 32.0% and queue waits
62.9% of the completion interval. Queue waits include hardware, transport and
host capture; they do not isolate FPGA compute. The 153,803/s result is about
50.1% of the simulated queued compute ceiling. Doubling the target clock has
not doubled end-to-end throughput. The recorded CPU baseline remains about
5.0 times faster than this uninstrumented FPGA run.

Three further uninstrumented full-cohort runs (`model_v4_200_full_1.json`,
`model_v4_200_full_2.json`, `model_v4_200_full_3.json` in `reports`) each return
27,477/27,477 correct, with zero missing, duplicates, mismatches or errors.
Rates are **155,245**, **141,080** and **154,051 variants/s**, respectively.
The median is **154,051/s** (178.3629 ms), about 50.2% of the simulated queued
compute ceiling; the range spans 9.2% of the median. It is 20.8% above the
previous instrumented desktop result, with the same instrumentation caveat
as above. The CPU baseline is about 4.99 times faster than this median.

## Activation-selector revision and raw host transport

The original full routed 200 MHz build subsequently reports **-0.812 ns setup
slack and 1,666 failing endpoints**, despite the board tests above passing.
The critical path is activation selection into DSP inputs. The updated core
splits that selection across its existing two read cycles: a registered
16-way selection in each of four quarters, followed by a 4-way quarter
selection. Weights, model, cycle count and 128 DSPs remain unchanged.

All 128 quarter-selection registers survive synthesis without retention
attributes. The revised core passes 1,000 golden vectors in 635 cycles.
Core-only synthesis has +0.357 ns setup and +0.079 ns hold slack, with 5,003
LUTs, 8,123 registers and 16.5 BRAM tiles. These replace the earlier core-only
synthesis resource/timing figures above. **Full routed timing of this revision
is still pending; the previous routed report describes the old selector.**

The new `simulation/ethernet_batch_200_window_tb.sv` tests a strict four-credit
host over 2,000 variants in 98 full/mixed batches, including consecutive
single-record requests. It fills both parser and both RX banks by initially
holding classifier acceptance, then exercises sustained reply-credit refill
and bank wrap. All scores, sequences, flags and reply CRCs pass, with no
receiver overflow or error LED. The test's reply receiver uses two banks to
handle minimum-gap replies while decoding the preceding packet. Existing
production RX/request/result storage is unchanged. Run it with
`scripts/test_ethernet_batch_200_window.tcl`.

`software/ethernet_batch_stream_rate_fast.py` is a separate host experiment:
raw-byte capture, credit refill before golden-score checking, and windows
1, 2 or 4 (default 2). It preserves mandatory score/flag validation and adds
host/NIC/backend/model identity plus a user-supplied design label to reports.
The original host benchmark remains unchanged. See the
[implementation report](../../docs/bottleneck_implementation_report.md) for
files, validation scope, Vivado acceptance and user-run commands.
