# Throughput and energy evaluation — October 3, 2026

## Measured baseline

The streamed V4 FPGA passes all 27,477 test scores at 127,499 completed
variants/s on the second desktop. The same desktop's best tested CPU
configuration reaches 768,944/s (batch 64/four BLAS threads, median of five
warmed repetitions). Both exclude preparation; FPGA includes transport.
CPU is 6.031× faster. FPGA queued simulation capacity is 159,236/s at
100 MHz, with 628 clocks between starts. Increasing only host efficiency
cannot bridge the current compute gap.

The adapter comparison is **deferred at the user's request**. Later, test
the original USB adapter versus another interface on the **same PC**, keeping
FPGA bitstream, model, cable/link settings, batch size 32, window 2 and capture
software fixed. Repeat runs and compare medians. The existing two-PC result
establishes a host/interface-path difference, not a proven individual adapter
fault. Both 100 Mb/s links can have different driver and application latency.

## Recommended throughput work

Preserve the working streamed design and frozen model. Make each experiment
separate and compare bit-exact results, routed timing, resources and physical
completed throughput after each change.

1. **Raise only the core clock first**, initially targeting 125 MHz, then
   consider 150 MHz if routing permits. Generate the core clock using the
   Clocking Wizard and move core logic/mailbox endpoints/reset synchronization
   to that clock. Keep the physical 100 MHz board input correctly constrained,
   and leave the PHY's three 50 MHz clocks/phases unchanged. Changing the input
   XDC period without changing the actual core clock is not a valid speedup.
   At unchanged 628-clock spacing, 125 MHz budgets 199,045/s and 150 MHz
   budgets 238,854/s, before transport and host starvation. These are targets,
   not verified timing or promised completed rates.
2. **Increase useful DSP work per clock**, using a separate core and correctly
   repacked weight memories. A candidate is 224 lanes (32 outputs × seven
   inputs), within the device's 240 DSP count, versus current 128. Handle
   partial groups explicitly and check exact signed arithmetic. Verify LUT,
   routing and memory costs before assuming it fits. Measure cycles before
   accepting this layout; unused lanes and scalar output scheduling matter.
   Keep the same trained weights and model quality. No global false paths or
   relaxed timing constraints are substitutes for closure.
3. **Plan transport capacity alongside compute**. The current 32-record
   protocol at 100 Mb/s tops out at 714,286/s, below the measured CPU rate.
   Simply removing the 32-record limit is insufficient: each record already
   needs 16 feature bytes; even a roughly MTU-sized 93-record request at 1,512
   bytes before FCS budgets only 756,836/s. Lossless feature packing, a faster
   physical interface supported by different hardware, or keeping relevant
   data on the accelerator are possible design directions, each changing the
   transfer/workload assumptions. A larger host window must match actual
   buffer capacity; do not just remove the current limit.

This sequence improves the current design and establishes where gains stop.
It does not promise to beat the CPU: even all 240 DSPs at 200 MHz budget only
686,813 useful MAC-based variants/s for V4 before scheduling overhead.
Ideal all-DSP arithmetic parity needs about 223.9 MHz; practical parity needs
more headroom and transport changes. DSP packing could change the arithmetic
budget but adds signed arithmetic/packing complexity and is a later option.
Changing model size merely to make a benchmark favorable is not evidence of
investment value; heavier computation should come from a justified workload
and must be benchmarked against the optimized CPU too.

No clock, lane, model or transport changes are implemented by this document.
The original pipeline and working streamed RTL remain intact.

## Energy efficiency: measure, do not assume

Vivado's calculation is useful as the **first estimate**; an external meter
is not required to begin the power study. With the routed design open in
Vivado, run:

```tcl
report_power -file {C:/Users/user/OneDrive - Texas Tech University/Desktop/Code/FPGA Project/Genome Classifier/reports/model_v4_stream_power_estimate.rpt}
```

Record Total On-Chip Power, static/dynamic breakdown, clock frequencies,
activity assumptions and confidence level. Default activity assumptions may
not represent inference; representative simulation activity (SAIF) improves
the workload estimate. Use estimated watts divided by matching completed
variants/s to report **estimated FPGA-chip joules/variant**, with duty-cycle
assumptions recorded. This excludes the host, external PHY and board supply
losses and must not be presented as measured desktop-plus-FPGA efficiency.
Compare like scopes or retain separate chip, board and system results.

