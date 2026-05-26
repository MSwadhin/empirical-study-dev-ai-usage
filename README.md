# Empirical Study on the Characteristics and Evolution of AI-usage in GitHub Repositories: Evidence from Code Comments

This repository contains the replication package for the paper. It is organized around four components:

1. `human_annotations/`
   Human annotations for the two coding questions, 500-comment annotation set. This folder also includes the original open-coding guideline used for manual taxonomy development.

2. `llm_annotation_prompts/`
   The prompt templates used for large-scale model annotation.

3. `dataset/`
   Gzipped JSONL dataset files for both:
   - the full AI-referencing `comment + code block` corpus
   - the connected subset that also includes a first-change commit
   - the subset without a successfully collected first-change commit
   along with GitHub links and identifiers for traceability.

4. `scripts/`
   The main scripts needed to reproduce the key computational stages:
   - code-block extraction from introduction commits
   - LLM-based annotation
   - first-change commit clustering

This repository intentionally excludes semantic grouping, since that stage is meant to be performed manually by human researchers.

## Counts

- Human annotations: `500`
- Total comment + code block records: `35,278`
- Connected comment + code block + first-change commit records: `12,996`
- Comment + code block records without a successfully collected first-change commit: `22,282`

