# FPGA application feasibility study

October 3, 2026. Based on repository results and primary-source research. Rankings below are engineering judgments and customer hypotheses, not validated market demand. No hardware, model, or existing project files were changed for this study.

## Recommendation

The current system is a credible research prototype, but the evidence does not support selling an FPGA appliance principally to accelerate annotated human missense-variant scoring. Preserve VariantGate as a software-first triage/evidence workflow with optional hardware. For the FPGA, investigate a decision made directly on streaming data: a small targeted sequence filter is the closest genomics extension; instrument event classification and triggering is the strongest adjacent hardware fit, provided a lab partner can supply data and integration access.

Adaptive nanopore sampling is an attractive longer-term genomics direction, but replacing only the final decision score is unlikely to help if basecalling, mapping, or host control dominates the response time.

## What the project actually establishes

| Evidence | Finding | Feasibility implication |
|---|---|---|
| V4 physical streamed test | 27,477/27,477 exact scores; 127,499 completed variants/s; 215.51 ms | Transport and inference work correctly on the tested cohort |
| Same-desktop V4 CPU benchmark | 768,944 variants/s; 35.73 ms; median of five warmed runs | Best tested CPU configuration is 6.03 times faster than the recorded FPGA path |
| CPU with one thread, batch 64 | 577,379 variants/s | CPU advantage is not solely a four-thread comparison |
| Old 200 MHz-target board runs | Three full runs: 141,080–155,245/s, median 154,051/s; exact scores | Promising functional evidence, but old full implementation failed setup timing; not an accepted 200 MHz operating point |
| Revised 200 MHz core | Functional simulation passes; core-only synthesis passes; full routed timing pending | Approximately 306,740/s simulated queued capacity remains a conditional ceiling |
| Current transport | Ideal 32-record/100 Mb/s request wire ceiling approximately 714,286/s | Faster core alone cannot beat the best measured CPU under this protocol |
| V4 quality versus V3 | AP 0.977264 versus 0.977759; F1 0.927797 versus 0.925249 | Additional compute did not establish a consistently better biological model |
| FPGA power | 0.870 W estimated on-chip, low confidence | No measured board or whole-system energy advantage |

CPU timings include prepared-feature inference and batch orchestration; FPGA timings include prepared-request transport and completed replies. Both exclude preparation. This is a useful offload comparison, not a pure arithmetic comparison or a full sample-to-report benchmark. The older FPGA reports do not independently capture host/model identity; the same-desktop attribution comes from project documentation. Long sustained matched repetitions remain desirable.

Local evidence: [CPU benchmark](benchmark_v4_cpu.md), [desktop CPU JSON](../reports/model_v4_cpu_desktop.json), [physical streamed JSON](../reports/model_v4_capture_precise_desktop.json), [200 MHz status](../rtl/mhz200/README.md), [latest timing fix](bottleneck_implementation_report.md), [model quality](model_v4_batch_report.md), and [power scope](accelerator_next_steps.md).

## Why variant interpretation is a weak standalone hardware target

The FPGA receives sixteen prepared annotation features. The frozen V4 manifest includes CADD, REVEL, AlphaMissense, ESM1b, conservation, population frequency, and gene constraint. It does not derive these predictors from sequence, perform read alignment, call variants, or gather literature. Consequently, it accelerates the last small combination of information that has already been obtained elsewhere.

The workflow is approximately sequencing → basecalling/alignment → variant calling → annotation → score → evidence review. This implementation addresses the score. We have not profiled the full workflow sufficiently to establish where its time goes. Nevertheless, scoring 27,477 prepared rows already takes about 36 ms on the tested CPU, so a large practical benefit needs a specific unmet workload requirement.

As an illustration, at the observed rates one million prepared rows require approximately 1.30 seconds on CPU and 7.84 seconds through the measured FPGA path, assuming those rates sustain. These are extrapolations, not million-row measurements. They count model-eligible annotated rows, not every variant in a whole genome.

