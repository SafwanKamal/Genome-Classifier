# Field data, acquisition parallelism, power and battery analysis

October 3, 2026. Engineering estimates for the current Nexys A7/Artix-7 design and a prospective KR260 field gateway. No hardware measurements or RTL changes were performed for this analysis. Public dataset replay is the first deployment stage; a live sequencer is not currently available.

## Conclusions

- An estimate is useful now. The current 0.870 W Vivado chip estimate plus component allowances gives approximately **1.7–2.6 W at the current board input**, holding the chip estimate fixed. Use **2.2 W** as a planning point, not a measurement or guaranteed maximum.
- A fully occupied 512-channel nanopore-scale input at 5 kHz and 16 bits/sample produces **5.12 MB/s** uncompressed signals. One 100 Mb/s connection can plausibly carry it using an efficient bulk protocol; two streams leave little margin, and four require a faster interface.
- Acquisition parallelism does not require 512 independent neural networks. At one score per channel per 100 ms, one stream needs **5,120 decisions/s**. A shared core can be sufficient; feature extraction, sample association and buffering need separate sizing.
- A 100 Wh battery with 80% usable capacity and 90% downstream conversion delivers 72 Wh to board inputs. At 2.2 W this gives approximately **33 hours for the current accelerator board alone**. A prospective 10 W KR260 gateway gives **7.2 hours**. A complete 50 W acquisition system gives **1.44 hours**.
- Idle savings require design action. Core clock gating can preserve inference state while reducing switching. K26 additionally supports PL rail shutdown for longer inactivity, with quiescence and configuration/state restoration requirements.

## Acquisition assumptions and calculations

This is a MinION-scale sizing example, not a promised yield or measured transfer rate. Assume 512 simultaneously sampled channels, 5,000 samples/channel/s, and two bytes/sample. Actual channel activity, chemistry, file compression, packetization and arrival bursts vary. Read the real sample rate from dataset metadata during replay.