The Nexys 4 DDR's 100 MHz oscillator is a reference input, not a maximum
internal clock frequency. Its Artix-7 clock management supports synthesized
frequencies through MMCM/PLL; Clocking Wizard can generate a 125 MHz core
clock while retaining the three existing 50 MHz Ethernet clocks. See the
[board reference manual](https://digilent.com/reference/_media/nexys4-ddr%3Anexys4ddr_rm.pdf)
and [Clocking Wizard frequency configuration](https://docs.amd.com/r/en-US/pg065-clk-wiz/Configuring-Output-Clocks).
125 MHz means an 8 ns core cycle, versus 10 ns currently. Board clock capability
does not establish that this design meets 8 ns routed setup/hold timing.

The October 3 routed report in the manual `Genome_Classification` project
meets 100 MHz with only **0.120 ns core setup slack**. Holding placement,
routing and clock effects fixed gives a rough limit of
`1000 / (10 - 0.120) = 101.2 MHz`, not a verified maximum. The worst core
path runs from an accumulator/product register through addition and output
quantization to an output register; its 9.319 ns data delay includes 6.185 ns
of routing. Thus 125 MHz requires shortening this path, for example by
registering the accumulated result before output quantization, then checking
cycle alignment, numerical correctness and routed timing. The earlier
core-only synthesis slack does not establish the full design's routed limit.
AMD's [Artix-7 DS181](https://docs.amd.com/v/u/en-US/ds181_Artix_7_Data_Sheet)
specifies a 464 MHz global clock-tree limit for the -1 speed grade; this is a
clock distribution specification, not the achievable frequency of this model.

An isolated 200 MHz target now lives in `rtl/mhz200`, preserving the existing
RTL. See its [README](../rtl/mhz200/README.md) for pipeline changes, functional
verification, Clocking Wizard settings and the separate Vivado project setup.
The target still needs full routed timing acceptance and a physical throughput
test; simulated 200 MHz operation does not establish board timing closure.

An FPGA may use less power while running this inference workload, but a
lower wattage does not automatically mean less energy per completed variant.
No power or energy readings have been collected, so no measured efficiency
advantage is currently claimed.

The previous 100 MHz streamed design does have a routed Vivado power estimate:
`reports/model_v4_stream_power_estimate.rpt`, generated October 3 at 04:20:57.
The automatic implementation report at 02:24:33 gives the same power totals.
Estimated total on-chip power is **0.870 W**, comprising **0.770 W dynamic**
and **0.101 W static** (rounded separately). The classifier hierarchy accounts
for **0.640 W dynamic**, and the 128 DSPs account for **0.135 W**; these are
different breakdowns of the same power and must not be added together.
Overall confidence is **Low**: no simulation activity file is supplied, more
than 75% of inputs lack user-specified activity, and less than 25% of internal
nodes have user-specified activity. Thermal settings include 25 C ambient,
250 LFM airflow and a medium heatsink; the reported 29 C junction temperature
is an estimate under those settings, not a board temperature measurement.

Dividing the estimate by the previous desktop throughput of 127,499 variants/s
gives approximately **6.82 microjoules per variant for the FPGA chip alone**.
This is illustrative: report switching activity has not been matched to that
benchmark's duty cycle. It excludes host, PHY and board supply losses and is
not evidence of whole-system energy superiority over the CPU. A representative
activity file and physical power measurement remain future validation work.

Report both:

- **Joules per correct variant:** `energy over the measured interval / correct_count`.
- **Correct variants per joule:** `correct_count / energy`, equivalent to
  `throughput / average_power` for a sustained workload.

Use the same desktop, frozen model, prepared cohort and correctness checks.
Measure sustained inference long enough for the meter's sampling resolution,
with warmup excluded and repeated trials. The existing subsecond run is too
short for many external power meters. Reuse prepared features/requests for
long runs; file loading and golden-reference computation remain outside the
inference interval. Match the energy interval to the reported throughput
interval, and record sample rate, instrument, duration and temperature/power
settings. For an application-level comparison including preparation, include
that work consistently in both backends and report it separately.

The principal investment comparison should be **whole-system energy**:

| CPU path | Accelerator path |
|---|---|
| Desktop running CPU inference | Same desktop sending/capturing replies plus FPGA board |
| Measure desktop supply energy | Measure desktop and board supply energy together |

If the board is powered through the measured desktop's USB supply, its energy
is already included: do not add it twice. If externally powered, include its
supply energy as well. Include PHY, regulators, memory and NIC costs in the
system boundary. Collect idle power in both configurations and report both
total energy and incremental energy above each configuration's idle baseline.
Idle-subtracted results answer a different question and must not replace the
total-energy result. Do not compare FPGA-chip estimates directly with whole
desktop wall power and call it a system efficiency gain.

For supporting component measurements, CPU package counters such as
[Intel RAPL](https://www.intel.com/content/www/us/en/developer/articles/technical/software-security-guidance/advisory-guidance/running-average-power-limit-energy-reporting.html)
cover defined power domains, not necessarily the whole desktop. FPGA
`report_power` after routing provides an **estimate**, preferably with
representative switching activity; AMD notes post-route analysis uses the
implemented logic and routing resources for its most accurate estimate.
See [Vivado power analysis](https://docs.amd.com/r/en-US/ug907-vivado-power-analysis-optimization/Vivado-Power-Analysis).
Label these separately from physical board/system readings. TDP is not a
measurement of this workload's power.

At current desktop rates, `127,499 / 768,944 = 0.1658`: for lower total
joules/variant, the accelerator path must average **less than 16.6% of the
CPU path's power** over equivalent work, with the same measurement boundary.
Because the current offload path still uses a desktop, that is a demanding
condition. It is a mathematical break-even condition, not a power result.
Higher accelerator throughput can improve this tradeoff, but raising clock
rate or adding DSPs may also increase power and must be measured.

The hardware needed for a credible energy result is a suitable external
energy/power meter, or calibrated board supply measurement plus desktop supply
measurement. No new measurement dependency, power-meter integration or
continuous hardware test is introduced yet.
