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
| Quantized model | Train and evaluate a hardware-matched `16 → 8 → 1` network |
| FPGA RTL | Execute exported fixed-point parameters with deterministic integer arithmetic |
| Host backends | Run NumPy, UART-FPGA, or comparison inference through a common interface |
| Routing | Allocate variants to deep or light review using a recall-constrained threshold |
| Evidence pipeline | Retrieve ClinVar and PubMed evidence, reconcile sources, and generate auditable reports |
| Ethernet work | Develop a reusable FPGA result-transport interface, beginning with raw RMII transmit |

## Reproducible genomic dataset

The dataset pipeline starts from ClinVar GRCh38 records and keeps only an initial cohort of unambiguous missense variants with supported pathogenic/benign labels and review-status filtering. It joins dbNSFP-derived annotations, selects transcripts with a defined preference order (MANE Select, then fallbacks), derives amino-acid substitution features, builds quantized features, and validates the final contract.

The V1 validation manifest records:

- **184,186** labeled variants across **13,989** genes
- Gene-disjoint train/validation/test split
- Train: 128,416 variants, 9,789 genes
- Validation: 28,293 variants, 2,101 genes
- Locked test: 27,477 variants, 2,099 genes
- dbNSFP match rate: **99.39%**

The model always uses the ordered 16-feature contract recorded in the export manifest. This ordering is part of the hardware/software interface—not an informal preprocessing detail.

For the detailed pipeline, schema, inputs, and commands, see [`genomic-dataset-pipeline/README.md`](genomic-dataset-pipeline/README.md) and [`ANNOTATION_SCHEMA.md`](genomic-dataset-pipeline/ANNOTATION_SCHEMA.md).

## Quantized FPGA model

The current model is a quantization-aware `16 → 8 → 1` dense network.

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
| Architecture | `16 → 8 → 1` |
| Locked test variants | 27,477 |
| Unique test genes | 2,099 |
| PyTorch–NumPy score matches | 27,477 / 27,477 |
| ROC-AUC | 0.98823 |
| Average precision | 0.97555 |
| Accuracy | 95.46% |
| Precision | 93.22% |
| Recall | 91.67% |
| F1 | 0.92438 |

The locked test set was not used to select the model, classification threshold, or routing threshold.

### Physical FPGA verification

The prior `16 → 4 → 1` hardware deployment completed a full locked-test UART validation:

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

- common inference interface with NumPy, UART-FPGA, and comparison backends;
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

The known-good clocking arrangement sends a direct 50 MHz reference clock to the PHY and uses a related 180° 50 MHz clock for MAC transmit logic, so RMII data updates occur between PHY sampling edges. Ethernet receive, ARP, IPv4, UDP, and classifier-result packets are future work.

## Repository layout

- `genomic-dataset-pipeline/` — data download, parsing, annotation, feature build, QAT training, evaluation, and FPGA export
- `rtl/` — synthesizable SystemVerilog for the triage accelerator, UART, and Ethernet TX
- `simulation/` — SystemVerilog testbenches and vector files
- `memory/` — exported quantized model parameters
- `software/` — FPGA validation utilities and VariantGate orchestration/evidence tools
- `reports/` — tracked data-quality, training, export, test, FPGA-validation, and routing-policy manifests
- `constraints/` — Nexys board and standalone Ethernet constraints
- `scripts/` — Vivado project-recreation script
- `runs/` — generated scoring/evidence outputs when retained for reproducibility

Raw databases, large generated datasets, trained checkpoints, Vivado build outputs, and bitstreams are excluded from normal Git history.

## Next steps

1. Finish the V2 full-set FPGA validation and preserve the same bit-exact evidence chain as V1.
2. Integrate the classifier, routing policy, and VariantGate workflow into a single reproducible end-to-end run.
3. Commit the known-good Ethernet clock/top-level revision and resolve the remaining `last_out` multiple-driver cleanup in the TX buffer.
4. Define a compact raw-Ethernet result payload and host decoder.
5. Add receive, ARP, static IPv4, and UDP only after the raw result protocol is stable.
6. Expand evidence-source coverage while preserving source identity, provenance, and disagreement reporting.