Oxford Nanopore documents [512-channel flow cells](https://store.nanoporetech.com/us/flow-cells.html); its [POD5 specification](https://software-docs.nanoporetech.com/pod5/0.3.34/specification/) defines uncompressed signed 16-bit signal storage. Dorado documents [5,000 Hz model/sample-rate cases](https://software-docs.nanoporetech.com/dorado/latest/troubleshooting/troubleshooting/). For basecalled data, use a separate illustrative translocation rate of 450 bases/channel/s; ONT describes roughly 400–450 bases/s DNA operation in its [technology history](https://nanoporetech.com/about/continuous-development-and-improvement). These assumptions do not imply every modern chemistry uses the same rates.

Formulas, using decimal MB/GB:

- Raw rate = devices × 512 × 5,000 × 2 bytes/s.
- Sequence rate = devices × 512 × 450 bases/s.
- Uncompressed FASTQ estimate = sequence rate × 2 bytes/base × 1.2; one character each for base and quality, with an illustrative 20% header/format allowance. Short reads can have larger relative header overhead.

| Devices | Uncompressed raw MB/s | Raw Mb/s | Illustrative FASTQ MB/s | Raw GB/hour | Raw GB/8 hours |
|---:|---:|---:|---:|---:|---:|
| 1 | 5.12 | 40.96 | 0.553 | 18.43 | 147.46 |
| 2 | 10.24 | 81.92 | 1.106 | 36.86 | 294.91 |
| 4 | 20.48 | 163.84 | 2.212 | 73.73 | 589.82 |
| 16 | 81.92 | 655.36 | 8.847 | 294.91 | 2,359.30 |

These are payload-generation calculations. POD5/FASTQ compression can reduce storage/transfer, but use measured compression ratios from each dataset rather than assume one. If retaining raw plus FASTQ, budget both. At 4 kHz, raw numbers are 80% of the table. At half channel activity, recorded strand data may be smaller, while an acquisition interface may still transport open-pore/background samples. Budget the actual interface stream.

For one full stream, sequence production is approximately 230,400 bases/s. A hypothetical mean read length of 5,000 bases gives about 46 completed reads/s; 500-base reads give about 461/s. Fragment length and pore idle time alter this markedly. Reads/s, signal samples/s and variants/s are different units and must not be compared directly.

## How much must be processed concurrently?

Maintain independent channel/read identity and state, but time-multiplex arithmetic. The interface serializes data into a stream even though molecules are sensed concurrently.

| One score/channel every | Scores/s, one device | Nonoverlapping raw window buffer | Double buffer |
|---|---:|---:|---:|
| 1 second | 512 | 5.12 MB | 10.24 MB |
| 100 ms | 5,120 | 0.512 MB | 1.024 MB |
| 20 ms | 25,600 | 0.1024 MB | 0.2048 MB |

Window length and decision interval can differ. For a 1-second context updated every 100 ms, retain the 1-second ring buffer even though decisions occur every 100 ms. Add metadata, normalization/feature state, outputs, model memory and queue buffers to the table. A short window does not establish enough biological information for accurate screening.

The existing 100 MHz V4 simulated queued core capacity is about 159,236 feature vectors/s. At 5,120 vectors/s this corresponds to approximately 3.2% ideal compute occupancy, conditional on the same model and prepared-feature interface. Physical measured completion is about 127,499/s, also above this decision rate. Neither result establishes raw-signal classifier capacity: extraction and a different model may dominate.

An illustrative 1-million-MAC model at 5,120 windows/s needs 5.12 billion MAC/s; our 128 DSPs at 100 MHz budget 12.8 billion unpacked MAC/s before scheduling and memory limits. This is a sizing example, not evidence that such a model fits or achieves accuracy. For four devices, requirements scale fourfold.

Start with one shared processing pipeline and 512 channel contexts. Add lanes or engines only after measuring its busiest stage and worst-case latency. Stagger decisions when possible; otherwise a synchronized 512-window burst matters. At the current simulated capacity, processing 512 already prepared V4 vectors takes about 3.2 ms; feature generation and new-model work are additional.

## Input methods and storage

| Method | Suitability | Integration boundary |
|---|---|---|
| Local recorded FASTQ/POD5 replay | First experiment; repeatable | Measures replay processing, not real acquisition/control |
| Supported sequencer host → 100 Mb/s Ethernet → current FPGA | One raw stream potentially fits; FASTQ comfortably fits | New bulk input protocol and buffers needed; current 16-feature protocol cannot ingest reads/signals |
| Supported host → KR260 Gigabit Ethernet → ARM/DMA/PL | Preferred gateway architecture; several streams | Stream parsing, memory traffic and persistent storage included |
| Direct USB sequencer acquisition on KR260 | Not a supported assumption | Current MinION host software excludes arbitrary ARM hosts |
| 10GigE on KR260 | Future larger aggregate workloads | Transceiver/module/IP and energy costs; unnecessary for first one-device experiment |

100 Mb/s is 12.5 MB/s line rate. Use **8 MB/s application payload** as a conservative planning target until measured; efficient large Ethernet frames have a better theoretical ceiling, while our Python/raw-socket path may have a lower practical one. Full-duplex TX and RX have separate line capacity, but share processing/buffer resources. One raw stream fits this planning target; two exceed it. Gigabit has 125 MB/s line rate; use 80–100 MB/s as a bulk planning range, not a measured KR260 promise. Four streams fit comfortably; sixteen are near the conservative lower end.

For one raw stream, a 1-second pause needs 5.12 MB, 60 seconds needs 307.2 MB, and 10 minutes needs 3.072 GB. BRAM is for short bursts; DDR absorbs longer pauses; SSD or other sustained storage is needed for outages and full-run retention. KR260's 4 GB DDR is not all available for buffering after OS/model allocations. Storage write rate needs headroom beyond average acquisition. Filtering only after ingress cannot repair an overloaded ingress link.

MinION acquisition requires a supported host, and [Mk1D specifications](https://nanoporetech.com/document/requirements/minion-mk1d-device-and-it-specifications) list maximum device power of 7.5 W. USB signaling power is not the same as attached-device power: include the sequencer once wherever it is powered. A gateway next to a laptop is not a laptop-free acquisition system.

## Current Artix-7 power budget

The routed [Vivado report](../reports/model_v4_stream_power_estimate.rpt) gives 0.870 W on-chip: 0.770 W dynamic and 0.101 W static (rounded separately). Classifier hierarchy dynamic power is 0.640 W; MMCM is about 0.106 W. The report has no simulation activity file and low overall confidence. Its temperature/airflow settings also differ from an uncooled field enclosure.

| Component | Planning allowance | Basis |
|---|---:|---|
| FPGA chip | 0.870 W | Existing routed report; held fixed for this estimate |
| LAN8720A and Ethernet driver/magnetics supply, LEDs | 0.30–0.45 W | Datasheet chip figure plus separately documented external supply current; supply configuration margin |
| USB/JTAG bridge | 0.15–0.30 W | Operating allowance; removing UART RTL does not remove or automatically suspend the board chip |
| Unused DDR/flash/sensors/oscillator/status LEDs and other board loads | 0.20–0.60 W | Engineering allowance; exact operating/suspend states not established |
| Board regulator efficiency | 85–92% | Assumed range at this load; apply to rail-load sum |
| **Board input** | **1.65–2.61 W, rounded to 1.7–2.6 W** | Sum of rail loads divided by regulator efficiency |

The [LAN8720A datasheet](https://ww1.microchip.com/downloads/en/DeviceDoc/00002165B.pdf), table 5-1 and notes, lists 148 mW typical device-only traffic power and separately 41 mA at the transformer supply for 100BASE-TX. At 3.3 V the latter contributes 135 mW. Table conditions, internal-regulator use and I/O voltage require adjustment; do not add internal regulator losses twice. Its general/energy-detect power-down figures are 7.6/21 mW device-only under stated test conditions, not established whole-board sleep power. A linked-idle PHY continues signaling; lack of packets does not mean zero PHY power.

The [Nexys board manual](https://digilent.com/reference/_media/reference/programmable-logic/nexys-a7/nexys-a7_rm.pdf) documents its supply rails and USB/JTAG hardware. [FTDI's H-series factsheet](https://ftdichip.com/wp-content/uploads/2025/08/h-chip-series-factsheet-en-v1.pdf) lists typical operating current around 70 mA. Our allowance is approximate and depends on power/enumeration state. The remaining 0.20–0.60 W is explicitly not a sum of verified part-mode currents.

These bounds hold chip power fixed and are not a worst-case guarantee. If actual chip demand differs from 0.870 W, approximately each extra 0.1 W adds 0.11 W at board input around 90% efficiency. A +/-0.3 W chip sensitivity expands the budget roughly to 1.3–3.0 W. Add external storage, an acquisition host, switch/router, display and sequencer separately. An inline two-port bridge also needs the second interface; the current board is a single-port endpoint.

## Prospective KR260 budget

Use **8–15 W board input, 10 W planning point**, for a lightly configured local gateway with a bounded screening kernel, one active Gigabit port, modest storage activity and cooling. This is a provisional scenario, not a sum based on a synthesized K26 version of our model. Full fabric utilization or heavy storage can exceed it.

AMD's [SOM product table](https://docs.amd.com/api/khub/documents/lwwUFdfst1gWnYUxm9b4PA/content) lists 7.5 W typical/15 W maximum K26 SOM values; those are not whole KR260 board measurements. Its [power-estimation guidance](https://docs.amd.com/r/en-US/ug1090-k26-thermal-design/Power-Estimation) explicitly requires adding external SOM peripherals to Vivado's MPSoC estimate. The [DP83867 datasheet](https://www.ti.com/lit/ds/symlink/dp83867ir.pdf) lists approximately 0.46–0.53 W typical operational Gigabit power depending on package/supplies. Provision roughly 0.5–0.7 W per active physical port with board allowance; four active ports can cost around 2 W. Actual KR260 part variants and supply configuration must be checked. CPU/DDR power, carrier regulators, fan and storage supply the rest of the budget. Avoid double-counting integrated MAC/transceiver power already included in a future SoC report.

## Battery life

Use generic **100 Wh and 250 Wh nominal packs** as field planning examples, not product recommendations. Assume 80% available energy after reserve/derating and 90% battery-to-board conversion. This gives 72 and 180 Wh at board inputs. Board regulator loss is already included above and is not charged again here. Cold, battery age, discharge rate, converter standby and cable losses can change these factors.

`runtime_hours = nominal_Wh × usable_fraction × external_conversion_efficiency / average_board_input_W`

| Scenario | Average board/system input | 100 Wh pack | 250 Wh pack |
|---|---:|---:|---:|
| Current board active planning point | 2.2 W | 32.7 h | 81.8 h |
| Current board active estimated range | 1.7–2.6 W | 27.7–42.4 h | 69.2–105.9 h |
| Hypothetical 10% active, 90% optimized 1.2 W idle | 1.3 W | 55.4 h | 138.5 h |
| Prospective KR260 planning point | 10 W | 7.2 h | 18 h |
| Prospective KR260 scenario range | 8–15 W | 4.8–9 h | 12–22.5 h |
| Whole acquisition example: host + gateway + sequencer/storage | 50 W | 1.44 h | 3.6 h |
| Whole acquisition scenario range | 40–70 W | 1.0–1.8 h | 2.6–4.5 h |

The 40–70 W system bracket is an assumption for host activity, gateway, sequencing and storage, not a measured minimum/maximum. For example, 30 W host + 10 W gateway + 7.5 W sequencer + 2.5 W extra storage = 50 W; count any USB-powered component only once. GPU-heavy live basecalling can raise demand substantially. The examples exclude extraction/library-preparation equipment, heaters, pumps, radio links and the central server. Eight hours at 10 W needs about 111 Wh nominal under these factors; eight hours at 50 W needs about 556 Wh. A 12.8 V 20 Ah pack is 256 Wh nominal, illustrating why voltage must accompany an Ah rating.

## Specific idle-power mechanisms

1. **Clock/data enables:** suppress DSP operand updates, BRAM reads and register changes when there is no valid work, accounting for pipeline drain. The current stream core's ROM reads, input registers and multiplier registers execute on each clock even in IDLE. Stable operands may stop most data toggling, but clocks still consume power. Inspect `rtl/batch_model_stream_core.sv`; no idle savings are newly implemented here. AMD [resource guidance](https://docs.amd.com/r/en-US/ug907-vivado-power-analysis-optimization/Use-Device-Resources-More-Efficiently) documents BRAM/DSP enables.
2. **Dedicated core clock gating:** use BUFGCE/appropriate clock resources, not an AND gate in fabric. Keep ingress buffers, wake controller and required PHY clocks running. Finish outstanding operations, gate the core, and wake from an ungated domain when data arrives. Clock gating preserves powered state but does not remove static leakage or MMCM/peripheral power. See [AMD clock-buffer guidance](https://docs.amd.com/r/en-US/ug949-vivado-design-methodology/Gating-the-Clock-Buffer).
3. **Frequency scheduling:** run fast enough to finish a burst and sleep, or lower frequency for steady low demand. Dynamic power roughly follows activity × capacitance × voltage squared × frequency. Neither choice automatically minimizes energy: static power and wake overhead determine the break-even.
4. **Interface/storage management:** stop unused peripherals and power down unused PHYs where supported. Avoid rapid link drops when data may arrive; general PHY shutdown cannot preserve normal network reception. Energy-detect and full power-down have different wake behavior. Keep SSD writes batched within latency/storage-loss requirements.
5. **K26 PL rail shutdown:** AMD documents [independent PL power-domain control](https://docs.amd.com/r/en-US/ds987-k26-som/PL-Power-Domain-Control). For sufficiently long idle periods, ARM can remain available while PL rails are off. Quiesce DMA/interfaces and preserve required state first; restore configuration/state before reuse. PS-side networking may remain available, but PL-side Ethernet/logic cannot. Suspend unused ARM cores/peripherals and use supported CPU frequency/idle policies too.
6. **Whole-device shutdown:** suited to long collection gaps with an independent wake mechanism. Artix-7 fabric does not provide K26-style independently switchable PL rails; whole-board shutdown loses volatile state/configuration and requires boot/reconfiguration.

An illustrative chip model using the report hierarchy is `P_chip(d) ≈ 0.231 + 0.640 × d W`, if classifier dynamic power is almost completely gated while all other reported activity remains. At 10% compute duty this gives 0.295 W chip power. This is a conditional accounting model, not a prediction that our current RTL already reaches it. The 1.2 W idle board assumption in the battery table includes linked interface and other overhead. Even a large chip saving is diluted by peripheral/acquisition power.

For deeper sleep, use `idle_duration > transition_energy / (awake_idle_power - sleep_power)` and check wake latency against the stream deadline. Raw acquisition can continue while the classifier sleeps, so the always-on buffer needs at least raw_rate × wake_latency plus burst headroom. Do not shut down the only ingress path without upstream retention/backpressure.

## Recommended next measurements and design choices

Start with one-device public replay, then 2/4-device and burst stress cases. Use Gigabit Ethernet on KR260; retain the current 100 Mb/s endpoint for feature-core experiments. Target DMA-based ingestion, independent channel contexts, bounded queues, and persistent outage storage. Establish one shared kernel's capacity before duplicating cores.

Generate separate representative busy, linked-idle and gated-idle activity traces and power reports for the unchanged 100 MHz design. Measure board input voltage/current in each state and compare with this budget; confirm DDR/USB/PHY mode assumptions. On KR260 measure ARM-only versus ARM+PL with identical input, model and traffic, including fan/storage. Calibrate battery usable energy and converter efficiency at the intended load and temperature. These measurements refine an already useful estimate rather than being prerequisites to all planning.
