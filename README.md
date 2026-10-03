# Genome Classifier FPGA

An end-to-end research prototype for **genomic missense-variant triage**. The project turns a reproducible ClinVar/dbNSFP data pipeline into a small quantized neural network, deploys that model as deterministic SystemVerilog on a Nexys 4 DDR (Artix-7 / Nexys A7-100T), and uses the resulting score to prioritize evidence review.

The intended role is **triage**, not clinical diagnosis: a fast FPGA score ranks variants for lightweight or deep review, while the software pipeline gathers, reconciles, and reports source-linked evidence. Any real interpretation must remain subject to expert review and validated clinical workflows.

## Why this project

Missense-variant interpretation has two complementary bottlenecks:

1. A large number of variants need an inexpensive, reproducible first pass.
2. The smaller set worth deeper attention needs evidence that is traceable back to its sources.

This repository explores both sides. The FPGA supplies deterministic low-latency scoring, while the **VariantGate** software layer provides a provenance-aware path from model score to ClinVar and literature evidence, reconciliation, and human-readable reports.

## System overview

| Layer | Responsibility |
|---|---|
| Dataset pipeline | Build a labeled missense-variant dataset and an explicit 16-feature INT8 contract |
| Quantized model | Train and evaluate a hardware-matched `16 â†’ 8 â†’ 1` network |
| FPGA RTL | Execute exported fixed-point parameters with deterministic integer arithmetic |
| Host backends | Run NumPy, UART-FPGA, Ethernet-FPGA, or comparison inference through a common interface |
| Routing | Allocate variants to deep or light review using a recall-constrained threshold |
| Evidence pipeline | Retrieve ClinVar and PubMed evidence, reconcile sources, and generate auditable reports |
| Ethernet work | Develop the raw RMII transmit and receive interfaces for FPGA requests and results |

## Reproducible genomic dataset

The dataset pipeline starts from ClinVar GRCh38 records and keeps only an initial cohort of unambiguous missense variants with supported pathogenic/benign labels and review-status filtering. It joins dbNSFP-derived annotations, selects transcripts with a defined preference order (MANE Select, then fallbacks), derives amino-acid substitution features, builds quantized features, and validates the final contract.

The V1 validation manifest records:

- **184,186** labeled variants across **13,989** genes
- Gene-disjoint train/validation/test split
- Train: 128,416 variants, 9,789 genes
- Validation: 28,293 variants, 2,101 genes
- Locked test: 27,477 variants, 2,099 genes
- dbNSFP match rate: **99.39%**

The model always uses the ordered 16-feature contract recorded in the export manifest. This ordering is part of the hardware/software interfaceâ€”not an informal preprocessing detail.

For the detailed pipeline, schema, inputs, and commands, see [`genomic-dataset-pipeline/README.md`](genomic-dataset-pipeline/README.md) and [`ANNOTATION_SCHEMA.md`](genomic-dataset-pipeline/ANNOTATION_SCHEMA.md).

## Quantized FPGA model

The current model is a quantization-aware `16 â†’ 8 â†’ 1` dense network.

- Inputs and weights: signed INT8
- Biases and accumulators: signed INT32
- Hidden activation: ReLU, round-half-up requantization with `QSHIFT = 4`, then INT8 saturation
- Output: signed INT32 folded score
- Classification convention: `score >= 0`

The exported parameter manifest includes SHA-256 hashes, feature order, accumulator bounds, packed-memory layout, and the folded output bias. The hardware-oriented model in Python and the RTL are tested against the same integer arithmetic rules.

The scalable `dense_engine` uses packed weight memories and reusable MAC lanes:

- Hidden layer: four MAC lanes
- Output layer: one MAC lane
- Packed ordering: output group, input, lane
- Lane 0 occupies the most-significant packed bits

This keeps the architecture extensible without duplicating hand-written neuron datapaths.

## Model results

### V2 locked-test evaluation

| Metric | Result |
|---|---:|
| Architecture | `16 â†’ 8 â†’ 1` |
| Locked test variants | 27,477 |
| Unique test genes | 2,099 |
| PyTorchâ€“NumPy score matches | 27,477 / 27,477 |
| ROC-AUC | 0.98823 |
| Average precision | 0.97555 |
| Accuracy | 95.46% |
| Precision | 93.22% |
| Recall | 91.67% |
| F1 | 0.92438 |

