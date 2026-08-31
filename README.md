# Genome Classifier FPGA

An FPGA-assisted genomic missense-variant triage system implemented on the Nexys A7-100T. A reproducible Python pipeline builds a ClinVar-derived dataset, trains a quantization-aware neural network, exports integer parameters, and verifies the deployed FPGA over UART.

## V1 status

Version 1 deploys a quantized `16 → 4 → 1` neural network.

- Dataset: 184,186 labeled ClinVar variants
- Genes: 13,989
- Split policy: gene-disjoint training, validation, and test sets
- Input: 16 ordered signed INT8 genomic features
- Hidden layer: four INT8-weight neurons with INT32 accumulation
- Activation: ReLU, `QSHIFT=4`, and INT8 saturation
- Output: signed INT32 score
- Classification rule: score `>= 0`
- Original validation threshold: 75
- Threshold-folded output bias: `-73`

## V1 test results

- Held-out variants: 27,477
- Accuracy: 95.054%
- ROC-AUC: 0.98693
- Average precision: 0.97076
- F1: 0.91908
- True negatives: 18,400
- False positives: 756
- False negatives: 603
- True positives: 7,718

The test set is locked and was not used for model selection or parameter tuning.

## Physical FPGA verification

The complete held-out test set was transmitted to the programmed FPGA over UART.

- Bit-exact matches: 27,477
- Bit-exact mismatches: 0
- Board: Nexys A7-100T
- Clock: 100 MHz
- UART: 115,200 baud

This verifies the signed feature representation, weight memories, biases, multiply-accumulate arithmetic, quantization, threshold folding, packet protocol, and response byte order.

## Repository structure

- `rtl/` — synthesizable SystemVerilog
- `memory/` — exported INT8 neuron weights
- `constraints/` — Nexys A7 constraints
- `software/` — FPGA UART interface and hardware validation
- `genomic-dataset-pipeline/` — dataset, training, evaluation, and export code
- `reports/checkpoint_v1/` — V1 manifests and validation reports
- `scripts/` — Vivado project-recreation scripts

Raw databases, generated Parquet datasets, trained PyTorch checkpoints, Vivado build outputs, and bitstreams are excluded from normal Git history.