Even a hypothetical infinitely fast scorer gives only 1.01 times total speedup if scoring accounts for 1% of a workflow. This is Amdahl's law, not a measured timing breakdown for this repository.

Model validity is also distinct from hardware correctness. Gene-disjoint evaluation is useful, but it does not establish prospective performance on uncertain variants, different clinical cohorts, missing annotations, or new database releases. Audit overlap between the evaluation labels and training data for constituent predictors. Combining predictors also does not create independent evidence. ClinGen's computational-evidence recommendations emphasize calibrated tools; our triage score must not automatically be assigned an ACMG evidence strength. See [ClinGen recommendations](https://clinicalgenome.org/docs/calibration-of-computational-tools-for-missense-variant-pathogenicity-classification-and-clingen-recommendations-for-pp3-bp4-cri/).

## Niches within the current problem

| Candidate user | Actual value proposition | FPGA case today | Evidence needed |
|---|---|---|---|
| Research variant-curation teams | Prioritize review and preserve source provenance/conflicts | Weak; software can supply the same workflow | Reviewer time saved, missed important variants, comparison with a single existing predictor and simple rules |
| Offline or restricted-network research labs | Local, reproducible scoring and cached evidence | Conditional; CPUs also operate offline | Maintained local databases, model/database versioning, measured whole-system cost and energy |
| Cohort reanalysis services | Repeated prioritization under new evidence | Weak for scoring alone | Full-workflow profile showing a real bottleneck; unique versus repeatedly scored variants; caching baseline |
| Embedded instrument vendors | Fixed model integrated near data acquisition | Conditional, but strongest hardware niche in this problem | Locally available features and a deadline/power requirement that CPU or MCU cannot economically meet |
| FPGA/bioinformatics teaching labs | Reproducible demonstration of quantization, RTL, transport, provenance | Strong educational fit | Documentation and reproducibility; revenue/demand still untested |

Rare-disease triage is a plausible software niche. It needs patient phenotype, inheritance, segregation and disease context beyond this score. A human missense model should not be repurposed directly for somatic cancer interpretation, microbial resistance, structural variants, or noncoding variants without a new task definition and validation.

Privacy, repeatability and deterministic integer outputs are useful properties but do not by themselves establish FPGA differentiation: the CPU reference already reproduces the integer scores exactly.

## Adjacent opportunities

Ratings express fit for our skills and architecture, not readiness for deployment.

| Opportunity | FPGA fit | Reuse | Main obstacle | Position |
|---|---|---|---|---|
| Targeted read filtering for contamination/pathogen research | Moderate to high if it avoids costly downstream work | Stream transport, buffering, integer logic, validation | New k-mer/minimizer extraction and reference lookup; memory capacity/bandwidth | Best first genomics experiment |
| Adaptive nanopore accept/reject decisions | High architectural potential | Scheduling, buffering, compact inference | Basecalling/mapping/control may dominate; live integration and early-read accuracy | Best longer-term genomics direction with a partner |
| Flow-cytometry or microfluidic event classification/triggering | High | Compact quantized inference, predictable core timing, control logic | Sensor acquisition, new features/model, actuator timing, lab access | Strongest adjacent instrument fit |
| Selected alignment/pre-alignment kernels | High algorithmic fit | RTL engineering and verification | Different datapath and memory architecture; established competition | Strong research topic, substantial rebuild |
| Full mapping/variant-calling appliance | Established FPGA category | Limited direct reuse | Scale, memory, interfaces, software ecosystem and validation | Poor first product target for this board |

