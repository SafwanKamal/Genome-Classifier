# Genome Classifier FPGA

An FPGA-assisted system for **genomic missense-variant triage**. The project couples a reproducible, quantized machine-learning pipeline with a deterministic SystemVerilog accelerator on the **Digilent Nexys 4 DDR (Artix-7 / Nexys A7-100T)**.

The FPGA produces a fast integer score for each variant. That score is intended to prioritize variants for deeper evidence review—not to make a clinical diagnosis. The broader project direction is an agentic, source-linked interpretation workflow in which the FPGA provides a low-latency first-pass triage stage.

## Current status

| Area | Status |
|---|---|
| Quantized V2 model (`16 → 8 → 1`) | Implemented and simulated bit-exactly |
| FPGA inference over UART | Verified on hardware |
| Ethernet RMII transmit path | Verified on hardware with Wireshark |
| Ethernet receive / ARP / IPv4 / UDP endpoint | Planned |
| Ethernet transport of classifier results | Planned |

## FPGA triage accelerator

The deployed accelerator evaluates a small quantized dense neural network using deterministic fixed-point arithmetic.

- **Inputs:** 16 ordered signed INT8 genomic features
- **V2 model:** `16 → 8 → 1`
- **Weights:** signed INT8, exported into memory files
- **Biases and accumulation:** signed INT32
- **Hidden activation:** ReLU, quantization shift, and INT8 saturation
- **Output:** signed INT32 triage score
- **Decision convention:** score `>= 0` is the model's pathogenic-side prediction

The RTL is designed as a scalable dense-engine path with packed weight/bias memories, synchronous ROM sequencing, and reused arithmetic rather than a separately hand-written datapath for every neuron.

### Verification results

- V2 behavioral simulation: **1,000 bit-exact vectors passed**
- Latency: **122 cycles per inference**
- Hardware UART smoke test: **100 / 100 FPGA–NumPy comparisons matched**
- Full held-out V1 UART verification: **27,477 / 27,477 bit-exact matches**, **0 mismatches**

The hardware checks validate feature ordering and signed representation, exported memories, MAC arithmetic, activation/quantization behavior, threshold handling, UART packet framing, CRC, and response byte order.

### Routing policy

The current routing policy uses the FPGA score to decide whether a variant should receive deep evidence review.

- Frozen routing threshold: **-276**
- Pathogenic recall on validation: **99.5099%**
- Deep-review fraction: **47.63%** (13,475 of 28,293 validation variants)
- Light-review fraction: **52.37%** (14,818 variants)

This is a prioritization policy: it deliberately keeps recall high while reducing the number of variants that need the most expensive downstream analysis.

## Ethernet bring-up

The project is adding a reusable, custom 100 Mb/s Ethernet transmit path instead of relying on vendor MAC IP. This interface is meant to become a reusable transport layer for classifier results and later FPGA projects.

### Hardware-verified milestone

The Nexys 4 DDR's LAN8720A PHY has negotiated a stable **100 Mb/s** Ethernet link, and the FPGA now sends an accepted raw Ethernet broadcast once per second.

Wireshark observed:

- Source MAC: `02:00:00:00:00:01`
- Destination MAC: `FF:FF:FF:FF:FF:FF` (broadcast)
- EtherType: `0x88B5` (local experimental)
- Ethernet frame length before FCS: 60 bytes
- Cadence: 1 frame/s

A useful capture filter is:

```
eth.type == 0x88b5 && eth.src == 02:00:00:00:00:01
```

### TX architecture

The transmit path is intentionally split into independent blocks.

- `rmii_TX.sv` serializes each byte into four 2-bit RMII dibits at 50 MHz.
- `ethernet_CRC32.sv` computes the Ethernet CRC-32 over the MAC frame and padding.
- `ethernet_TX.sv` adds the seven-byte preamble, SFD, minimum-frame padding, four-byte FCS, and minimum 96-bit inter-frame gap.
- `ethernet_TX_buffer.sv` stores a complete frame before transmission, supports ready/valid flow control, provides an overflow/discard path, and is structured to infer block RAM.
- `ethernet_test_top_100M_config.sv` is the current raw-frame hardware test top.
- `phy_mdio_config.sv` is retained for PHY-management experiments; the successful raw-TX configuration leaves the PHY in its board-default auto-negotiation mode.

