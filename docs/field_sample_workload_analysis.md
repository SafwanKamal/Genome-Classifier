# Biological samples to field computation: workload sizing

October 3, 2026. Companion to `field_data_power_battery_analysis.md`. This addresses biological sample counts, sequencing depth and processing requirements rather than just interface bandwidth. All proposed batch sizes and compute service times are engineering scenarios, not observations from our equipment. We currently have public datasets and no sequencer.

## Proposed initial workload

Start with six environmental microbial samples per replay batch, then size for 24 samples, and stress-test 96. These are proposed workload targets, not an established field collection capacity. A sample is one separately identified specimen/library, not one DNA molecule, sequencing channel or read. Budget extraction blanks, positive controls, replicates and failed libraries separately; a 24-barcode run may consequently contain fewer than 24 field specimens.

Collection volume depends on material and assay. The [SituSeq field study](https://www.nature.com/articles/s43705-023-00239-3) extracted DNA from 0.5–1 g sediment per sample. At that extraction scale, 24 samples would mean processing 12–24 g sediment, though collection and retained material could be larger. This is a concrete research example, not a protocol for water, swabs or medical specimens. It reported roughly two hours of sequencing for six samples at 5,000 reads/sample using about 60 active Flongle pores. Its analysis ran on a standard laptop, with separate basecalling support in the field setup.

One gram of specimen does not determine bytes of sequencing. Extraction yield, amplification, library loading, sequencing time and chosen depth determine how many molecules are read. [ONT's current rapid genomic-DNA barcoding protocol](https://nanoporetech.com/document/rapid-sequencing-gdna-barcoding-sqk-rbk114) supports 24/96 barcodes and specifies 200 ng gDNA per barcode. That requirement belongs to that particular genomic-DNA protocol; it is not an input requirement for every amplicon or environmental assay.

## Data per sample and batch

Use decimal units. For uncompressed FASTQ, budget 2.4 bytes/base: one base character, one quality character and an illustrative 20% allowance for headers/format. Actual overhead and compression vary. Raw electrical signal is additional and is sized in the companion report.

| Research scenario | Target per sample | Sequence per sample | Reads in 24-sample batch | Sequence in batch | Estimated FASTQ in batch |
|---|---|---:|---:|---:|---:|
| Initial full-length 16S survey | 5,000 reads, mean 1,500 bases | 7.5 Mb | 120,000 | 180 Mb | 0.43 GB |
| Deeper 16S survey | 20,000 reads, mean 1,500 bases | 30 Mb | 480,000 | 720 Mb | 1.73 GB |
| Isolated bacterial genome | 5 Mb genome, illustrative 30x mean coverage; 5 kb reads | 150 Mb | 720,000 | 3.6 Gb | 8.64 GB |

The isolate example is not a claim that 30x guarantees assembly or accurate variants. Amplicon depth likewise does not guarantee species-level identification or comprehensive community characterization. These workloads answer different biological questions and cannot be substituted without changing the assay.

For a mixed shotgun sample, required total sequence for target coverage is approximately `coverage * target_genome_size / target_fraction_of_sequenced_bases`. A 5 Mb target at 30x needs 150 Mb target sequence. At 1% abundance by sequenced bases, it needs approximately 15 Gb total per specimen: 360 Gb and roughly 864 GB FASTQ for 24 specimens. Uneven coverage and extraction bias increase uncertainty. This is fundamentally different from merely detecting a target's presence.

Under an ideal independent read-sampling model, probability of observing at least one target read is `1-(1-q)^N`, where q is target fraction of reads. About `3/q` reads gives a 95% chance of at least one. At q=0.1%, this is about 3,000 reads; at q=0.01%, about 30,000. With 5,000 reads at q=0.01%, it is only 39%. This is sampling arithmetic, not a validated detection limit: contamination, primer bias, taxonomic ambiguity and multi-read confirmation are additional issues. FPGA speed cannot recover molecules that were never sequenced.

## Acquisition time and concurrent processing

A sizing point of 330 million usable bases/hour corresponds approximately to 256 occupied channels at 450 bases/second and 80% retained yield. This is an illustrative operating point using the companion report's rate assumptions, not a manufacturer yield guarantee or measured field rate. It is 91,667 bases/second.

At that point, ideal collection times for the 24-sample table are 33 minutes, 2.2 hours and 10.9 hours respectively. The rare-target shotgun case takes about 1,091 hours on one such stream. These exclude preparation and assume balanced sample allocation and sustained yield; they are capacity calculations, not promised turnaround. Actual field runs can be much slower, as the SituSeq example demonstrates.

At a fixed flow-cell throughput, moving from six to 24 equally represented specimens reduces the average allocation per specimen fourfold. It does not increase aggregate ingress fourfold. Four separate flow cells do increase aggregate throughput approximately fourfold if yield scales. Barcodes identify mixed samples; they are not separate acquisition devices.

For the illustrative 330 Mb/hour stream:

- Mean 1.5 kb reads: approximately 61 completed reads/second, across the entire pool.
- Mean 5 kb reads: approximately 18 completed reads/second.
- At 24 balanced barcodes, 1.5 kb reads average 2.5 reads/second per specimen; skewed barcodes can differ substantially.
- FASTQ generation is approximately 0.22 MB/second, readily below 100 Mb/s Ethernet bandwidth with efficient transport. Raw signals have a different budget.

The total batch may be gigabytes, but it need not reside in working memory. Process reads as they arrive and retain per-sample counts/coverage, model state, reference indexes and a bounded queue. Whole-genome assembly and consensus may require much larger working sets than streaming screening.

## How much CPU parallelism helps

Let arrival rate be lambda reads/second, serial CPU service cost t seconds/read and target utilization U. Approximate required independent worker equivalents as `ceil(lambda*t/U)`. This assumes jobs can be partitioned and ignores contention; benchmark actual stages, references and hardware.

At 61 reads/second:

| Hypothetical serial processing cost | CPU-seconds per wall second | Workers at 50% target utilization |
|---|---:|---:|
| 1 ms/read | 0.061 | 1 |
| 10 ms/read | 0.61 | 2 |
| 100 ms/read | 6.1 | 13 |

These are sensitivity estimates, not measured basecalling, alignment or ARM performance. Four independent streams multiply the work fourfold, whereas more barcodes in the same stream primarily multiply bookkeeping. Hardware acceleration helps when it reduces the cost of a bottleneck stage; adding fast inference does not accelerate unaffected basecalling, mapping, database lookup or assembly.

The existing physical V4 benchmark of 127,499 feature vectors/second would be vastly above a once-per-read decision rate of 61/second, **if** the workload used already prepared inputs to the same network. It does not establish that this network can classify sequences. The current model consumes 16 annotated variant features. A read/signal model and its feature generation must be selected, trained and measured separately.

Useful parallelism can instead be repeated decisions per active pore, simultaneous stages and multiple devices. The companion report's 512-channel example at one update per 100 ms demands 5,120 decisions/second, with channel-specific state and bursts. That is a different workload from scoring each completed read once. More lanes reduce backlog only until the next stage becomes limiting.

## Input and next experiment

Begin with basecalled reads from a supported acquisition host or public FASTQ data. The ARM parses records, associates barcodes/sample identifiers, batches work and maintains aggregates; DMA sends compact inputs to a shared PL pipeline. Ethernet carries bulk records rather than a separate exchange for every base. The current variant-feature Ethernet protocol needs a new sequence input path. Raw-signal POD5 replay is a separate experiment and must include signal processing/basecalling costs and valid sample association. Do not assume the KR260 replaces a supported sequencer host.

For the central LLM, schedule a report per sample and evidence-driven updates rather than a query per read. Twenty-four final reports plus four intermediate updates per specimen gives 120 requests per batch, versus 120,000 reads for the initial survey. This preserves powerful-model centralization but does not itself prove FPGA necessity.

The next experiment should replay a six-sample public 16S dataset, then 24/96 sample groups with preserved identities. If multiplex identities are unavailable, distinguish synthetic grouping from actual barcoded runs. Measure quality filtering, barcode handling when applicable, sequence features/search, screening and aggregation separately. Replay at real-time timestamps when available or explicitly imposed rates, then at 4x/16x speed; report queue growth, p95/p99 decision latency, peak memory and energy per processed base/read. Compare a CPU baseline with the same decisions and accuracy targets.

For modest single-device microbial surveys, data quantity alone does not establish a need for FPGA inference. A stronger justification requires a costly continuous stage, repeated low-latency decisions, multiple acquisition devices or demonstrated energy savings over an adequate embedded CPU baseline.
