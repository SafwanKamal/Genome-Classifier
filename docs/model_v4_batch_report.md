# V4 batch accelerator change report — October 1, 2026

The experiment now has a trained `16 → 256 → 256 → 1` model, a shared parallel
MAC core, and up to 32 variants per Ethernet request/reply. It is a working
candidate for physical testing, not a demonstrated host-throughput win.

## Changes made in this step

| Files | Change and reason |
|---|---|
| `rtl/variant_triage_top_ethernet.sv` | Added opt-in `BATCH_MODEL=1` selection for the new parser, core and reply builder. Added one batch-end bit to each existing mailbox. The default remains single-variant V2/V3 behavior. |
| `rtl/batch_model_core.sv` | Added one shared 32-output × 4-input MAC engine, reused for all three dense layers. Synchronous packed ROMs and registered DSP products serve the trained model. |
| `rtl/ethernet_batch_request_RX.sv` | Added RB01 frame parsing and one-batch storage. Streams its records through the existing ready/valid mailbox. |
| `rtl/ethernet_batch_result.sv` | Collects scores and emits one type-2 reply with signed scores and classification/routing flags. |
| `scripts/build_variant_triage_ethernet_only.tcl` | Conditionally adds the three new modules and batch memories when the separate V4 wrapper is used. |
| `scripts/build_model_v4_batch.tcl` | Added a separate user-run project with `BATCH_MODEL=1` and routing threshold −1833. |
| `genomic-dataset-pipeline/software/model/train_batch_model.py` | Added training/export for the deeper model, reusing existing quantization and metric helpers. Selection uses validation only. |
| `software/batch_model_reference.py`, `software/evaluate_batch_model.py` | Added integer/float32 references, trained-checkpoint checks, golden vector generation, and warmed batched host timing. |
| `software/ethernet_batch_protocol.py`, `software/ethernet_batch_rate.py` | Added batch framing/decoding and a user-run correctness/completion benchmark. One outstanding batch keeps the first implementation simple. |
| `simulation/batch_model_core_tb.sv`, `simulation/ethernet_batch_tb.sv`, their two test scripts, and `software/tests/test_ethernet_batch.py` | Added arithmetic, Ethernet, malformed-frame and host-side checks. |
| `scripts/synthesize_batch_model_core.tcl` | Added a core-only synthesis/resource/timing check without creating a bitstream. |
| `README.md`, this report, `reports/model_v4_host_test.json`, `reports/checkpoint_v4/` | Recorded setup, provenance, measured results and limitations. |

No changes were made in this step to the working V2/V3 weights, model headers,
PHY/RMII clock phases, XDC, UART top, or existing single-variant software API.
The general VariantGate pipeline continues to use its existing model schema;
the V4 experiment uses its separate validator and three-layer export schema.

## Model selection and quality

The new middle layer was initialized densely and trained; it is not an inserted
identity layer or repeated dummy work. The first/output layers started from V3.
Training used the existing real sixteen features, seed 7, batch size 4096,
four CPU threads, learning rate 0.02, logit divisor 384, and early-stop patience 12.
Validation average precision selected **epoch 28**; training stopped at epoch 40.
The model has **44,719 nonzero middle-layer weights**.

Layer requantization shifts are 4 and 6. Classification threshold 428 is folded
into the output bias. Routing threshold **−1833** was frozen using validation,
with pathogenic routing recall 0.995099. It differs from the V3 threshold −1914.
The model performs **69,888 MACs per variant** (16×256 + 256×256 + 256), versus V3's 4,352 and V2's 136.

| Held-out metric | V3 | V4 |
|---|---:|---:|
| Average precision | 0.977759 | 0.977264 |
| ROC AUC | 0.989374 | 0.989285 |
| F1 | 0.925249 | 0.927797 |

Quality is mixed: F1 improved, while ranking metrics slipped slightly. V4 is not
universally better. Test data was evaluated after model/threshold selection,
and has not been used to retune the model.

## Protocol and compatibility

The physical link remains 100 Mbps RMII. EtherType remains `0x88B5`.
Batch requests carry `RB01`, a big-endian base sequence, a count from 1 to 32,
a zero reserved byte, and `count × 16` feature bytes. Subsequent variant sequences
are `base + index`. A 32-variant request is **536 bytes before FCS**.

Batch replies use version 1/type 2, base sequence, count/reserved, then six bytes
per record: signed big-endian INT32 score and two flags. A full reply is
**214 bytes before FCS**. Small frames receive standard Ethernet padding.
The existing RX CRC gate and TX CRC generator are reused.

The batch firmware accepts RB01; the old RX01 request command is for the
single-variant firmware. Use the matching new validator after programming V4.
The validator sends one batch, validates its reply, then sends the next batch.
It does not silently retry losses. Missing batches produce a failure report and
no completed-throughput value.

## Verification and performance

