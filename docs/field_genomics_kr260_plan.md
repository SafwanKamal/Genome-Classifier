# Field genomics on KR260: research direction and staged prototype

October 3, 2026. The user is acquiring a KR260 and wants to investigate low-power field processing. Initial data access is public datasets; no sequencer is currently available. This document proposes experiments, not established product performance or customer demand.

**Clarified model-protection objective:** the user's privacy concern is distributing powerful proprietary LLMs to field sites, where weights or capabilities may be copied, extracted or distilled. The biological-data confidentiality modes below are a separate possible requirement, not the user's stated model-protection rationale.

## Model-protection architecture

Keep the powerful LLM and its proprietary weights on an operator-controlled central server. Field devices receive a narrowly scoped screening model or deterministic filter, local buffering, and authenticated access to a task-specific central service. Return structured findings with citations rather than expose an unrestricted general-purpose LLM endpoint. Treat the local model as potentially recoverable and distribute only capabilities whose compromise is acceptable.

This removes distribution of the central weights but does not eliminate black-box extraction or distillation from service responses. Model-extraction research demonstrates that query access itself can reveal model behavior ([USENIX research](https://www.usenix.org/conference/usenixsecurity20/presentation/jagielski)); LLM research has also recovered part of a production model under particular API conditions ([primary paper](https://arxiv.org/abs/2403.06634)). Task restrictions, authorization, quotas, monitoring and limiting unnecessary output detail reduce exposure but are not guarantees.

FPGA deployment can support encrypted/authenticated configuration and controlled interfaces, but does not inherently prevent behavior cloning. Secure boot protects boot/configuration integrity and confidentiality within its design; it does not prove all runtime model weights or physical access are protected ([AMD boot security](https://docs.amd.com/r/en-US/ug1283-bootgen-user-guide/Boot-Time-Security)). Any local proprietary model requires its own physical/runtime threat assessment.

Model protection justifies separating field and central capabilities. FPGA acceleration separately needs a measured power/deadline advantage over the KR260 ARM baseline. A disconnected field device provides the local screen and queued requests; it cannot provide the central LLM's full capability without distributing a model capable of doing that work. If biological payloads also cannot leave the site, central processing requires a separate solution such as an institution-controlled local server or a specifically evaluated privacy-preserving computation approach.

## Proposed scope

Develop a local genomic screening gateway that processes incoming reads, maintains a local evidence summary, retains original data, and permits only policy-approved exports. First demonstrate it with replayed public basecalled reads and a narrow microbial target panel. Then evaluate raw-signal screening if suitable signal data are available. Preserve the existing human missense-variant project as a separate validated task.

Two potential applications share the platform but need distinct evaluation:

- Environmental/agricultural microbial research: recognize a bounded set of targets, report emerging evidence, and prioritize further analysis while sampling.
- Sensitive host-containing samples: retain human/uncertain material locally and export only explicitly approved results or data. A classifier is not sufficient to guarantee that exported sequences contain no human information.

The first observable decision is 'target evidence is emerging; prioritize this sample/read for local or approved further analysis,' not a diagnosis or a clinical resistance prediction.

## Why KR260 changes the architecture

The K26 processing system includes quad-core Cortex-A53 application processors and dual Cortex-R5F real-time processors ([AMD processing-system specification](https://docs.amd.com/r/en-US/ds987-k26-som/Processing-System)). The KR260 includes 4 GB DDR4, four Gigabit Ethernet interfaces split between PS and PL, and an SFP+ interface supporting 10GigE ([kit specification](https://docs.amd.com/r/en-US/ds988-kr260-starter-kit/Product-Details)). Faster interface support still requires the corresponding transceiver/module, logic, software and integration; it is not automatic application throughput.

Use ARM Linux for file/stream parsing, storage, policy, networking, provenance and local reporting. Attach programmable logic through an AXI/DMA/shared-memory path for the measured expensive streaming kernel. This replaces desktop-to-FPGA feature round trips in the intended local deployment. Cortex-R5F can be considered later if a specific control task requires tighter timing; it is not necessary for the first replay.

Benchmark ARM-only against ARM+FPGA on the same board, with identical input, model and accuracy. The FPGA earns its role only if useful deadlines, measured energy or throughput improve after DMA, preprocessing, buffering and validation are included.

KR260 is an actively cooled development platform. AMD lists K26 SOM typical power around 7.5 W and maximum around 15 W in its [SOM product table](https://docs.amd.com/api/khub/documents/lwwUFdfst1gWnYUxm9b4PA/content); these are not measured total KR260 workload power. Include carrier, storage, PHYs, cooling, acquisition host, sequencer and conversion losses in a field power budget. Do not promise sub-watt or all-day battery operation from an FPGA chip estimate. A smaller production carrier or device may follow a successful workload demonstration.

## Acquisition constraint

The current [MinION Mk1D requirements](https://nanoporetech.com/document/requirements/minion-mk1d-device-and-it-specifications) do not support arbitrary ARM processors as acquisition hosts. KR260 should initially be a replay/gateway device, not a promised direct USB replacement for the sequencer's supported control computer. A future live demonstration can receive data from a supported acquisition host over a local connection. That host remains inside power, privacy and cost boundaries.

Public FASTQ replay establishes basecalled-read screening only. It does not establish raw-signal processing, basecalling energy savings, live sequencing control, or molecule rejection timing. File batches may also hide acquisition delay. Label reconstructed arrival schedules as simulated unless actual timestamps are available.

## Data and model options

| Input | Field-accessible information | Candidate task | Limitation |
|---|---|---|---|
| FASTQ/BAM reads | Sequence, qualities, length; optional barcode/run metadata | Target-panel membership and uncertainty | Upstream basecalling has already occurred |
| Raw nanopore signal | Current samples, calibration, channel/read timing when supplied | Host/background/target screening before full decoding | New signal model and preprocessing; chemistry/run shift |
| Accumulated read evidence | Target hit counts, coverage summaries, confidence over time | Continue sampling, prioritize local analysis, summarize progress | Evidence thresholds need task-specific validation |

Do not replace ClinVar labels with arbitrary field labels. Define a prediction target and obtain traceable labels through known mock-community composition, reference alignment, experimental provenance or barcodes, recording ambiguities. Split training/evaluation by sample/run and held-out organism/strain as appropriate, rather than random fragments from the same read. Prevent adapter/barcode leakage and evaluate different preparations and sequencing conditions.

The existing dense inference RTL, integer arithmetic checks and manifests are reusable engineering assets. The current pathogenicity weights and annotation features are not reusable for read classification. Test compact k-mer features plus a dense model against direct membership/matching rules. Use a small convolutional signal/sequence model if compact features fail; do not force sixteen inputs or add layers purely to favor FPGA benchmarks.

## Public starting points

1. [EPI2ME wf-metagenomics demo data](https://epi2me.nanoporetech.com/workflows/wf-metagenomics/): documented small real/simulated read datasets for exercising input parsing and reference workflows. Suitable for an initial replay smoke test, not a sufficient independent training/evaluation cohort. The full reference workflow's documented minimum is 16 GB RAM, exceeding KR260's 4 GB; run full reference analysis on a desktop and use a bounded local panel on KR260.
2. [SquiggleNet publication](https://link.springer.com/article/10.1186/s13059-021-02511-y) and [author code](https://github.com/welch-lab/SquiggleNet): host/bacterial signal-classification research, pretrained models and study accession SRP296988. Verify actual raw-signal files, calibration, labels, access terms and archive availability before downloading. Sequence-only archive records are not a substitute for current traces. Older FAST5 data and chemistry are useful for research replay, not proof on a current sequencer.
3. [SituSeq field study](https://www.nature.com/articles/s43705-023-00239-3): demonstrates offline portable 16S analysis on a laptop. This supports field use and supplies a relevant CPU-oriented comparison, not an FPGA advantage.

Use the desktop for trusted reference labeling/training and evaluation; deployment can carry frozen models and reference subsets. Ground truth produced centrally during development does not require remote labeling during field operation.

## Privacy and control modes

- **Local-only:** process and retain reads locally; do not export biological payloads. Reference/model updates are a separate explicit operation. Remote literature or LLM requests must also obey the export policy because prompts and search terms can disclose sensitive information.
- **Summary export:** transmit approved aggregate findings/provenance. Do not call summaries anonymous by default; rare targets, location, timestamps and sample context can be revealing.
- **Selected-data export:** send only explicitly authorized data to authorized recipients. Classification is advisory; run an independent local identity/filter check before export when host exclusion is required. Hold unknown/ambiguous reads locally. If screening cannot establish eligibility, do not release raw data automatically.

Keeping data local supports confidentiality and control, but does not determine legal ownership, consent, intellectual-property rights or permitted downstream use. Medical-team demand is a hypothesis requiring interviews. Plant/breeding or industrial microbial genomes are another plausible proprietary-data niche.

AMD documents hardware root-of-trust, secure/measured boot and TPM support ([K26 security features](https://docs.amd.com/r/en-US/ds987-k26-som/Security-Features)). Verify the exact kit/SOM revision and supported security functions, then configure and test them. Hardware capability does not mean the prototype ships securely configured. Use authenticated communication, encrypted storage where required, access controls, signed updates and explicit export logging. No irreversible fuse programming belongs in the first prototype.

## Stages and success measurements

1. **Desktop public-read replay:** freeze source/version/hashes, label provenance and a held-out evaluation split. Implement a bounded target screen, local archive, uncertainty and deterministic export policy. Compare against simple rules. Use replay speed and network/central-compute limits as experimental controls.
2. **KR260 ARM-only replay:** run the same pipeline locally. Measure sustained preprocessing-to-decision latency, p95/p99 under bursts, memory/storage, bytes eligible for export and board input energy. Establish failure behavior for network loss, full queues and storage exhaustion.
3. **FPGA kernel integration:** accelerate the dominant measured kernel through AXI/DMA. Reuse integer validation where applicable. Require equivalent model results and evaluate target recall, host leakage and unknown handling in addition to throughput. Measure total ARM+PL board energy over the same useful workload.
4. **Raw-signal branch:** only if usable public traces are confirmed and pre-basecalling decisions offer a material benefit. Compare raw-signal processing against a baseline with basecalling costs included. Keep separate results from the FASTQ branch.
5. **Live partner demonstration:** supported acquisition host → local KR260 gateway → local decision and approved optional export. Include extraction/library preparation, collection timing, environmental constraints and the complete battery budget. Obtain field data only through the partner's applicable process.

Acceptance criteria should be fixed before tuning: target sensitivity with sample-level uncertainty, earliest reliable target indication, false alerts, host reads/bytes released under policy, unknown reads held, missed deadlines, sustained backlog and joules per useful decision. Absolute rates and thresholds depend on the task and hardware measurements; they are not yet established.

A successful first milestone is a public microbial-read stream producing a local target/uncertainty summary, with recorded provenance and an export policy that releases no raw sequences by default. A successful hardware milestone demonstrates that FPGA acceleration improves the ARM baseline within an explicit battery and deadline budget.
