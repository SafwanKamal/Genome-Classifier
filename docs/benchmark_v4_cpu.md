# V4 CPU benchmark on the second desktop

`software/benchmark_v4_cpu.py` benchmarks the frozen `16 → 256 → 256 → 1`
model using the same prepared INT8 feature rows as the Ethernet tests. It
needs only the two files already made eligible for Git:

- `genomic-dataset-pipeline/artifacts/model_v4_batch_seed_7/export_manifest.json`
- `genomic-dataset-pipeline/data/processed/variants_model_int8.parquet`

No PyTorch checkpoint, training run, Scapy installation or FPGA connection is
needed. The script reuses `software/batch_model_reference.py`; that dependency
must also be synced. New files are the benchmark, its offline tests at
`software/tests/test_benchmark_v4_cpu.py`, and this document. Existing RTL,
model, Ethernet tools and benchmark scripts are unchanged.

From the repository folder on the second desktop:

```powershell
uv run --with numpy --with pandas --with pyarrow --with threadpoolctl python software/benchmark_v4_cpu.py --report reports/model_v4_cpu_desktop.json --fpga-report reports/model_v4_capture_precise_timing.json
```

Use that desktop's own FPGA JSON (the run returning approximately 127,499/s).
If it is elsewhere, pass its actual path. To benchmark without comparison:

```powershell
uv run --with numpy --with pandas --with pyarrow --with threadpoolctl python software/benchmark_v4_cpu.py --report reports/model_v4_cpu_desktop.json
```

Defaults: all 27,477 test variants; CPU batch sizes 32, 64 and 1024; one and
four BLAS threads; one warmup and five measured repetitions per configuration.
Every float32 result must match the bounded INT64 reference exactly. Loading,
feature conversion, golden-reference generation, warmup and comparison checks
are outside the inference timer. Batch orchestration and producing all scores
are included. Results use median times; the best configuration is selected by
its median throughput, not a single fastest repetition.

The JSON records machine/OS/Python/NumPy details, active numerical threadpools,
model/features/scores hashes, every measured time, median rates and the best
configuration. Optional FPGA comparison requires a passing run for the same
count and split; it reports CPU/FPGA and FPGA/CPU ratios. Older FPGA JSON does
not identify the host or model hash, so matching those remains a user check.
CPU timings measure prepared-feature inference; FPGA timings include request
and reply transport after preparation. This tests whether offloading this
inference path benefits completed throughput; it does not compare CPU
arithmetic directly with FPGA arithmetic alone.

Run with other host thread counts using, for example, `--threads 1 4 8`.
Repeat the default benchmark with other applications idle before drawing a
performance conclusion. Keep the same model and test cohort across comparisons.

Verification on October 3: two offline tests pass, checking partial CPU batches,
exact-score validation, and rejection of failed/different-cohort FPGA reports.
A smoke run on 64 real V4 test rows passes exactness at batch sizes 32 and 64.
Its short timings are validation only, not a new full-set performance claim.

## Second desktop result — October 3

The supplied `reports/model_v4_cpu_desktop.json` reports exact scores for all
27,477 variants in every measured configuration. The model manifest hash
matches the frozen V4 export. Its FPGA comparison uses the supplied desktop
rate 127,498.865/s (27,477/27,477 correct, zero reply errors).

| Backend/configuration | Median time | Variants/s |
|---|---:|---:|
| FPGA, batch 32/window 2 | 215.51 ms (one run) | 127,499 |
| CPU, batch 32/one thread | 65.36 ms | 420,371 |
| CPU, batch 32/four threads | 54.24 ms | 506,557 |
| CPU, batch 64/one thread | 47.59 ms | 577,379 |
| CPU, batch 64/four threads | 35.73 ms | 768,944 |

The best tested CPU configuration is 6.031× faster than measured FPGA
completion. At the same batch size of 32, four-thread CPU inference is 3.973×
faster. Even the fastest one-thread configuration is 4.529× faster. CPU values
are medians of five warmed runs; FPGA has one supplied run and includes
transport. No end-to-end acceleration advantage is demonstrated.

This desktop changes the throughput target: the current 32-record/100 Mb/s
protocol's ideal wire ceiling is 714,286 variants/s, already below the CPU's
768,944/s. More FPGA DSPs or a higher core clock alone cannot overcome that
protocol limit. All 240 DSPs at 200 MHz have an unpacked arithmetic budget
of 686,813/s for V4, also below this CPU rate. Ideal all-DSP arithmetic parity
would require about 223.9 MHz before scheduling overhead, plus transport
improvements. A different transport/encoding or an application-justified
workload with a better accelerator advantage is needed to pursue throughput
parity; simply scaling the FPGA core under the existing protocol is insufficient.
