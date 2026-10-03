# Raw Ethernet classifier requests and results

## Ethernet-only top — October 1, 2026

Use **`variant_triage_top_ethernet`**, from `rtl/variant_triage_top_ethernet.sv`,
for Ethernet-only inference. It contains no UART ports, receiver, transmitter,
input arbitration, or UART busy gate. The classifier accepts a request when
it is idle and the result mailbox has capacity. The RX01 request and result
formats, model weights, three clock phases, reset handling, and LED meanings
are unchanged. Buffers remain finite; short-burst board results are recorded below.

In your Vivado project:

1. Add `rtl/variant_triage_top_ethernet.sv` and set `variant_triage_top_ethernet`
   as the synthesis top. Keep the existing classifier, RX/TX, CRC, parser,
   mailbox, and model memory files.
2. Use **only** `constraints/variant_triage_ethernet_only.xdc` for this top;
   disable the combined UART/Ethernet constraints. Mark the new XDC processing
   order **Late**, so its input-clock correction follows the clock-IP constraints.
3. Keep `clk_wiz_ethernet` configured with 50 MHz outputs at 0°, 180°, and 36°.
   UART source files are unnecessary for this top.
4. Build and program it yourself, clear the debug-probes file (there is no ILA),
   and wait about three seconds after PHY reset release.

`scripts/build_variant_triage_ethernet_only.tcl` provides the matching build
configuration in a separate `build/ethernet_classifier_only` project. It was
prepared but **not run by the assistant**. The user subsequently tested the
Ethernet-only design; its routed timing reports have not been reviewed.

XSim passed on October 1 using `scripts/test_variant_triage_ethernet_only.tcl`:
three committed V2 vectors and the physical bring-up vector (score -124)
returned matching sequences, scores, classification/routing flags, padding,
and Ethernet CRC. ARP and a corrupted-FCS request launched no inference.
The old combined top and its physical results below are retained as the baseline.

After programming, run these yourself from the repository folder. No UART
connection or pyserial dependency is needed. Use the Ethernet rate validator;
the older dual-output validator requires UART replies and does not fit this top.

```powershell
# All 1,000 saved vectors, with conservative pacing.
uv run --with scapy python software/ethernet_request_rate.py --interface "Ethernet" --count 1000 --sequence 400000 --interval 0.01 --report reports/ethernet_only_vectors.json

# Repeat the previous 1 ms and burst checks for comparison.
uv run --with scapy python software/ethernet_request_rate.py --interface "Ethernet" --count 100 --sequence 500000 --interval 0.001 --report reports/ethernet_only_rate_1ms.json
uv run --with scapy python software/ethernet_request_rate.py --interface "Ethernet" --count 100 --sequence 600000 --interval 0 --report reports/ethernet_only_rate_burst.json
```

Expected: every reply has its request sequence and golden score/flags, with no
missing, duplicate, or out-of-order replies. Record the actual burst result;
removing UART pacing does not by itself establish lossless reception.

### Physical Ethernet-only XDC comparison - October 1, 2026

The user reported two groups of three Ethernet-only tests: first with the
previous XDC, then with the new Ethernet-only XDC. The pasted transcript
also contains the earlier combined UART/Ethernet baseline, which is separate
from this six-run comparison. Exact previous XDC contents and routed timing
reports for these builds were not supplied.

| Test | Previous XDC | New Ethernet-only XDC |
|---|---|---|
| 1,000 vectors, 10 ms interval | 1000/1000 correct; host send rate 86.9/s | 1000/1000 correct; host send rate 90.4/s |
| 100 vectors, 1 ms interval | 100/100 correct; host send rate 615.4/s | 100/100 correct; host send rate 587.0/s |
| 100 vectors, burst | 100/100 correct; host send rate 2631.7/s | 100/100 correct; host send rate 2385.6/s |

All six runs had zero missing, duplicate, out-of-order, or mismatched replies.
Both groups used the same report filenames, so the later JSON reports replace
the earlier group; the pasted console transcript preserves both.

These runs show no correctness improvement attributable to the XDC change.
Host send-call rates do not measure FPGA processing speed. Both Ethernet-only
builds completed their 100-request bursts, compared with 68/100 replies for
the combined top. This is consistent with removing UART pacing helping, but
does not isolate every implementation or host effect. Keep the Ethernet-only
XDC to check external RX/TX timing and classifier clocks; passing functional
tests alone does not establish timing margin. Sustained maximum throughput
and direct RX overflow counts remain unmeasured.

### Longer Ethernet-only burst - October 1, 2026

The user ran 1,000 requests with no intentional spacing, sequences 700000
through 700999. All 1000/1000 replies matched the committed V2 vectors;
missing, duplicate, out-of-order, unrelated, and mismatched reply counts
were zero. The host send call took 0.311 seconds (3211.5 requests/s), with a
two-second capture tail. This is a host send-call rate, not a wire-rate or
sustained FPGA capacity measurement. Evidence:
`reports/ethernet_only_burst_1000.json`.

### Next: the complete locked V2 test set

The existing 1,000 committed vectors are sampled from real held-out genomic
variants. The next check expands that coverage to all 27,477 locked-test
variants using the existing generator, without changing RTL or model weights:

```powershell
uv run --with pandas --with pyarrow python genomic-dataset-pipeline/software/model/generate_core_vectors.py --predictions genomic-dataset-pipeline/artifacts/model_v2_h8_seed_7/test_predictions.parquet --data genomic-dataset-pipeline/data/processed/variants_model_int8.parquet --manifest reports/checkpoint_v2/export_manifest.json --output build/ethernet_v2_full_test.mem --test-number 27477
```

The assistant prepared this file offline and checked all 27,477 golden scores
against the exported V2 manifest arithmetic. The Ethernet sender dry-run also
prepared all 27,477 frames successfully. Scores range from -1362 to 631.
There are 113 duplicate packed feature/score lines, representing variants
with identical quantized inputs; each request still gets a distinct sequence.
This is an offline preparation result, not a physical FPGA pass.

The user can now run the full physical comparison with 1 ms requested pacing:

```powershell
uv run --with scapy python software/ethernet_request_rate.py --interface "Ethernet" --vectors build/ethernet_v2_full_test.mem --count 27477 --sequence 1000000 --interval 0.001 --report reports/ethernet_only_full_v2_test.json
```

Expected: 27477/27477 correct, with zero missing, duplicate, out-of-order,
or mismatched replies. This checks hardware/model parity, including result
flags and sequence association; matching the model is separate from matching
biological labels. Physical testing remains with the user.

### Full locked V2 Ethernet pass and batch-size measurements - October 1, 2026

The user completed the unpaced full-set comparison: 27477/27477 correct,
with zero missing, duplicate, out-of-order, unrelated, or mismatched replies.
The host send call took 6.6911774 seconds (4106.45 requests/s). The report
`reports/ethernet_only_full_v2_test.json` records this run and a two-second
capture tail. The preceding paced full-set console excerpt reports 43.209
seconds and 635.9 requests/s; its receive summary was not in that excerpt,
and the report filename was overwritten by the unpaced run.

Using the later/new-XDC 100-request burst for a consistent baseline:

| Burst requests | Host send-call seconds | Requests/s | Average send-call time/request |
|---|---|---|---|
| 100 | 0.041918 | 2385.6 | 419.2 us |
| 1,000 | 0.311380 | 3211.5 | 311.4 us |
| 27,477 | 6.691177 | 4106.5 | 243.5 us |

The observed rate increased 34.6% from 100 to 1,000 requests, then 27.9%
from 1,000 to 27,477: 72.1% overall. Average send-call time per request fell
41.9%. Removing 1 ms requested spacing in the full-set run reduced send-call
time by 84.5% and increased the observed rate about 6.46 times.

These are single measurements of host send calls, excluding capture setup
and the two-second capture tail. Average send-call time per request is not
inference or round-trip latency. The increase is consistent with amortized
host overhead and runtime variation; it does not establish that FPGA compute
became faster, or isolate the cause. The largest test also uses the full
dataset rather than the 1,000-vector subset. Repeated runs with the same
packet content at each batch size would be needed to estimate variability.

### Host send overhead investigation - October 1, 2026

Inspection of the installed Scapy 2.7.0 implementation found these costs
inside the existing timed `sendp` call:

- `sendp` / `_send` resolve the interface, open a layer-2 capture handle,
  send the batch, and close the handle. This setup occurs once per batch.
- `__gen_send` iterates Scapy packet objects; packet iteration clones layers.
  `L2pcapSocket.send` converts each packet to bytes, and
  `_PcapWrapper_libpcap.send` calls `pcap_inject` once for each packet.
- `__gen_send` calls `time.sleep(inter)` after every packet, even when
  `inter` is zero. Concurrent capture and reply decoding also run on the
  host during the real test. Their contention/scheduling contribution has
  not yet been isolated.

An offline measurement ran the same Scapy generator over 1,000 request
packets five times, using a fake socket that serializes but never injects
packets. Median duration was 35.1 ms normally, and 30.3 ms with the sleep
call replaced by a no-op in that isolated process. Profiling this offline
path identified packet cloning/iteration as its main cost. See
`reports/ethernet_send_offline_profile.json` for the five measurements.

The real 1,000-request send call took 311.4 ms. These offline measurements
exclude capture-handle setup, Npcap injection, concurrent reception, and the
NIC, so they cannot assign percentages to those real-run costs. In
particular, sleep(0) alone does not explain the gap. The observed batch-size
rates also vary enough that one fixed startup cost is not an established
explanation. Repeated real measurements and a send-only profile are needed
to identify the dominant host cost; no RTL changes are justified by this.

The sender has an optional diagnostic flag around `sendp`, excluding
packet preparation, capture startup, and the capture tail. The initial
cProfile implementation also captured the sniffer thread on Python 3.12.13
and has been replaced with thread-specific `profile.Profile.runcall`:

```powershell
uv run --with scapy python software/ethernet_request_rate.py --interface "Ethernet" --count 1000 --sequence 1100000 --interval 0 --profile-send reports/ethernet_send_profile.prof --report reports/ethernet_send_profile.json
```

