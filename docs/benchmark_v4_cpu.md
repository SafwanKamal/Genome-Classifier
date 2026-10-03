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
