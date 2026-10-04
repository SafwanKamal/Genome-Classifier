# V4 bottleneck review and proposed work

October 3, 2026. Three independent read-only reviews covered host transport,
RTL scheduling/buffering, and timing/benchmark evidence. This document is the
proposal before implementation. No RTL, software or constraints were changed
as part of this review; no builds, packets or bitstreams were generated.

## 1. Full routed 200 MHz timing currently fails

The core-only synthesis result (+0.444 ns) is superseded for timing acceptance
by the full routed report:

`build/ethernet_classifier_v4_200/ethernet_classifier_v4_200.runs/impl_1/variant_triage_top_ethernet_200_timing_summary_routed.rpt`

The October 3 04:57:51 report confirms a 5 ns classifier period, but reports
**WNS -0.812 ns, TNS -322.479 ns and 1,666 failing setup endpoints**, all in the
classifier clock domain. Hold passes with +0.009 ns worst slack. Internal
endpoints have timing constraints. Passing board score tests demonstrates
correctness for those runs, not closure across the operating conditions.
The earlier description of routed timing as unverified is now outdated.

Worst path (report near line 3003): `issue_input_reg[1]_rep__4` to
`output_lane[15].pair_a_reg[15]/A[5]`. Its data delay is 5.436 ns, including
**4.198 ns routing (77.2%)**. It contains two LUT6s, MUXF7 and MUXF8. Similar
paths run from activation registers into DSP inputs.

**Proposed first change:** in `rtl/mhz200/batch_model_200_core.sv:94`, replace
the flat activation selector with a registered two-level selector. Select
within four quarters in the first existing read cycle, then select the
quarter in the second cycle, delaying the quarter index accordingly. This
splits a 64-way selector per input lane into 16-way and 4-way stages.
Estimated extra storage is about 98 register bits. Keep the current read
latency, bias/weight/control alignment, model memories and 128 DSPs.

Inspect the synthesized register boundaries: the current `fetched_inputs`
and `inputs` registers can be absorbed into DSP stages, so their RTL names
alone do not establish a physical boundary. Use a narrowly scoped retention
attribute only if the intended first-stage registers disappear. Do not weaken
XDC timing, disable DSP inference globally, add blanket `DONT_TOUCH` or start
with floorplanning.

Validation: all 1,000 golden core vectors, Ethernet integration, synthesized
register structure and resource count. The user then runs full implementation;
require zero failing setup/hold endpoints at 200 MHz before claiming closure.
This change makes the clock target reliable; no throughput gain is promised
relative to the already programmed design nominally running at 200 MHz.

## 2. The host credit window plausibly explains the throughput gap

| Evidence | Value |
|---|---:|
| Three uninstrumented board runs, median | 154,051 variants/s |
| Simulated queued compute ceiling at 200 MHz | about 306,740/s |
| Core cycles / queued start spacing | 635 / about 652 |
| Compute time for 32 queued variants | about 104.3 us |
| Median host send-to-reply latency | 420.2 us |
| Current maximum outstanding batches | 2, or 64 variants |
| Approximate two-credit throughput at that latency | 152,308/s |
| Request / reply wire time per full batch | 44.8 / 19.04 us |

The credit estimate closely matches the measured rate, supporting a latency
and outstanding-work limitation. It is not proof: latency changes with load
and host observations include multiple overlapping stages. At the same fixed
latency, four credits would nominally support about 305,000/s, but that number
is not a forecast for a loaded four-credit implementation.

`software/ethernet_batch_stream_rate.py:121` limits the window to 1 or 2.
The FPGA has two batch-parser banks plus two RX frame banks. Those capacities
are potentially useful, but are not an automatic guarantee that a four-credit
burst is safe. RMII has no receiver backpressure, and
`rtl/ethernet_RX_overlap.sv:48` drops reception when its write bank is full.
Existing integration tests send only two full overlapping batches.