**Targeted read filtering.** Start with a bounded target panel: a few organisms or contamination signatures, with background and unknown reads. Process sequence into compact counts or membership results on the FPGA, then forward suspicious/uncertain reads to a more complete software analysis. A conservative filter must retain ambiguous reads. Screening is not proof of infection or antimicrobial resistance. Broad classifiers such as [Kraken 2](https://link.springer.com/article/10.1186/s13059-019-1891-0) use substantial reference data structures; a tiny neural score alone does not reproduce that function. Compare against a CPU implementation of the same filter and an appropriate established classifier, including database size, recall and downstream work avoided.

**Adaptive sampling.** A decision can reject an off-target molecule while it is still being sequenced. [Readfish](https://www.nature.com/articles/s41587-020-00746-x) demonstrates this approach. Oxford Nanopore's [current guide](https://nanoporetech.com/document/adaptive-sampling) describes real-time compute requirements and warns that delays and resource contention can reduce enrichment. This creates an actual decision deadline, but existing vendor/software implementations already address it. The project would need early-read or signal features and a new keep/reject/defer policy; pathogenicity features are not available at that stage. Measure the whole path from an available signal/read chunk through device command, plus target yield and erroneous rejection. A faster final classifier is insufficient if it does not improve this path.

**Instrument sorting.** A cell or particle moving toward an actuator supplies a physical decision window. A small network operating directly on acquired features is much closer to our reusable core. A published [FPGA cell-sorting study](https://pubs.rsc.org/en/content/articlehtml/2026/dd/d5dd00345h) reports 14.5 microseconds inference and 24.7 microseconds detection-to-trigger for its particular implementation. Those numbers demonstrate another system, not ours. Begin with compact pulse/morphology features if feasible; a full image pipeline requires more work. Acquisition and triggering should be local to the FPGA. Windows/Python Ethernet round trips cannot be assumed to provide a hard real-time deadline.

**Upstream genome processing.** Illumina's [DRAGEN server](https://emea.illumina.com/products/by-type/informatics-products/dragen-secondary-analysis/server.html) applies FPGA acceleration to mapping/alignment and other secondary-analysis stages. This establishes that genomics can benefit from FPGAs, while also showing that we would enter a mature category. The current Artix-7 prototype and annotation scorer are not a scaled-down replacement for that platform.

## Investment gates and next study

1. Preserve the functioning hardware as a validated reference. Accept the revised 200 MHz design only after full routed timing and matched physical tests; treat this as a bounded engineering experiment rather than proof of market viability.
2. Profile one realistic VariantGate workflow: annotation/preparation, scoring, evidence retrieval, reconciliation and reviewer work. Benchmark scoring against direct predictor thresholds, simple rules and caching. Measure useful review reduction at an agreed recall, including independent/temporal data.
3. Identify an actual research/instrument partner and obtain its workload, interfaces, tolerated errors, required deadline and existing CPU/GPU/MCU baseline. No customer interviews have been conducted for this study.
4. For the first genomics pivot, replay real reads through a small targeted filter, including background organisms and held-out strains. Measure preprocessing-to-decision throughput, reference memory, target recall, uncertain-read forwarding and expensive analysis avoided. Raw-read feature extraction must be inside the comparison boundary.
5. If a sorter partner exists, prioritize event classification instead. Replay timestamped real events first, then measure sensor-to-trigger latency and missed deadlines under load. Biological accuracy and actuator outcome matter alongside bit-exact arithmetic.
6. Measure total system energy and cost: host, FPGA, PHY, supplies, maintained databases and engineering/support. Compare a deployable CPU/MCU option, not only a desktop. The current 0.870 W chip estimate is not this measurement.

Proceed with a hardware product only if it satisfies a user-valued requirement better than the deployable alternative: fewer missed deadlines, lower measured energy/cost, greater target yield, or substantial full-workflow speedup at acceptable accuracy. Choose quantitative acceptance thresholds with the partner before tuning. Stop hardware scaling for the current scorer if its only benefit remains a better FPGA benchmark.

The most defensible near-term strategy is to retain VariantGate's software/evidence value, use a targeted streaming genomics filter to test a new hardware rationale, and pursue direct instrument classification if suitable lab access is available.