It prints the top fifteen cumulative-time entries and saves the complete
trace. Profiling adds overhead, so its sending rate is diagnostic rather
than a replacement for the unprofiled measurements. Cumulative times are
nested and must not be added together. This profiles the sending thread;
it does not directly profile the capture thread or FPGA. The initial real profiled run is recorded below; a corrected
thread-specific run remains for the user to perform. Offline report/trace
verification and the four rate-report unit tests passed without hardware access.

After identifying whether setup, packet processing, or injection dominates,
make only the corresponding host change. The next functional milestone is
an Ethernet backend for VariantGate, using the existing verified request/
result protocol and NumPy comparison path; the current VariantGate CLI
supports NumPy/UART comparison but does not yet support Ethernet inference.

### Initial real profile and profiler correction - October 1, 2026

The user ran 1,000 requests with profiling enabled. All 1000/1000 replies
were correct, with zero missing, duplicate, out-of-order, unrelated, or
mismatched replies. Send wall time was 0.551811 seconds (1812.2 requests/s),
which includes profiler overhead. The original evidence remains in
`reports/ethernet_send_profile.json` and `.prof`.

The trace unexpectedly contains sniffer-thread `sessions.recv`, socket
receive, and packet dissection. `sessions.recv` shows 1.017 seconds of
cumulative time in a 0.552-second run, while the outer sendp call shows only
about 0.001 seconds. This is not a valid sending-thread time breakdown.
The assistant reproduced cross-thread capture offline on the same Python
3.12.13 runtime: a cProfile instance enabled in the main thread recorded a
background worker's function. Consequently no bottleneck percentages can
be reliably assigned from this trace. The trace does confirm activity in
packet cloning, dissection, per-packet injection, and result decoding, but
does not determine their true shares or explain batch scaling quantitatively.

