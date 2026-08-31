# Data directories

These directories are intentionally empty in Git.

- `raw/`: immutable downloaded source files, including the ClinVar VCF.
- `interim/`: labeled variants, annotation tables, and split assignments.
- `processed/`: float and INT8 model tables plus fitted preprocessing metadata.

Do not commit third-party annotation databases or patient data. Record every source release,
download date, genome assembly, checksum, and license in your experiment notes.

The annotation join expects one row per normalized GRCh38 allele, keyed as:

```text
chromosome:position:reference:alternate
```

For example: `17:43071077:A:G`. Chromosome names are stored without the `chr` prefix.