**Proposed experiment:** extend the isolated 200 MHz integration test to model
window four, actual reply-credit replenishment, minimum-gap arrivals, sustained
bank wrap and partial final batches. Require all sequences/scores correct and
no receiver overflow, reply errors or deadlocks. First try to use existing
storage. Enable window four in a separate experimental host script only if
that schedule is safe; add deeper request storage only if simulation shows
it is necessary. Do not permit arbitrary windows or change the wire protocol.

## 3. Remove avoidable receive work on the host

Request frames are already built as bytes before timing starts and sent using
a persistent raw socket (`ethernet_batch_stream_rate.py:140-145,58`). The old
`sendp` packet-cloning bottleneck has already been removed. Faster feature
preparation would not improve this benchmark's measured interval.

Receive still uses `AsyncSniffer` (line 176), which constructs Scapy packet
objects; the callback then serializes them to bytes again (line 161), solely
to decode our fixed Ethernet response. Median capture-to-callback is 74.9 us,
callback decoding 17.7 us, and reply-to-refill 27.8 us in the diagnostic run.
These figures overlap; they are not independent costs to sum.

**Proposed software change:** create a separate experimental batch script
using raw-byte reception through the existing Scapy/libpcap socket facilities.
Retain the BPF filter, packet decoder, sequence/count/flag checks, timeout and
startup/shutdown behavior. Avoid per-packet dissection and reserialization.
Keep the current benchmark script as the comparison baseline. No C extension,
driver changes or new native transport framework initially.

The cached Windows Scapy backend inspected during review already calls
`pcap_setmintocopy(handle, 0)`. Merely enabling immediate capture is therefore
not a new optimization unless the actual desktop backend differs; record its
version/backend rather than assuming a missing setting.

Return a valid reply's credit and refill before comparing all 32 golden
scores. Preserve framing, flags, expected sequence/count and duplicate checks
before accepting the reply. Every score must still be validated before the
run is reported successful. If golden checking moves outside timing, state
that explicitly and record validation separately. Main-thread other work is
only about 5.1% of the instrumented interval, so this alone cannot explain a
twofold gap.

## 4. Work sequence and acceptance criteria

1. Fix the isolated activation selector and complete functional checks.
   User runs implementation and checks routed timing; no assistant bitstream.
2. Compare existing versus raw-byte host reception at window two on the same
   timing-closed design, then evaluate the simulation-approved window four.
   Keep host and window changes separate so benefits can be attributed.
3. For each configuration, user runs three full 27,477-variant benchmarks
   without timing instrumentation, plus a separate diagnostic run. Require
   zero missing, mismatched or duplicate results. Compare median throughput.
4. Add minimal report identity: hostname, resolved NIC, transport/backend,
   model/input hashes and a user-supplied programmed-design label. A label
   records the user's configuration; it does not independently attest firmware.
5. Document results and decide the next change from the measured remaining
   limit, rather than changing several unrelated parts at once.

The matched uninstrumented 100 MHz baseline is still useful for attributing
the clock/pipeline change. It does not block offline review or simulation.

## Deferred changes

- Mailbox replacement: its normal 17-cycle overhead is only about 2.6% of
  queued spacing, so removing it cannot recover the current twofold gap.
- More DSP lanes, larger model, larger batches and memory repacking: defer
  until verified compute capacity is actually approached by the host path.
- Gigabit hardware: current full-batch request wire ceiling is about 714,286/s,
  well above current compute and measured throughput. Built-in LAN8720A is
  limited to 10/100 Mbps.
- PHY phase, RX/TX rewrite and extra timing exceptions: not implicated by the
  current critical core paths, so leave them unchanged.

The new full routed power report also exists beside the timing report:
**0.864 W on-chip, Low confidence**, no activity file. Its similarity to the
previous 0.870 W estimate does not establish measured energy savings. Power
comparison should follow a valid timing-closed design and representative
activity, with FPGA-only and whole-system scopes kept explicit.