The optional profiler was corrected to use `profile.Profile.runcall`, which
uses Python's thread-specific sys.setprofile hook. An offline two-thread
check confirmed that background worker calls are excluded. Trace saving
and report metadata were also verified with mocked network calls; all four
rate-report tests still pass. Normal unprofiled sending is unchanged.
Reference: [Python sys.setprofile documentation](https://docs.python.org/3.12/library/sys.html#sys.setprofile).

Run a corrected diagnostic without overwriting the original trace:

```powershell
uv run --with scapy python software/ethernet_request_rate.py --interface "Ethernet" --count 1000 --sequence 1200000 --interval 0 --profile-send reports/ethernet_send_thread_profile.prof --report reports/ethernet_send_thread_profile.json
```

The pure Python profiler adds overhead too; use the table to locate sending
costs, not as a speed benchmark. It does not directly profile capture-thread
CPU time or FPGA inference. The next investigation depends on this user-run
trace; no RTL changes or hardware tests were performed by the assistant.

### Sending-thread trace and prebuilt-byte comparison - October 1, 2026

The corrected user-run trace returned 1000/1000 correct replies, with no
missing, duplicate, out-of-order, unrelated, or mismatched replies. Actual
send-call wall time was 1.1132701 seconds (898.3 requests/s), including
profiling overhead. Evidence: `reports/ethernet_send_thread_profile.json`
and `.prof`.

Unlike the first trace, this one contains no receiver functions. It shows
0.251 seconds in Scapy packet iteration (including 0.210 seconds in layer
cloning) and 0.337 seconds in the layer-2 send path (including 0.313 seconds
in the pcap injection wrapper). These nested times must not be summed with
their children. The profile's synthetic setprofile entry is 2.025 seconds
and its reported total is 2.625 seconds, exceeding the actual 1.113-second
wall time. Thus exact overhead fractions and unprofiled costs remain
unestablished; the profile is useful here to identify code paths, not as an
accurate performance budget. More profiled rates are not needed to compare
performance.

The concrete avoidable work is packet-object cloning in Scapy's generic
sender. An optional `--sender raw` now builds the same request bytes before
timing, opens one Scapy layer-2 socket, and sends those bytes directly. It
avoids the packet-object iteration/cloning and skips sleep(0) when no delay
is requested. It still calls Npcap injection for each packet, uses the same
reply capture/validation, and respects positive requested intervals. No RTL,
wire format, model, or buffering changes were made. The existing `scapy`
sender remains the default for comparison.

Seven offline tests passed, including byte order, pacing, and socket cleanup
on injection failure. The raw sender dry-run also prepared all 27,477 frames.
Both the first physical comparison and the full-set raw run passed, as
recorded below.
Run this unprofiled pair using the same 1,000-vector content:

```powershell
uv run --with scapy python software/ethernet_request_rate.py --interface "Ethernet" --sender scapy --count 1000 --sequence 1300000 --interval 0 --report reports/ethernet_sender_scapy_1000.json
uv run --with scapy python software/ethernet_request_rate.py --interface "Ethernet" --sender raw --count 1000 --sequence 1400000 --interval 0 --report reports/ethernet_sender_raw_1000.json
```

Compare send-call duration and all reply error counts, not just requests/s.
If raw sends faster but loses replies, it exposes a capacity/capture limit
and is not a complete improvement. If it passes, repeat on the full V2 set:

```powershell
uv run --with scapy python software/ethernet_request_rate.py --interface "Ethernet" --sender raw --vectors build/ethernet_v2_full_test.mem --count 27477 --sequence 1500000 --interval 0 --report reports/ethernet_sender_raw_full_v2.json
```

Repeat the unprofiled comparisons if needed to distinguish sender changes
from run-to-run host variation. After this host comparison, the functional
next step remains connecting the verified Ethernet transport to VariantGate.

### Unprofiled sender comparison - October 1, 2026

The user compared the two senders on the same 1,000 committed V2 vectors,
with no requested spacing or profiling. Both returned 1000/1000 correct
replies and zero missing, duplicate, out-of-order, unrelated, or mismatched
replies.

| Sender | Send-call time | Host requests/s | Average send-call time/request |
|---|---|---|---|
| Scapy sendp | 0.284734 s | 3512.1 | 284.7 us |
| Prebuilt bytes (`raw`) | 0.154678 s | 6465.0 | 154.7 us |

Raw achieved 1.841 times the observed sending rate (+84.1%) and reduced
send-call time by 45.7%, saving 130.1 ms per 1,000 requests in this pair.
Evidence: `reports/ethernet_sender_scapy_1000.json` and
`reports/ethernet_sender_raw_1000.json`.

This paired observation supports the host-side optimization without an
RTL change. The raw path removes both Scapy packet cloning/iteration and
zero-delay sleep calls, so this test cannot assign the improvement to
cloning alone. It does not establish a wire-rate or FPGA capacity ceiling;
receive validation also includes the two-second tail. Repeated runs would
be needed to estimate variability. The full 27,477-vector raw test command
above subsequently passed, as recorded below.

### Full locked V2 pass with the raw sender - October 1, 2026

The user sent all 27,477 locked-test variants with `--sender raw` and no
intentional spacing. All 27477/27477 replies matched their golden scores,
classification/routing flags, and sequences. Missing, duplicate, out-of-order,
unrelated, and mismatched reply counts were all zero. Evidence:
`reports/ethernet_sender_raw_full_v2.json`.

| Full-set sender | Send-call time | Host requests/s |
|---|---|---|
| Earlier Scapy sendp | 6.691177 s | 4106.5 |
| Raw prebuilt bytes | 4.121176 s | 6667.3 |

The raw run had 62.4% higher observed sending rate (1.624 times) and 38.4%
less send-call time, with average send-call time of 150.0 us/request. These
are separate unprofiled runs of the same full vector file; they do not
establish repeated-run variability, wire rate, inference latency, or the
FPGA capacity ceiling. Replies may finish within the two-second capture
tail after sending. The result completes full-set physical parity validation
for this host optimization without changing RTL or the model.

The next functional milestone is an Ethernet inference backend in
VariantGate, followed by comparison with its existing NumPy backend using
the same V2 manifest and routing policy. No further transport or RTL
optimization is required by the evidence so far.

## VariantGate Ethernet integration - October 1, 2026

VariantGate now supports `--backend ethernet` and `--backend compare-ethernet`
in `software.variantgate.cli triage`. The second mode compares every FPGA
score with the existing bit-exact NumPy backend and stops on a mismatch.
Existing NumPy, UART, and UART comparison modes remain available. Scapy is
loaded only when an Ethernet backend is selected.

`software/variantgate/backends/ethernet_backend.py` reuses the validated RX01
frame builder and result decoder. One raw sending socket and one capture
are kept open for the whole run. Capture is armed before requests begin;
each request carries a sequence number, and the backend waits for the
matching reply before scoring the next variant. Unrelated/old sequence
replies are ignored within the same timeout. Missing replies raise a timeout;
there are no automatic retries. Classification and the current firmware's
-276 routing flag are checked against the returned score.

The host applies the supplied frozen routing policy through the existing
VariantGate path. Output `scores.parquet` includes variant identity, score,
prediction, reference score in comparison mode, latency, and routing columns.
`summary.json` records comparison status and routing totals; `run_manifest.json`
records model/input hashes, interface, first sequence, and timeout. The
latency is host round-trip time including injection, capture, decoding, and
queue delivery, not isolated FPGA compute latency. This sequential API is
for workflow integration; it is not the earlier burst sender benchmark.

Offline validation passed all 41 VariantGate tests, including four new
Ethernet tests: signed feature packing and reply sequence association,
persistent resources, missing-reply timeout, invalid flags, and a mocked
end-to-end triage comparison producing scores/routing/run metadata. CLI
help also ran without Scapy installed, confirming non-Ethernet modes do
not acquire that new dependency. No packets were sent, UART ports opened,
RTL changed, or bitstreams generated by the assistant for this integration.
Physical VariantGate smoke and full-set comparisons subsequently passed,
as recorded below.

With the existing Ethernet-only FPGA design programmed, run a ten-variant
smoke comparison from the repository folder:

```powershell
uv run --with scapy --with numpy --with pandas --with pyarrow --with pyserial python -m software.variantgate.cli triage --input genomic-dataset-pipeline/data/processed/variants_model_int8.parquet --manifest reports/checkpoint_v2/export_manifest.json --backend compare-ethernet --interface "Ethernet" --routing-policy reports/checkpoint_v2/routing_policy.json --split test --limit 10 --output-root runs/ethernet_triage
```

Expected summary: status pass, variant_number 10, backend
`compare:fpga_ethernet:numpy`, bit_exact_comparison true, and
bit_exact_mismatches 0. Both deep/light routing counts are reported if those
routes occur in the selected rows; there is no requirement that every small
subset includes both. The output path is printed; each run gets a fresh
UTC timestamp directory, so repeating the command preserves earlier outputs.

If that passes, remove `--limit 10` to compare all 27,477 held-out variants
through VariantGate. An Ethernet-only workflow uses `--backend ethernet`
instead; keep comparison enabled for the first physical integration checks.
No UART connection is needed. The pyserial package remains in this command
because the existing VariantGate package also imports its UART backend.

### Physical VariantGate smoke and full-set pass - October 1, 2026

The user ran the ten-variant smoke check and then the entire locked V2 test
set through `compare-ethernet` with the frozen -276 routing policy. Both
completed successfully with zero bit-exact score mismatches.

| Metric | Smoke | Full test set |
|---|---|---|
| Variants | 10 | 27,477 |
| FPGA/NumPy score mismatches | 0 | 0 |
| Score range | -783 to 153 | -1362 to 631 |
| Predicted benign-side / pathogenic-side | 8 / 2 | 19,294 / 8,183 |
| Light / deep review | 8 / 2 | 14,023 / 13,454 |
| Mean host round-trip latency | 945.9 us | 928.8 us |
| Total backend latency | 0.009459 s | 25.521758 s |
| CLI scoring/context interval | 1.375244 s | 28.381130 s |

The full run routes 48.96% to deep review and 51.04% to light review. Its
CLI timing corresponds to about 968 variants/s, including comparison and
backend lifecycle overhead. The CLI timer excludes input loading/record
preparation and final output writing. This sequential workflow timing is
not the raw sender's 6667.3 requests/s burst measurement, and neither measures
isolated FPGA inference latency. Prediction counts describe model outputs,
not agreement with biological labels.

Saved runs:
- Smoke: `runs/ethernet_triage/20261001T071204.180766Z`
- Full: `runs/ethernet_triage/20261001T071227.501218Z`

The assistant verified the full scores file has 27,477 unique variant keys,
all scores equal their NumPy reference, all prediction and routing columns
agree with the thresholds, and scores/summary hashes match the run manifest.
The summary, provenance metadata, and offline verification result are also
preserved in `reports/ethernet_variantgate_full_v2_validation.json`.

The next step is the existing evidence workflow on a small deep-review subset.
An offline input check prepared ten requests successfully using the dataset's
`variation_id` column. No evidence-network requests were made by the assistant.
The user can run:

```powershell
uv run --with numpy --with pandas --with pyarrow python -m software.variantgate.evidence_cli collect-clinvar --scores runs/ethernet_triage/20261001T071227.501218Z/scores.parquet --variants genomic-dataset-pipeline/data/processed/variants_model_int8.parquet --routing-policy reports/checkpoint_v2/routing_policy.json --route deep_review --limit 10 --output-root runs/ethernet_evidence
```

This fetches ClinVar summaries for the ten selected variants and writes
source-linked evidence, a summary, and a run manifest in a fresh directory.
It does not need an FPGA connection. Inspect that small evidence run before
expanding collection or continuing to the existing literature/reconciliation
steps.

### Host CPU comparison - October 1, 2026

The assistant benchmarked the existing VariantGate NumPy backend offline
on the same host, V2 manifest, and 27,477 held-out variants. Five warmed
repetitions used the same score_records loop, excluding dataset loading,
record preparation, output writing, and after-timing score validation.
Every result matched the saved physical Ethernet score by variant key.
No FPGA, network, or UART calls were made. Evidence:
`reports/ethernet_vs_host_v2_benchmark.json`.

| Measurement | Host NumPy | Sequential Ethernet comparison |
|---|---|---|
| 27,477-variant scoring/context interval | Median 0.3702 s | 28.3811 s |
| Variants/s over that interval | 74,228.8 | 968.1 |
| Mean backend latency per variant | Median 10.60 us compute | 928.84 us host round trip |

Host loop times ranged from 0.3518 to 0.4641 seconds across the five runs.
The sequential Ethernet/NumPy comparison interval was 76.7 times the median
host-only loop interval. Its per-request host round-trip latency was 87.6
times the median host compute latency. These compare the current workflows,
not isolated CPU versus FPGA arithmetic: Ethernet adds packet injection,
capture, parsing, queues, startup/cleanup, and the reference NumPy evaluation.
FPGA compute-only latency has not been measured.

For this small 16-8-1 network, the current host NumPy implementation is
substantially faster than offloading each record and waiting for its reply.
The FPGA path provides verified hardware inference, but these results do
not demonstrate an end-to-end speedup. The raw full-set test reached 6667.3
host send requests/s, also below the host-only loop's measured rate; its
send window and capture tail differ from sequential round-trip timing.
Batching NumPy calculations could further improve the CPU baseline, but
was not measured here.

## Combined UART/Ethernet baseline

`rtl/variant_triage_top_UART_ethernet.sv` now accepts the same `RX01` request
verified in the standalone RX board test. Its path is:

`RMII RX -> CRC-checked frame -> RX01 parser -> 160-bit mailbox -> classifier -> 64-bit mailbox -> Ethernet TX`

The input mailbox carries the 32-bit sequence and all sixteen feature bytes
together from the receive clock to the 100 MHz classifier clock. The output
mailbox carries the sequence and signed 32-bit score to the transmit clock.
Ethernet responses echo the request sequence. UART requests retain a separate
counter beginning at zero; both inputs produce the existing UART score reply
and an Ethernet result frame.

The classifier launches one inference at a time, after reserving capacity
for both outputs. A pending Ethernet request waits until the classifier and
outputs are available. UART has priority if a UART packet and an Ethernet
request arrive together. UART has no request backpressure: send one request
and wait for its response before sending another. Ethernet storage is also
finite; this remains a sequential request/response test, not a lossless stream
under arbitrary traffic.

## Clocks and board build

The combined top requires sixteen features and a three-output Clocking Wizard:

| Output | Clock | Purpose |
|---|---|---|
| `clk_out1` | 50 MHz, 0Â° | PHY reference, forwarded by ODDR |
| `clk_out2` | 50 MHz, 180Â° | RMII TX |
| `clk_out3` | 50 MHz, 36Â° | RMII RX |

`scripts/build_variant_triage_ethernet.tcl` creates the dedicated project in
`build/ethernet_classifier`, configures those clocks, includes the exported
model memories, and uses `constraints/variant_triage_ethernet.xdc`. It retains
the RX input timing checks and adds the TX output delays. The constraints
also define the board's 100 MHz clock and relate the clock-IP checkpoint's
input clock back to that source. The RX-only design did not exercise the
100 MHz classifier; omitting this definition left those registers untimed.
Leaving the checkpoint's input as an independent clock produced incorrect
mailbox timing checks. The combined build checks for unclocked registers
and keeps the clock domains related rather than excluding internal paths.
It generates the
bitstream only after routed setup and hold timing pass:

```powershell
vivado -mode batch -source scripts/build_variant_triage_ethernet.tcl
```

Program `build/ethernet_classifier/ethernet_classifier.bit`. This build has no
ILA, so clear the debug-probes selection when programming it. The standalone
`build/ethernet_RX_ila` bitstream and probes remain available for RX diagnosis.
Do not combine constraints or reuse the two-output RX-only clock IP with this
top.

These LEDs differ from the standalone RX top:

| LED | Meaning |
|---|---|
| 0 | Clocking Wizard locked |
| 1 | PHY reset released |
| 2 | Toggles after each completed Ethernet result transmission |
| 3 | Sticky TX buffer overflow or TX underflow |

Wait about three seconds after PHY reset release before testing, allowing the
existing TX startup delay to complete.

## Physical request/response check

Start the result capture in one terminal before sending the request:

```powershell
uv run --with scapy python software/decode_ethernet_result.py --interface "Ethernet" --count 1
```

In another terminal send the already verified feature vector, `-8` through `7`:

```powershell
uv run --with scapy python software/send_ethernet_RX_test.py --iface "Ethernet" --sequence 1 --count 1
```

Use the actual Scapy interface name. Expect a broadcast result from
`02:00:00:00:00:01`, EtherType `0x88B5`, version 1, result type 1, and
sequence **1**. Its signed score is followed by the classification
(`score >= 0`) and deep-review flag (`score >= -276`), then reserved zeros
and Ethernet padding. With the currently exported V2 weights, the `-8`
through `7` vector produces score **-124**, classification **0**, and
deep-review flag **1**; the end-to-end simulation checks this exact vector
too. The existing decoder supports this response format.
Repeat with sequence 2 after receiving the first response. Record the actual
scores and LED behavior. The first successful physical result is recorded below.

## Physical result â€” September 30, 2026

The user captured this result on the programmed board using the decoder command
above and the known `RX01` request with features -8 through 7:

```text
sequence=         1  score=  -124  classification=benign-side       routing=deep review   [first captured packet]
```

All reported fields match the expected result: request sequence 1, signed
score -124, classification 0 (score below zero), and deep-review flag 1
(score at least -276). This verifies one physical request through RMII RX,
the parser, the request mailbox, the classifier, the result mailbox, and
RMII TX back to the PC.

The decoder printed a warning that `Ethernet` was not found in Scapy's
interface list, but capture succeeded on that name. The warning did not
prevent this test. Combined-build LED states were not reported for this run.

### Repeated requests: 10/10 passed

The user then ran the decoder with `--count 10` and sent requests beginning
at sequence 2 with `--count 10`, using the sender's default one-second interval.
The captured output contained every sequence from **2 through 11**, in order.
Every response reported score **-124**, `benign-side`, and `deep review`.
The decoder marked sequences 3 through 11 as continuous; no sequence gaps
or duplicates appeared in the ten captured responses.

This verifies 10/10 request/response completion for this vector and send rate
on the physical board. It does not establish maximum throughput. The
different-vector and UART comparison below subsequently passed too.

### Different vectors and UART parity: 20/20 passed

The combined board remained programmed throughout this test; no bitstream
was generated. The USB serial port was COM8 and the network interface was
`Ethernet`. The first ten committed V2 vectors were sent through each input
using:

```powershell
uv run --with scapy --with pyserial python software/FPGA_UART_ethernet_validation.py --port COM8 --interface "Ethernet" --request-path both --count 10
```

For each vector the validator sends a UART request and checks its UART and
Ethernet replies, then sends an `RX01` Ethernet request with the same features
and checks both replies again. Every score is compared with the signed golden
score saved in `simulation/model_v2_h8_core_vectors.mem`. It also checks
Ethernet classification/routing flags, UART-request sequence continuity, and
exact sequence echo for Ethernet requests.

| Vector | Golden score | UART-request sequence | Ethernet-request sequence | Both reply scores for both inputs |
|---|---|---|---|---|
| 1 | 216 | 0 | 1000 | Matched |
| 2 | 326 | 1 | 1001 | Matched |
| 3 | -484 | 2 | 1002 | Matched |
| 4 | -211 | 3 | 1003 | Matched |
| 5 | -500 | 4 | 1004 | Matched |
| 6 | 176 | 5 | 1005 | Matched |
| 7 | -538 | 6 | 1006 | Matched |
| 8 | -402 | 7 | 1007 | Matched |
| 9 | -464 | 8 | 1008 | Matched |
| 10 | -202 | 9 | 1009 | Matched |

The validator printed `PASS: 20/20 requests matched the committed V2 vectors`.
The sample covers positive classification, benign-side/deep-review results,
and benign-side/light-review results. The subsequent full-vector test below
extends this physical parity evidence. The comparison test sends each request
only after receiving the previous request's responses.

`--request-path uart` retains the previous validator behavior; `ethernet`
tests only Ethernet input, and `both` tests both inputs. `--dry-run --count 10`
prints features, expected scores/flags, and request bytes without opening
hardware. The UART port and interface arguments are unnecessary for dry-run.

### Full saved-vector comparison â€” October 1, 2026

The user ran the comparison for all 1,000 committed V2 vectors through both
inputs and reported:

```text
PASS: 2000/2000 requests matched the committed V2 vectors
```

Reproduce the test with:

```powershell
uv run --with scapy --with pyserial python software/FPGA_UART_ethernet_validation.py --port COM8 --interface "Ethernet" --request-path both --count 1000
```

This verifies 1,000 UART requests and 1,000 Ethernet requests on the physical
combined design. Both reply scores matched the golden value for every request,
and the validator's Ethernet flags and sequence checks passed. It covers the
committed test-vector file, not the complete genomic dataset. Sequential
response waiting means this is correctness/parity evidence rather than a
maximum-throughput measurement.

## Request pacing and loss check â€” prepared October 1, 2026

`software/ethernet_request_rate.py` sends a batch without waiting for each
reply. It compares replies with the same saved golden vectors, counts unique
responses, and reports missing sequences, duplicates, ordering errors, and
score/flag mismatches. It needs no UART port and does not generate or program
a bitstream. Both Scapy and pyserial are included in the commands because it
reuses the vector loader from the existing comparison utility.

Run these separately, waiting for each command to finish. The existing
programmed combined top is sufficient. Begin with 100 requests spaced by
10 ms (nominally 100 requests/s):

```powershell
uv run --with scapy --with pyserial python software/ethernet_request_rate.py --interface "Ethernet" --count 100 --sequence 100000 --interval 0.01 --report reports/ethernet_rate_10ms.json
```

Then reduce the requested spacing to 1 ms (nominally 1,000 requests/s):

```powershell
uv run --with scapy --with pyserial python software/ethernet_request_rate.py --interface "Ethernet" --count 100 --sequence 200000 --interval 0.001 --report reports/ethernet_rate_1ms.json
```

Finally send with no deliberate spacing:

```powershell
uv run --with scapy --with pyserial python software/ethernet_request_rate.py --interface "Ethernet" --count 100 --sequence 300000 --interval 0 --report reports/ethernet_rate_burst.json
```

All three use the first 100 saved vectors. Each command includes a
two-second capture tail after sending to allow outstanding responses to arrive.
`--timeout` changes that tail. Distinct sequence ranges distinguish the runs.
JSON reports record the requested interval, send-call duration, measured host
send-call rate, and response checks. Exit status is nonzero if a response is
missing, duplicated, out of order, or mismatched.

For a loss-free run, expect `Received 100/100; correct 100; missing 0;
duplicates 0; out of order 0; mismatches 0`. At higher rates, missing replies
can reveal the capacity limit. Any score or flag mismatch is a correctness
failure to investigate separately from dropped requests.

Scapy and Windows scheduling affect actual pacing. The printed rate is
`count / send-call duration`, not a measured Ethernet wire rate or a maximum
FPGA throughput claim. A zero interval sends as quickly as Scapy can inject
the frames, without guaranteeing back-to-back wire traffic.

The combined top still emits a UART reply for every inference and waits for
UART response capacity before launching another inference. Five bytes at
115,200 baud already take about 434 microseconds on the UART wire, so this
comparison build includes a UART pacing limit as well as finite Ethernet
request buffers. A loss test does not isolate classifier speed.

Missing responses cannot by themselves identify FPGA RX overflow: they may
also reflect host injection/capture loss or the response deadline. The current
combined LED[3] reports TX buffer overflow or underflow, not `receiver`
overflow. Direct RX overflow confirmation would require probing `rx_overflow`
in an ILA or adding a counter, followed by a user-built instrumented design.
No RTL instrumentation or bitstream build is needed for this first host test.

Offline verification passed four response-summary tests, and dry-run produced
100 correctly sized request frames. The user subsequently ran the three physical
pacing tests recorded below. To reproduce the offline checks:

```powershell
uv run --with scapy --with pyserial python -m unittest discover -s software/tests -p test_ethernet_request_rate.py
uv run --with scapy --with pyserial python software/ethernet_request_rate.py --dry-run --count 100 --interval 0.01
```

### Physical pacing results â€” October 1, 2026

The user ran the three commands above. Their saved JSON reports agree with the
reported console summaries:

| Requested interval | Host send-call duration | Host send-call rate | Correct unique replies | Missing |
|---|---|---|---|---|
| 10 ms | 1.090 s | 91.8 requests/s | 100/100 | 0 |
| 1 ms | 0.156 s | 639.0 requests/s | 100/100 | 0 |
| No deliberate spacing | 0.044 s | 2270.3 requests/s | 68/100 | 32 |

Every run had zero duplicates, ordering errors, and score/flag mismatches.
The burst lost 32 responses (32% of requests); all 68 captured responses were
correct. The missing sequence numbers are preserved in
`reports/ethernet_rate_burst.json`. The other evidence files are
`reports/ethernet_rate_10ms.json` and `reports/ethernet_rate_1ms.json`.

These tests demonstrate no observed reply loss in the two paced 100-request
runs, and reply loss in the faster burst. They do not identify an exact safe
rate or prove that the missing responses were FPGA RX overflows.

The burst's host send-call rate is near the UART wire limit of roughly
2,304 five-byte replies/s, before controller overhead. The top gates every
inference on `!response_busy` and sends a UART reply even for Ethernet-origin
requests. UART pacing and finite receive buffers are therefore a plausible
explanation for burst loss, not a directly measured cause. A simple next
change to evaluate is to emit UART replies only for UART-origin requests,
then repeat the same pacing checks with a user-built bitstream. This would
require updating the validator's Ethernet-input expectations, which currently
require both replies. No such RTL change has been made for these results.

## Simulation evidence

Run the real classifier with the exported V2 weights and golden vectors:

```powershell
vivado -mode batch -source scripts/test_variant_triage_ethernet.tcl
```

`simulation/variant_triage_ethernet_tb.sv` sends complete RMII frames with
leading carrier zeros, preamble, and FCS. It checks that ARP and a corrupt-FCS
request do not launch inference. Two valid requests exercise both clock
crossings, sequence preservation, and scores against saved golden vectors.
The test decodes the actual RMII response through a CRC-checking receiver
and checks its MAC header, protocol, sequence, score, flags, padding, and length.
It also sends a real UART packet for the same first vector, checks the actual
UART output bytes for every inference, and tests the board's `-8` through `7`
vector with expected score -124.
Clock and ODDR behavior are modeled in the testbench; physical clock timing
is checked separately by Vivado implementation.

For the multiplier mapping and synchronizer attribute placement encountered
during implementation, see [FPGA synthesis notes](fpga_synthesis.md).

## Implementation results â€” September 30, 2026

| Check | Result |
|---|---|
| End-to-end XSim | Passed: three Ethernet requests and one UART request; four matching UART and Ethernet responses |
| Ignored traffic | ARP and corrupt-FCS requests launched no inference |
| Board test vector | Features -8 through 7 returned score -124 in simulation and on the physical board |
| Repeated physical requests | 10/10 responses, sequences 2â€“11, expected score and flags, one-second send interval |
| Physical different-vector/UART parity | 20/20 requests across both inputs; UART and Ethernet replies matched ten golden scores |
| Full physical vector comparison | 2000/2000 requests; all 1,000 committed V2 vectors through both inputs |
| Physical pacing | 100/100 at observed send-call rates 91.8 and 639.0 requests/s; 68/100 in the 2270.3 requests/s burst; no score/flag mismatches |
| Mailbox regression | Reset, held data, and backpressure tests passed |
| Routed setup slack | +0.830 ns; zero failing endpoints |
| Routed hold slack | +0.068 ns; zero failing endpoints |
| RMII input setup / hold | +1.178 ns / +0.968 ns |
| Register clock coverage | Zero unclocked registers and zero unconstrained internal endpoints |
| CDC report | Zero unsafe or unknown crossings, zero missing ASYNC_REG entries |
| DSP mapping | Five DSP48E1 blocks |
| DRC | Zero errors; DSP pipelining/asynchronous-load and RAM asynchronous-control warnings remain |
| Bitstream | `build/ethernet_classifier/ethernet_classifier.bit` generated successfully |

Reports are saved beside the bitstream as `timing_summary.rpt`,
`synthesis_utilization.rpt`, `cdc.rpt`, and `drc.rpt`. These results establish
simulation and implementation readiness. The physical result above additionally
verifies a complete combined request/response, ten repeated requests, and
the full 2,000-request comparison against all 1,000 saved golden vectors.
The user-run pacing checks above additionally establish the observed burst
loss. Maximum sustained throughput and direct RX overflow counts remain unmeasured.