The locked test set was not used to select the model, classification threshold, or routing threshold.

### Physical FPGA verification

The prior `16 â†’ 4 â†’ 1` hardware deployment completed a full locked-test UART validation:

- Board: Nexys A7-100T
- Clock: 100 MHz
- UART: 115,200 baud
- Variants sent: 27,477
- Bit-exact matches: **27,477**
- Bit-exact mismatches: **0**

V2 behavioral simulation also passed 1,000 bit-exact vectors at **122 cycles per inference**; the V2 FPGA smoke test matched NumPy on 100/100 comparisons.

These checks cover signed encoding, memory export, multiply-accumulate arithmetic, quantization, folded threshold behavior, UART packet framing, CRC, and response byte order.

## Recall-first routing policy

The classifier score is also used as a resource-allocation signal. The frozen V2 policy sends a variant to deep review when `score >= -276`.

| Validation-policy measure | Result |
|---|---:|
| Target pathogenic recall | 99.5% |
| Achieved pathogenic recall | 99.5099% |
| Deep review | 13,475 variants (47.63%) |
| Light review | 14,818 variants (52.37%) |
| Pathogenic variants routed to light review | 34 of 6,938 |

The routing threshold is separate from the classifier's `score >= 0` decision convention. Its purpose is to preserve very high pathogenic recall while concentrating the costlier evidence workflow on the most relevant portion of the cohort.

## VariantGate evidence workflow

`software/variantgate/` provides a modular, test-covered evidence layer:

- common inference interface with NumPy, UART-FPGA, Ethernet-FPGA, and comparison backends;
- ClinVar summary retrieval with identifier normalization and caching;
- linked PubMed retrieval through NCBI E-utilities;
- direct, bounded PubMed search built from variant/gene/HGVS context;
- explicit relevance and identity checks to avoid treating weak search hits as verified evidence;
- reconciliation of model output, ClinVar, linked literature, and direct-search literature;
- JSON/JSONL outputs, manifests, and Markdown report generation.

The evidence workflow is designed to preserve provenance and disagreement. It reports missing, uncertain, or conflicting evidence rather than silently converting it into a definitive answer.

## FPGA implementation

The main RTL is under `rtl/`.

| Component | Purpose |
|---|---|
| `variant_triage_core.sv` | Core inference control and model integration |
| `dense_engine.sv` | Parameterized packed-memory dense-layer engine |
| `dense_layer.sv`, `dense_neuron.sv` | Earlier dense-layer/neuron implementation path |
| `ReLU_quantizer.sv` | Hardware-matched ReLU and requantization |
| `UART_Packet_RX.sv`, `UART_Response_TX.sv` | Request/response protocol around FPGA inference |
| `FIFO*.sv`, `parameterized_reg_file.sv` | Supporting storage/control blocks |
| `simulation/` | RTL testbenches and generated bit-exact vectors |

The Vivado recreation script currently targets the classifier build. Board constraints for the normal design are in `constraints/nexys_4_DDR.xdc`.

## Ethernet transport bring-up

Ethernet is an active extension of the FPGA interface, not the primary project itself. The custom RMII transmit stack is deliberately vendor-IP-free and consists of a serializer, Ethernet CRC-32 unit, framing/padding/FCS controller, complete-frame buffer, and raw test top.

The first on-board milestone is verified:

- LAN8720A PHY link negotiated at **100 Mb/s**
- FPGA broadcasts one raw Ethernet frame per second
- Source MAC: `02:00:00:00:00:01`
- EtherType: `0x88B5` (local experimental)
- Wireshark accepts the 60-byte frames

Useful capture filter:

```
eth.type == 0x88b5 && eth.src == 02:00:00:00:00:01
```