* All **27,477** held-out scores matched the trained checkpoint, PyTorch,
  integer NumPy and each tested batched float32 host implementation.
* Core XSim matched **1,000** real vectors at **1,844 cycles** per inference.
* Ethernet XSim passed batches of **1, 32 and 3** records, with 36 exact scores,
  sequences, flags and valid reply CRCs. It rejected bad CRC, RX01 marker,
  truncated batches and zero counts.
* Existing V2 and V3 Ethernet simulations passed after the shared-top change.
* Offline software tests: **55 passed**, with four passing subtests.
* Dry run prepared all 27,477 variants as **859 frames** without hardware access.

At 100 MHz, 1,844 simulated cycles equal **18.44 µs**, or an ideal core ceiling
of about **54,230 variants/s** before admission, clock crossing and Ethernet.
It is about 16.1× more MAC work than V3 with about 1.60× its cycle count.
The hardware clock and physical throughput still require user-run implementation
and board tests.

The first synthesis check found the combined multiplier/adder path too long at
100 MHz. The final core adds one DSP-product register stage and sizes the sum of
four signed 16-bit products to 18 bits before accumulation. This change fixes an
observed arithmetic timing path; it is not an additional protocol safeguard.
Final synthesis resource/timing details are recorded in
`reports/model_v4_core_utilization.rpt` and
`reports/model_v4_core_synthesis_timing.rpt`. Synthesis timing is not routed timing.

Final core-only synthesis at 100 MHz used **128/240 DSPs**, **18.5/135 BRAM tiles**,
**18,960/63,400 LUTs**, and **11,473 registers**. Setup WNS is **+3.027 ns** and
hold slack **+0.148 ns**, with no failing internal endpoints. These results cover
the core, not a routed full-board design. The synthesis-only script includes the
10 ns clock constraint before optimization; board XDC remains unchanged.

Host results are recorded in `reports/model_v4_host_test.json`: five warmed
repetitions on prepared features, with weights preconverted outside the timer,
batch sizes 32/64/1024 and one/four BLAS threads. The best measured batched host
rate is **434,605 variants/s** (64 variants, one BLAS thread), measured again
after Vivado finished to avoid competing synthesis/simulation work. That is
about **8.0× the ideal FPGA core ceiling**, with transport still excluded from
the FPGA figure. **No accelerator
investment case or end-to-end speedup is established by this experiment.**
Batching addresses per-variant host/network overhead; it does not by itself
overcome the compute gap. Measure the board results before choosing another
architecture change.

## User-run build and test commands

No bitstream was generated and no physical packets were sent during this work.
The top remains **`rtl/variant_triage_top_ethernet.sv`**. Use the V4 script to
select batch mode, its memories and threshold:

```powershell
& 'C:/AMDDesignTools/2025.2/Vivado/bin/vivado.bat' -mode batch -source scripts/build_model_v4_batch.tcl
```

Program `build/ethernet_classifier_v4_batch/ethernet_classifier_only.bit` after
the build passes routed timing. Wait for PHY reset/startup to finish, then run
these commands from the repository root:

```powershell
uv run --with scapy --with numpy --with pandas --with pyarrow python software/ethernet_batch_rate.py --interface "Ethernet" --count 100 --batch-size 1 --sequence 4000000 --report reports/model_v4_batch_1.json
uv run --with scapy --with numpy --with pandas --with pyarrow python software/ethernet_batch_rate.py --interface "Ethernet" --count 1000 --batch-size 32 --sequence 4100000 --report reports/model_v4_batch_32.json
uv run --with scapy --with numpy --with pandas --with pyarrow python software/ethernet_batch_rate.py --interface "Ethernet" --count 27477 --batch-size 32 --sequence 4200000 --report reports/model_v4_batch_full.json
```

Require all replies and scores to match. Compare batch sizes on the same model.
Completed throughput measures first batch send to last correct host-captured
reply, including waits between batches. Dataset loading, reference scoring,
frame construction and capture startup are excluded. No fixed capture tail is
included. Physical results and host preparation cost must be distinguished from
core-only simulation figures.

Training/export artifacts are under
`genomic-dataset-pipeline/artifacts/model_v4_batch_seed_7/`; earlier model
directories remain separate. Reproducible offline commands:

```powershell
# From genomic-dataset-pipeline, retrain/export (overwrites only V4 artifacts):
uv run python -m software.model.train_batch_model
# From repository root, evaluate the frozen checkpoint and generate vectors:
& 'genomic-dataset-pipeline/.venv/Scripts/python.exe' -m software.evaluate_batch_model --report reports/model_v4_host_test.json --vectors build/model_v4_test.mem
```

Simulation-only scripts: `scripts/test_batch_model_core.tcl` and
`scripts/test_ethernet_batch.tcl`. Core-only synthesis:
`scripts/synthesize_batch_model_core.tcl`. The build script is supplied for you
to run; it was not executed here.
