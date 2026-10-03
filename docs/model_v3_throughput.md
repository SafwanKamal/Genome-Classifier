# Larger model and throughput experiment — October 1, 2026

The objective is **completed variants per second**, compared with an efficient
host implementation. V3 is a trained `16 → 256 → 1` candidate, using the same
sixteen real features and INT8 arithmetic as V2. It performs 4,352 MACs per
variant versus 136 for V2 (32×). It is not evidence of an accelerator speedup.

## Model and correctness

Training used seed 7, four CPU threads, batch size 4096, logit divisor 384,
150 maximum epochs and patience 20. Training stopped at epoch 116; validation
average precision selected epoch 96. Neither width nor thresholds were selected
using the locked test set. The classification threshold 426 was folded into the
output bias, so the wire classification rule remains `score >= 0`.

| Metric | V2, 8 hidden neurons | V3, 256 hidden neurons |
|---|---:|---:|
| Validation average precision | 0.962755 | 0.967646 |
| Test average precision | 0.975551 | 0.977759 |
| Test ROC AUC | 0.988233 | 0.989374 |
| Test F1 | 0.924382 | 0.925249 |

The quality gain is modest despite the much larger network. All 27,477 locked
test scores matched between PyTorch and the integer NumPy reference.
Validation hidden saturation was 0.345%. The separately frozen routing threshold
is **−1914**, achieving validation pathogenic routing recall 0.995099 with
46.75% routed to deep review. V2's −276 threshold does not apply to V3.

Artifacts are in `genomic-dataset-pipeline/artifacts/model_v3_h256_seed_7/`;
the export has 32 packed hidden MAC lanes and one output MAC lane.
V2 memories and its model header remain available for the working V2 build.
Provenance copies and the routing policy are under `reports/checkpoint_v3/`.

## Measured throughput and its implication

The Ryzen 7 8845HS host benchmark uses five warmed repetitions, prepared features,
and verifies every score. Input loading and output writing are outside the timer.
Float32 matrix multiplication is used as an efficient alternative to scalar
INT64 inference; all results must match the integer reference exactly.

| V3 locked test measurement | Variants/s |
|---|---:|
| Scalar NumPy INT64, median | 36,050 |
| Batched float32, 64 variants, one BLAS thread, median | 1,477,623 |
| RTL compute ceiling at 100 MHz, before transport | about 86,655 |

Core XSim matched 1,000 real validation vectors at **1,154 cycles** per inference
(11.54 µs at 100 MHz). The reciprocal is an upper bound: it omits request admission,
mailboxes, Ethernet, and host capture. It is not measured board throughput.
End-to-end Ethernet simulation checks the real RX/parser/mailboxes/core/TX path,
including sequence, score, flags and reply CRC. V2 regression simulation also passes.
Offline VariantGate tests pass with the configurable routing threshold.

Reports: `reports/model_v3_host_throughput.json` (validation) and
`reports/model_v3_host_test_throughput.json` (locked test).
The earlier V2 raw sender's 6,667 requests/s measures send-call time, not completed
work. These measurements have different timing scopes and must not be presented
as an end-to-end speedup ratio.

**This candidate does not meet the throughput investment argument.** Even its
ideal compute ceiling is about 17× below the best measured batched host rate.
Increasing the tiny model has improved quality slightly, but CPU batching remains
very effective. The next architectural experiment needs a useful heavier workload,
batch transfers, and parallel/pipelined compute. Simply widening this network or
comparing against an intentionally slow CPU loop would not establish that benefit.

## Build and physical tests (user runs these)

No V3 bitstream was generated, and no packets were sent to hardware during this work.
Physical correctness, synthesized resource usage and routed timing are unverified.
The 32 hidden lanes plus one output lane target 33 DSP multipliers; confirm actual
mapping and timing in Vivado. Product-level `use_dsp` and flop-level `ASYNC_REG`
placement are retained; see `fpga_synthesis.md`.

Top SV: **`rtl/variant_triage_top_ethernet.sv`**.
Use the separate V3 script to select its memories and generics; choosing the top
alone keeps its V2 defaults. The XDC remains
`constraints/variant_triage_ethernet_only.xdc`; model width does not change board pins.

From the repository root, to build when ready:

```powershell
& 'C:/AMDDesignTools/2025.2/Vivado/bin/vivado.bat' -mode batch -source scripts/build_model_v3_ethernet.tcl
```

Program `build/ethernet_classifier_v3/ethernet_classifier_only.bit`. Start with
a paced correctness check, then test a burst:

```powershell
uv run --with scapy python software/ethernet_request_rate.py --interface "Ethernet" --sender raw --vectors build/model_v3_full_test.mem --count 100 --sequence 3000000 --interval 0.001 --routing-threshold -1914 --report reports/model_v3_smoke.json
uv run --with scapy python software/ethernet_request_rate.py --interface "Ethernet" --sender raw --vectors build/model_v3_full_test.mem --count 1000 --sequence 3100000 --interval 0 --routing-threshold -1914 --report reports/model_v3_burst.json
uv run --with scapy python software/ethernet_request_rate.py --interface "Ethernet" --sender raw --vectors build/model_v3_full_test.mem --count 27477 --sequence 3200000 --interval 0 --routing-threshold -1914 --report reports/model_v3_full_test.json
```

The validator now also reports **completed variants/s**, from send start to the
last correct host-captured reply. Preparation and capture startup are excluded;
the extra capture tail is excluded. If any requested correct reply is missing,
that completion rate is null. Retain loss and mismatch counts alongside rates.
This is host-observed end-to-end request/response timing, not a wire timestamp.

For reproducible host benchmarking and vector generation:

```powershell
uv run --with numpy --with pandas --with pyarrow --with pyserial --with threadpoolctl python -m software.benchmark_model_throughput --manifest genomic-dataset-pipeline/artifacts/model_v3_h256_seed_7/fpga_export/export_manifest.json --input genomic-dataset-pipeline/data/processed/variants_model_int8.parquet --split test --report reports/model_v3_host_test_throughput.json --vectors build/model_v3_full_test.mem
```

Simulation-only scripts: `scripts/test_model_v3_core.tcl` and
`scripts/test_model_v3_ethernet.tcl`. They use `build/model_v3_validation.mem`,
generated with the benchmark command's default `--split validation` and a separate
validation report/vector output. They do not synthesize or create a bitstream.
