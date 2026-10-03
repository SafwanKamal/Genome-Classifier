# Preserved baseline and streamed V4 experiment — October 2, 2026

## Theoretical limits for this board and workload

The project targets the XC7A100T-CSG324-1, which has 240 DSP slices,
63,400 LUTs and 135 36-Kbit BRAM tiles. The same FPGA is listed in
[Digilent's Nexys A7-100T specifications](https://digilent.com/shop/nexys-a7-amd-artix-7-fpga-trainer-board-recommended-for-ece-curriculum/).
There is no single throughput maximum independent of the model and protocol.
These calculations use the existing V4 `16 → 256 → 256 → 1` model:

`MACs/variant = 16×256 + 256×256 + 256 = 69,888`.

Assuming one useful INT8 multiply-accumulate per DSP per clock, all 240 DSPs
busy continuously, and no scheduling overhead:

| Hypothetical core clock | Arithmetic ceiling, variants/s |
|---|---:|
| 100 MHz | 343,407 |
| 150 MHz | 515,110 |
| 200 MHz | 686,813 |

Formula: `240 × clock_Hz / 69,888`. These are unpacked arithmetic budgets,
not achieved rates or guaranteed clock frequencies. They exclude padding of
unused lanes, pipeline drains, quantization, mailboxes, and host overhead.
DSP packing would change the assumption. LUTs and memory bandwidth also limit
what can be implemented. Neither 150 nor 200 MHz has been validated here.

With the current maximum batch of 32, a request is 536 bytes before FCS.
Adding 4 bytes FCS, 8 bytes preamble/SFD and 12 byte-times inter-frame gap gives
560 byte-times. At the verified 100 Mb/s link:

`32 × 100,000,000 / (560 × 8) = 714,286 variants/s`.

A full reply is 214 bytes before FCS, or 238 byte-times, so its corresponding
TX ceiling is 1,680,672 variants/s. Under full-duplex operation, RX is the
wire bottleneck. The roughly 714k figure is a ceiling for this 32-record
protocol, not a universal board maximum. Smaller batches reduce efficiency.

The measured optimized host V4 baseline is about 434,605 variants/s
(`reports/model_v4_host_test.json`). Even perfect use of all 240 DSPs at
100 MHz cannot match that. Ideal arithmetic parity needs about 126.6 MHz
with all 240 DSPs; a practical implementation needs additional headroom.

## What changed

The trained V4 model, quantization, routing threshold -1833, wire protocol,
100 MHz core clock, PHY clock phases 0/180/36 degrees, and existing XDC remain
the same. No training, parameter repacking, clock increase or lane increase
was performed. All implementation alternatives are new files:

| New file | Purpose |
|---|---|
| `rtl/batch_model_stream_core.sv` | Issue a packed weight word every clock; overlap synchronous ROM read, registered DSP multiplication and accumulation |
| `rtl/ethernet_RX_overlap.sv` | Two frame banks allow CRC-checked delivery while the next frame arrives |
| `rtl/ethernet_batch_request_overlap.sv` | Two feature banks allow parsing a batch while another batch feeds the core |
| `rtl/ethernet_batch_result_overlap.sv` | Two score banks allow collecting results while an earlier reply transmits |
| `rtl/variant_triage_top_ethernet_stream.sv` | Separate Ethernet-only top connecting the new modules to the existing mailboxes and TX stack |
| `software/ethernet_batch_stream_rate.py` | Raw-socket validator with one or two outstanding batches; reports completed throughput |
| `scripts/build_model_v4_stream.tcl` | Separate user-run Vivado project and bitstream output directory |
| `simulation/batch_model_stream_core_tb.sv`, `simulation/ethernet_batch_stream_tb.sv` | Golden-score and overlapping-frame integration tests |
| `scripts/test_batch_model_stream_core.tcl`, `scripts/test_ethernet_batch_stream.tcl` | Run the new XSim tests |
| `scripts/synthesize_batch_model_stream_core.tcl` | Core-only synthesis/resource/timing check at 100 MHz |
| `software/tests/test_ethernet_batch_stream.py` | Offline validation of window credits, partial batches, reordered/duplicate/missing replies and incorrect results |

The original `batch_model_core.sv`, `dense_engine.sv`, Ethernet top, receiver,
batch parser/reply modules, host batch utility and build scripts are preserved.
Nine baseline files were hashed before the changes and checked afterward;
see `reports/streamed_baseline_hashes.json` and
`reports/model_v4_stream_validation.json`. The original build remains usable.
README adds a link to this separate experiment. The earlier V4 report's MAC
total is corrected from 67,328 to 69,888 (about 16.1× V3's work); this corrects
documentation only, with no baseline RTL or model changes.

The stream carries first/last/group tags alongside each ROM word and its
registered products. The first word initializes accumulation from the bias;
the last word stores the sum including that word. Layers drain before
requantization, preserving the original signed arithmetic and rounding.
The existing `use_dsp` placement on each registered product is retained.
Existing mailbox synchronizers and their `ASYNC_REG` declarations are reused.

Two banks at each transport stage provide bounded overlap. This is not an
unlimited request queue: the host defaults to two outstanding batches and
returns one credit when the corresponding reply arrives. Window 1 provides
the stop-and-wait comparison. The classifier still computes one variant at
a time; transport overlap does not imply concurrent inference jobs.

## Verification and measured change

### Physical correctness and throughput — October 3

After the missing memories were added and the design rebuilt/programmed,
`reports/model_v4_memory_fix_smoke.json` passed 32/32 exact results in
1.1855 ms (26,993 variants/s, one batch). The two-outstanding-batch test
`reports/model_v4_stream_window_2.json` passed 1,000/1,000 in 18.3348 ms
(54,541 variants/s). The full held-out test
`reports/model_v4_stream_full.json` passed 27,477/27,477 in 0.5300598 s
(51,838 variants/s), with zero missing, duplicate or mismatched results.
That is about 8.38× slower than the prepared-feature host baseline; these
physical results establish correctness but no accelerator throughput advantage.

To investigate the gap, the integration simulation now measures start-to-start
spacing for the first 64 queued variants: **628 clocks for every gap**.
At 100 MHz, that is 6.28 us/variant or 159,236 variants/s before host/network
batch starvation. The core takes 617 clocks; the observed mailbox/control
gap adds 11 clocks (1.78%). It cannot account for the approximately threefold
gap between queued simulation capacity and physical completed throughput.
This is simulation evidence, not a physical FPGA cycle measurement.

The host script now offers optional `--timing`, adding aggregate sample counts,
total durations, medians, p95 and maxima for send calls, queue waits,
send-to-reply latency, reply-to-dequeue delays and reply-to-refill delays.
It also records send/reply spacing and callback decoding time. Queue waits
include FPGA/network/capture delay; they cannot isolate those components.
Callback decoding excludes Scapy's packet dissection before the callback.
Latency and callback totals overlap the main-thread interval, so they must
not be added to send/wait totals. Other main-thread time includes orchestration,
validation, scheduling and timing instrumentation. The default path retains
the same sending window and packet format. No working RTL changed.

The diagnostic test suite passes 18 offline tests, including timing sample
counts and timeout behavior; the 75-result RTL integration test still passes.
Run from the repository root (no FPGA rebuild):

```powershell
uv run --with scapy --with numpy --with pandas --with pyarrow python software/ethernet_batch_stream_rate.py --interface "Ethernet" --count 27477 --batch-size 32 --window 2 --sequence 6300000 --timing --report reports/model_v4_stream_timing.json
```

Compare this with the uninstrumented 51,838/s result; timing instrumentation
can affect throughput. Large send-call totals point toward host injection;
large reply/refill gaps point toward scheduling/capture/window refill costs.
Mostly waiting with small refill gaps still requires physical FPGA or capture
timestamps to distinguish compute, wire and driver delays. A larger request
window is not enabled because the hardware buffers remain bounded.

The physical diagnostic run `reports/model_v4_stream_timing.json` passed
27,477/27,477 with zero missing, duplicate or mismatched results in 0.4829493 s
(56,894 variants/s). This is 9.75% above the earlier full-set run; one comparison
does not prove a performance change. Of the 0.4830695 s observed main-thread
loop, sends account for 78.46 ms (16.2%), queue waits 392.86 ms (81.3%), and
other main-thread work 11.75 ms (2.4%). Median batch send-to-reply latency is
1,006 us, callback decode 20.1 us and reply-to-refill 118.6 us. Callback and
latency totals overlap the main-thread measurements.

Mean batch send-to-reply is approximately 1,014.7 us; mean reply-to-refill is
108.8 us. A two-batch window with roughly 1.1235 ms per credit cycle sustains
about `64 / 0.0011235 = 57k variants/s`, consistent with the observed result.
The dominant observed cost is waiting for replies/credits, not host score
validation. Queued FPGA batch computation is about `32 × 6.28 us = 200.96 us`
in simulation. The unexplained latency cannot yet be assigned to FPGA idle
time, network/driver buffering or capture delivery. The next measurement is
the native packet capture timestamp versus callback entry, to assess capture
delivery delay; direct hardware timestamps would be needed for FPGA idle time.

Capture delivery diagnostics have now been added to `--timing`. The JSON
includes `host_timing.capture_to_callback` and `host_timing.capture_spacing`.
The former compares the packet's native capture timestamp (`packet.time`)
against `time.time()` at callback entry; both use epoch seconds. This initial
implementation is superseded by the precise Windows clock fix below;
`time.time()` was too coarse on the user's Python 3.12 installation.
Existing send/reply intervals continue to use `perf_counter()`. Capture delivery includes
driver buffering, thread scheduling and Scapy processing before the callback,
but excludes subsequent batch decoding. Timestamp location depends on the
capture driver, so this does not measure FPGA or physical wire latency.
Negative samples are retained and counted rather than hidden; clock mismatch
or a wall-clock adjustment can invalidate the interpretation. Missing/invalid
timestamps are counted separately. Twenty offline software tests pass.

No hardware change or rebuild is needed. Run from the repository folder:

```powershell
uv run --with scapy --with numpy --with pandas --with pyarrow python software/ethernet_batch_stream_rate.py --interface "Ethernet" --count 27477 --batch-size 32 --window 2 --sequence 6400000 --timing --report reports/model_v4_capture_timing.json
```

If capture-to-callback accounts for hundreds of microseconds, capture delivery
is a substantial contributor. If it is small, investigate the remaining time
before capture using physical FPGA/capture evidence. These added measurements
do not change request pacing or the two-batch limit.

The first capture diagnostic run `reports/model_v4_capture_timing.json` passed
27,477/27,477 in 0.4782478 s (57,453 variants/s), but 825/859 capture-delay
samples were negative (median -1,567 us). These capture-delay values are invalid
for attribution. The installed Python 3.12.13 reports `time.time()` using
`GetSystemTimeAsFileTime` at 15.625 ms resolution. Python documents switching
to the precise Windows API starting in
[3.13](https://docs.python.org/3.13/library/time.html#time.time).
The tool now calls `GetSystemTimePreciseAsFileTime` directly on Windows,
converting FILETIME to epoch seconds before comparison with `packet.time`.
This avoids changing the Python environment. The JSON records the selected
clock as `capture_callback_clock`; negative samples remain visible if driver
clock disagreement persists. Twenty-one offline tests pass, including the
FILETIME epoch/fraction conversion. Throughput uses `perf_counter` throughout
and is unaffected by the coarse wall-clock issue. Rerun with sequence 6500000
and report `reports/model_v4_capture_precise_timing.json`; no rebuild is needed.

The precise-clock rerun passed 27,477/27,477 in 0.4688534 s (58,605 variants/s),
with zero missing, duplicate or mismatched results. All 859 capture timestamps
were present and none produced negative delays. Capture-to-callback median
was 123.5 us, p95 230.1 us, and maximum 405.8 us. Mean delivery delay was
130.9 us against mean send-to-decoded-reply latency 987.2 us: about 13.3%,
not most of the observed latency. This does not make the remaining delay an
FPGA measurement; send injection, earlier driver/NIC buffering, wire/FPGA
execution and request queueing are still included. Callback decoding median
was 18.0 us and reply-to-refill median 109.3 us.

A read-only adapter check identifies the active interface as a
`Realtek USB FE Family Controller` at 100 Mb/s. USB/NIC/driver scheduling is
a remaining hypothesis to investigate, not an established cause. No adapter
settings were changed. The next useful separation is send-to-native-capture
timing paired per batch, or physical inference-start spacing under load;
the capture-delivery measurement alone cannot explain the throughput gap.

### Second desktop comparison — October 3

The user supplied a report from a separate desktop, saved without overwriting
the original as `reports/model_v4_capture_precise_desktop.json`. It passes
27,477/27,477 with zero missing, duplicate or mismatched results, using the
same V4 manifest, batch size 32 and window 2.

| Measure | Original host | Second desktop |
|---|---:|---:|
| Completed variants/s | 58,605 | 127,499 |
| Full-set time | 468.85 ms | 215.51 ms |
| Median send-to-reply | 979.4 us | 487.1 us |
| Median capture-to-callback | 123.5 us | 42.2 us |
| Median reply-to-refill | 109.3 us | 14.2 us |
| Median send call | 81.2 us | 57.4 us |

Throughput improves 2.176× (117.6%); elapsed time falls 54.0%. Both capture
measurements have no negative or missing timestamps. Assuming the same FPGA
bitstream and link configuration, this demonstrates a substantial limitation
in the original host/interface path. It does not isolate the USB adapter,
driver, CPU or scheduler individually because the whole desktop changed.
Negotiating 100 Mb/s establishes the rate of bits on the Ethernet link;
it does not guarantee equal application completion latency on different
interfaces. NIC/driver buffering, USB transfer scheduling on the original
adapter, Windows thread wakeups and Python/Npcap processing can differ.
Interrupt moderation trades fewer CPU interrupts for additional response
latency, as explained in
[Microsoft's adapter performance guidance](https://learn.microsoft.com/en-us/windows-hardware/drivers/network/performance-in-network-adapters).
These are possible mechanisms, not identified individual causes in these runs.
With only two batches outstanding, delayed host delivery or refill can leave
the FPGA without queued work even when the link can transmit faster.
To isolate the interface, compare two adapters on the same host using the
same FPGA bitstream, model, batch/window and capture software, with repeated
runs. Comparing different PCs changes CPU and scheduling as well as interface.
The second result is about 80.1% of simulated queued capacity (159,236/s).
The 434,605/s CPU baseline was measured on the original host; a fair desktop
comparison needs that desktop's own CPU benchmark. Current 128-DSP/100 MHz
queued FPGA capacity still falls below the original CPU baseline, so host
improvements alone cannot establish parity. Repeat desktop runs, benchmark
its CPU, then consider increasing useful DSP throughput/core clock.

The standalone CPU benchmark for the second desktop is now available as
`software/benchmark_v4_cpu.py`; see [CPU benchmark commands and timing scope](benchmark_v4_cpu.md).
It needs only the synced manifest and feature Parquet file, and can compare
its median host throughput directly with that desktop's FPGA JSON.

The second-desktop CPU result is now available in
`reports/model_v4_cpu_desktop.json`: all scores exact, best median 768,944/s
at batch 64/four BLAS threads, versus FPGA 127,499/s (CPU 6.031× faster).
See [the measured comparison](benchmark_v4_cpu.md#second-desktop-result--october-3).
This supersedes the original-host 434,605/s figure as the parity target on
that desktop. The current 32-record wire ceiling (714,286/s) is below this
target, so increasing core clock/DSP utilization alone cannot establish
end-to-end parity with this protocol. No FPGA or model files changed.

| Check | Result |
|---|---|
| New core XSim | 1,000 real V4 golden vectors match exactly; 617 clocks per inference |
| Ethernet integration XSim | 75 exact scores in batches 32, 32, 3, 1, 7; two back-to-back full requests, partial batches and bank reuse |
| Invalid request integration checks | Bad CRC, wrong marker, truncated batch and zero count do not start inference |
| Offline host/protocol tests | 17 pass, including four new window tests and their subcases |
| Existing VariantGate tests | 42 pass; external/hardware accesses mocked |
| Full-set host dry run | 27,477 variants prepare 859 request frames; no interface opened |
| Core-only synthesis | 128 DSPs, 22,653 LUTs, 11,625 registers, 16.5 BRAM tiles |
| Core-only synthesized timing | 100 MHz; setup slack +2.162 ns, hold slack +0.152 ns; no failing internal endpoints |

Original core: 1,844 clocks, 18.44 us, compute ceiling 54,230 variants/s.
New core: 617 clocks, 6.17 us, compute ceiling 162,075 variants/s.
This is a **2.989× cycle reduction**, using the same 128 DSPs and model.
The weight schedule issues 32 + 512 + 64 = 608 words; nine additional clocks
account for layer drains, quantization and completion. The scalar output layer
uses only part of the packed word, so not every DSP performs useful work.

These rates exclude per-job control/CDC gaps and host/network costs.
The new compute ceiling remains 2.68× below the measured optimized host rate.
It establishes a pipeline improvement, not end-to-end FPGA parity or speedup.
The synthesized core uses more LUTs than the original (22,653 versus 18,960).
Full-top placement, routing and timing reports have not been reviewed here.
Physical throughput was initially unverified; the October 3 results above
supersede that status.
Core-only external ports have no I/O delays; this report checks internal paths,
not board-level timing closure. No bitstream was generated and no physical
packets were sent by the assistant; the physical runs above were user-run.

## Build and physical test commands

The same-PC adapter experiment is deferred. Planned core-clock/DSP/transport
improvements and the energy measurement method are documented separately in
[throughput and energy next steps](accelerator_next_steps.md). No energy
efficiency advantage has been measured or claimed.

### Physical failure diagnosed October 3

The user-run `reports/model_v4_batch_32.json` received 1,000/1,000 results,
but every returned score was zero: correct 0, missing 0, and 1,000 mismatches.
There is no successful throughput figure for this run. The manual project's
`build/Genome_Classification/Genome_Classification.runs/synth_1/runme.log`
confirms the correct streamed top was synthesized, but contains two
`Synth 8-4445` critical warnings: `$readmemh` could not open
`batch_weights.mem` or `batch_biases.mem`. Neither file appears in that
project's XPR source list. The trained files exist in the repository.
The core was therefore built without the model initialization; transport
delivered replies, but their scores do not establish valid inference.

The shortened manual-source instructions omitted these memories assuming
they were already included. They must be added as Design Sources even though
they are not SystemVerilog files. In the open manual project's Tcl Console:

```tcl
set repo {C:/Users/user/OneDrive - Texas Tech University/Desktop/Code/FPGA Project/Genome Classifier}
add_files -norecurse [list \
    "$repo/genomic-dataset-pipeline/artifacts/model_v4_batch_seed_7/memory/batch_weights.mem" \
    "$repo/genomic-dataset-pipeline/artifacts/model_v4_batch_seed_7/memory/batch_biases.mem"]
update_compile_order -fileset sources_1
```

Reset synthesis and rerun Generate Bitstream, then program the newly generated
bitstream. Before programming, confirm synthesis logs show successful reading
of both memory files, with no `Synth 8-4445` for either. Rerun a small correctness
test before the full set. Physical correctness after this setup fix is pending.
The separate scripted build already includes both files and is unchanged.

The new build explicitly sets `general.maxThreads=4` in both the main Vivado
session and synthesis/implementation workers using
`scripts/vivado_build_threads.tcl`. `launch_runs -jobs 1` keeps independent
build processes sequential; `-jobs` does not set the threads within a run.
This machine has a Ryzen 7 8845HS (8 cores/16 logical processors), about
13.8 GiB usable physical memory, and had only about 1.7 GiB free when checked.
Vivado allocates RAM as needed; there is no RAM allocation increase in this
change. Closing unused applications and extra Vivado sessions can provide
headroom. Four threads are a conservative increase from the observed default
of two; no build-time improvement has yet been measured.
Vivado 2025.2 accepted the four-thread setting and both worker-run hooks in
a configuration-only check; no synthesis or bitstream build was launched.
See AMD's [multithreading documentation](https://docs.amd.com/r/2025.1-English/ug904-vivado-implementation/Multithreading-with-the-Vivado-Tools)
and [parallel-run documentation](https://docs.amd.com/r/2025.1-English/ug904-vivado-implementation/Parallel-Runs)
for the distinction between worker threads and simultaneous runs.

The previous baseline V4 build in `vivado.log` ran from 01:23:03 to 01:43:42
on October 2 (20 minutes 39 seconds). Main synthesis took 5:27, placement 4:04,
and routing 3:44. IP generation, optimization, initialization and reporting
account for the remaining time. The script recreates a project and runs a
complete flow each time, so it does not resume the earlier run. Memory paging
and OneDrive overhead were not measured and cannot be assigned a cause.
That baseline run finished routing but failed its final timing check at
-4.025 ns setup slack (eight failing endpoints); it did not write a bitstream.
These timing results concern the old V4 batch top, not the new streamed top.

Inspection of that report identifies the worst path from
`batch_reply.builder/index_reg[1]_replica` to
`frame_buffer/frame_memory_reg/DIADI[0]`, on the 50 MHz TX clock. The data path
takes 23.624 ns against a 20 ns cycle and contains 39 logic levels. The original
reply formatter derives the score-record and byte-field indices using division
and modulo by six from a 32-bit integer index. The new overlap formatter now
uses registered 5-bit record and 3-bit field counters, advanced only on an
accepted output byte. This removes those operations from the byte-selection
path without changing the wire format or original formatter. Full-top routed
timing must still confirm closure after this change; extra CPU threads cannot
correct an FPGA timing violation.
The Ethernet integration simulation was rerun after the counter change and
again passed all 75 exact scores, reply flags, sequences and CRC checks.

New top: **`variant_triage_top_ethernet_stream`**, in
`rtl/variant_triage_top_ethernet_stream.sv`. The script selects batch mode and
the unchanged V4 memories. It uses `constraints/variant_triage_ethernet_only.xdc`.
Run from the repository root when you are ready to generate the bitstream:

```powershell
& 'C:/AMDDesignTools/2025.2/Vivado/bin/vivado.bat' -mode batch -source scripts/build_model_v4_stream.tcl
```

After routed timing passes, program
`build/ethernet_classifier_v4_stream/ethernet_classifier_v4_stream.bit`.
Wait for PHY startup and verify the 100 Mb/s link. Then run:

```powershell
# Smoke test, one outstanding batch
uv run --with scapy --with numpy --with pandas --with pyarrow python software/ethernet_batch_stream_rate.py --interface "Ethernet" --count 100 --batch-size 32 --window 1 --sequence 5000000 --report reports/model_v4_stream_smoke.json

# Same-size comparison of one versus two outstanding batches
uv run --with scapy --with numpy --with pandas --with pyarrow python software/ethernet_batch_stream_rate.py --interface "Ethernet" --count 1000 --batch-size 32 --window 1 --sequence 5100000 --report reports/model_v4_stream_window_1.json
uv run --with scapy --with numpy --with pandas --with pyarrow python software/ethernet_batch_stream_rate.py --interface "Ethernet" --count 1000 --batch-size 32 --window 2 --sequence 5200000 --report reports/model_v4_stream_window_2.json

# Full held-out set
uv run --with scapy --with numpy --with pandas --with pyarrow python software/ethernet_batch_stream_rate.py --interface "Ethernet" --count 27477 --batch-size 32 --window 2 --sequence 5300000 --report reports/model_v4_stream_full.json
```

Use window 2 only with the new overlap firmware. Require all results correct,
missing 0 and duplicates 0. The reported interval is first send to last correct
host-captured reply, including waits/refills; it excludes dataset loading,
reference scoring, frame construction and capture startup. Incorrect/missing
results have no successful completion rate. Repeat successful full-set runs
and compare medians with the same prepared-feature host baseline.

The next capacity step is more useful MACs per clock and a higher core clock,
checked against routed timing. This experiment deliberately holds those fixed
to isolate the pipeline and transport changes. A 150–200 MHz/all-DSP design
has an arithmetic budget that could exceed the host baseline, but actual
scheduling efficiency, host packet injection/capture and completed throughput
must demonstrate it. Simply increasing the model size again does not ensure
an accelerator advantage.