The test frame contains broadcast destination, local source MAC `02:00:00:00:00:01`, EtherType `0x88B5`, and payload bytes `00` through `2D`.

### Timing and board lessons

The LAN8720A uses RMII and needs a continuous **50 MHz reference clock**. The known-good hardware arrangement is:

- Clocking Wizard generates a 50 MHz PHY-reference output and a related 180° 50 MHz MAC clock.
- The PHY reference clock is driven **directly** to the PHY.
- MAC transmit data changes on the phase-shifted clock, halfway between PHY sampling edges.
- PHY reset remains asserted for 50 ms while the reference clock is already running, then releases synchronously.
- The PHY's default auto-negotiation was sufficient for the verified 100 Mb/s link; MDIO configuration is not required for the raw-TX test.
- The XDC disables default pulls on unused FPGA inputs so they do not disturb PHY mode straps.

This direct-clock, two-phase approach replaced a forwarded-clock experiment that produced periodic link instability. It provides a clean phase relationship between RMII data updates and the PHY sampling clock.

### Debug indicators

For the raw Ethernet test top, the four LEDs are:

| LED | Meaning |
|---|---|
| LD0 | Clocking Wizard locked |
| LD1 | PHY reset released |
| LD2 | Toggles after each completed frame transmission |
| LD3 | Sticky TX underflow or buffer-overflow error |

On the LAN8720A itself, the link/activity LED indicates a valid link; the speed LED being asserted indicates the 100 Mb/s negotiated mode.

## Repository structure

- `rtl/` — synthesizable SystemVerilog: classifier, UART, Ethernet TX, FIFO, and support modules
- `memory/` — exported quantized model parameters
- `constraints/` — board constraints, including the standalone Ethernet test XDC
- `simulation/` — SystemVerilog testbenches
- `software/` — UART interface, model verification, and host utilities
- `genomic-dataset-pipeline/` — dataset construction, training, evaluation, quantization, and parameter export
- `reports/` — model manifests, routing policy, and evaluation outputs
- `scripts/` — Vivado project-recreation scripts

Generated datasets, raw databases, PyTorch checkpoints, Vivado build directories, and bitstreams are excluded from normal Git history.

## Building the Ethernet test

In Vivado, select `ethernet_test_top_100M_config` as the synthesis top and use only `constraints/ethernet_test_top.xdc` for the standalone Ethernet design. The project-recreation script currently targets the classifier top, so the Ethernet test is best created as a separate Vivado project or configured explicitly before synthesis.

Create the Clocking Wizard IP as documented in the Ethernet top-level comments:

- Input: 100 MHz
- Output 1: 50 MHz, 0° phase, PHY reference clock
- Output 2: 50 MHz, 180° phase, MAC/transmit clock

Program the board, connect it through a switch, and capture on a host connected to the same network segment. The raw experimental broadcasts do not require an IP address or a host-side application.

## Next steps

1. Commit the known-good direct-PHY-clock two-output Clocking Wizard top-level and matching timing constraints.
2. Remove the remaining duplicate procedural/continuous driver of `last_out` in `ethernet_TX_buffer.sv`.
3. Define a compact Ethernet payload for classifier inputs/results while keeping EtherType `0x88B5` during validation.
4. Add a host decoder and round-trip tests.
5. Add RMII receive, ARP, static IPv4, and UDP only after the raw result protocol is stable.

## Scope and research boundary

This repository is an engineering/research prototype. It performs reproducible computational triage from ClinVar-derived labels; it is not a validated clinical decision system. Any final interpretation should remain evidence-based, source-linked, and subject to qualified human review.