The known-good TX clocking arrangement sends a direct 50 MHz reference clock to the PHY and uses a related 180Â° 50 MHz clock for MAC transmit logic, so RMII data updates occur between PHY sampling edges. The RX board investigation found that the PHY asserts carrier before the preamble, leaving initial `00` dibits that the original receiver silently rejected. RX now skips those carrier-acquisition zeros, captures the RMII inputs before decoding, forwards the reference clock through an ODDR, and buffers frames in synchronous block RAM. On September 30, 2026, board captures verified EtherType `88 B5`, the `RX01` marker, sequence 1, all 16 features, padding, and the final-byte marker. LEDs 0, 1, and 3 recorded PHY readiness, CRC acceptance, and an overflow; that standalone ILA capture did not establish ten out of ten acceptance. See [`docs/ethernet_RX.md`](docs/ethernet_RX.md#on-board-rx-validation--september-30-2026) for the test evidence, uv commands, and ILA setup. The combined `variant_triage_top_UART_ethernet` now connects that parser through request/result mailboxes to the classifier and Ethernet TX while retaining UART. See [`docs/ethernet_classifier.md`](docs/ethernet_classifier.md) for the combined build, simulation, and physical result: sequence 1 returned score -124, benign-side classification, and deep-review routing, matching the expected result for features -8 through 7. A subsequent board test received all ten responses, sequences 2 through 11, in order with the same expected score and flags at the default one-second send interval. Physical comparison then passed 20/20 requests: ten different golden vectors sent through both UART and Ethernet inputs, with both reply scores and Ethernet flags/sequences matching expectations. On October 1, the full comparison passed 2000/2000 requests across all 1,000 saved V2 vectors. User-run pacing checks returned 100/100 correct replies at observed host send-call rates of 91.8 and 639.0 requests/s, and 68/100 at 2270.3 requests/s with no incorrect scores or flags. Maximum sustained throughput and direct RX overflow counts remain unmeasured; ARP, IPv4, and UDP remain future work.

During implementation, MAC multiplication initially mapped to LUTs instead of DSPs, using fabric inefficiently and contributing to negative slack. The placement of `use_dsp` mattered: the current dense engine applies it directly to each product signal in its lane generate block. `ASYNC_REG` likewise belongs on the actual synchronizer register declarations. See [`docs/fpga_synthesis.md`](docs/fpga_synthesis.md) for the problem, placements, and synthesis checks.

## Repository layout

- `genomic-dataset-pipeline/` â€” data download, parsing, annotation, feature build, QAT training, evaluation, and FPGA export
- `rtl/` â€” synthesizable SystemVerilog for the triage accelerator, UART, and Ethernet TX/RX
- `simulation/` â€” SystemVerilog testbenches and vector files
- `memory/` â€” exported quantized model parameters
- `software/` â€” FPGA validation utilities and VariantGate orchestration/evidence tools
- `reports/` â€” tracked data-quality, training, export, test, FPGA-validation, and routing-policy manifests
- `constraints/` â€” Nexys board and standalone Ethernet constraints
- `scripts/` â€” Vivado project-recreation script
- `runs/` â€” generated scoring/evidence outputs when retained for reproducibility

Raw databases, large generated datasets, trained checkpoints, Vivado build outputs, and bitstreams are excluded from normal Git history.

The Ethernet-only top is now `variant_triage_top_ethernet` in `rtl/variant_triage_top_ethernet.sv`, with `constraints/variant_triage_ethernet_only.xdc`. It removes UART hardware, arbitration, and UART pacing while retaining the verified Ethernet protocol and model. End-to-end XSim passed. User-run Ethernet-only tests with both the previous and new XDC passed 1000/1000 vectors at 10 ms pacing, 100/100 at 1 ms pacing, and 100/100 in a short burst, without missing, duplicate, out-of-order, or mismatched replies. A subsequent 1,000-request burst also passed 1000/1000, with zero loss or mismatches, at an observed host send-call rate of 3211.5 requests/s. The full unpaced locked V2 comparison subsequently passed 27477/27477, with all reply error counts zero; its host send-call rate was 4106.5 requests/s over 6.691 seconds. This is a host measurement, not FPGA inference latency or a proven throughput ceiling. Routed timing reports and sustained throughput remain unreviewed/unmeasured. See [`docs/ethernet_classifier.md`](docs/ethernet_classifier.md) for setup and commands. No Ethernet-only bitstream was generated.

The host rate validator also offers `--sender raw` to send prebuilt request bytes through one layer-2 socket, avoiding Scapy packet cloning while retaining result validation. Offline tests passed; user-run comparison commands and profiling limitations are documented in [`docs/ethernet_classifier.md`](docs/ethernet_classifier.md). The first unprofiled physical comparison passed 1000/1000 replies for both senders: raw sent at an observed 6465.0 requests/s versus 3512.1 for Scapy, reducing send-call time by 45.7%. The full raw-sender check then passed 27477/27477 with zero reply errors at an observed 6667.3 requests/s over 4.121 seconds: 62.4% higher sending rate and 38.4% less send-call time than the earlier full-set Scapy run. These are host measurements; FPGA capacity and inference latency remain unmeasured.

VariantGate now provides `--backend ethernet` and `--backend compare-ethernet`, using one persistent raw socket and matching each request to its reply sequence. Comparison mode checks FPGA scores against NumPy and writes the existing scores, routing summary, and run manifest. All 41 offline VariantGate tests passed; the physical integration smoke/full commands are in [`docs/ethernet_classifier.md`](docs/ethernet_classifier.md#variantgate-ethernet-integration---october-1-2026). The physical smoke (10 variants) and full-set (27,477 variants) integrations both passed with zero FPGA/NumPy score mismatches. Full-set routing selected 13,454 deep-review and 14,023 light-review variants, with mean host round-trip latency 928.8 us. Summary/provenance verification is preserved in `reports/ethernet_variantgate_full_v2_validation.json`. No FPGA rebuild is needed.

A same-host V2 NumPy benchmark scored all 27,477 variants in a median 0.3702 seconds across five repetitions (about 74,229 variants/s), versus 28.3811 seconds for the sequential Ethernet/NumPy comparison workflow. This establishes no end-to-end FPGA speedup for this small model; transport and capture costs are included in the hardware workflow, and FPGA compute-only latency remains unmeasured. See `reports/ethernet_vs_host_v2_benchmark.json` for the measured comparison.

## Next steps

The preserved V4 baseline now has a separate streamed alternative: `rtl/variant_triage_top_ethernet_stream.sv`. It overlaps MAC pipeline stages and adds two-bank Ethernet batch buffering with a two-request host window. Core simulation improves from 1,844 to 617 clocks using the same model and 128 DSPs; physical throughput remains unmeasured. See [`docs/model_v4_stream_report.md`](docs/model_v4_stream_report.md) for the board's theoretical limits, file-by-file changes, verification and user-run commands. Original pipeline files and build scripts are unchanged; no bitstream was generated.

The separate V4 batch experiment adds a trained `16 → 256 → 256 → 1` model, a shared 128-DSP core, and up to 32 variants per Ethernet exchange. Default single-variant operation remains available. See [`docs/model_v4_batch_report.md`](docs/model_v4_batch_report.md) for the exact changes, verification, quality tradeoffs, throughput limits, and user-run build/test commands. No V4 bitstream was generated; host superiority remains the measured baseline.

The larger `16 → 256 → 1` V3 model is trained and exported separately, with 32 hidden MAC lanes. It improves held-out average precision slightly (0.9756 → 0.9778), but does not establish a throughput advantage: an efficient batched host reaches about 1.48 million variants/s, above this RTL's compute-only ceiling of about 86,700/s. See [`docs/model_v3_throughput.md`](docs/model_v3_throughput.md) for correctness checks, timing scope, build settings and user-run commands. No V3 bitstream was generated.

1. Build/program the separate V4 batch experiment, verify physical correctness, and compare completed request/response throughput at batch sizes 1 and 32 against the batched host baseline.
2. Integrate the classifier, routing policy, and VariantGate workflow into a single reproducible end-to-end run.
3. Review routed timing for the Ethernet-only build and measure sustained Ethernet capacity after its successful vector and short-burst checks (100/100 burst replies, compared with 68/100 in the combined-top baseline).
4. Measure sustained request capacity and confirm FPGA RX overflow separately from host reply loss; see [`docs/ethernet_classifier.md`](docs/ethernet_classifier.md#physical-pacing-results--october-1-2026).
5. Add ARP, static IPv4, and UDP after the raw request/response path is verified.
6. Expand evidence-source coverage while preserving source identity, provenance, and disagreement reporting.
