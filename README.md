# Replication Package

This repository contains the replication package for the paper. It is organized around five components:

1. `human_annotations/`
   Human annotations for the two coding questions, adapted from the `oss-llm` repository's 500-comment annotation set. The column names use `AI task type` and `AI contribution type` wording.

2. `human_annotation_prompts/`
   Text versions of the human-annotation prompts, rewritten to use `AI` wording while preserving the original content and category definitions.

3. `llm_annotation_prompts/`
   The prompt templates used for large-scale model annotation in this project, with `AI` wording substituted for `LLM` wording.

4. `dataset/`
   Gzipped JSONL dataset files for both:
   - the full AI-referencing `comment + code block` corpus
   - the connected subset that also includes a first-change commit
   - the subset without a successfully collected first-change commit
   along with GitHub links and identifiers for traceability.

5. `scripts/`
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

## Notes

- The dataset files are stored as gzipped JSONL for easier distribution while staying within repository size limits.
- The copied scripts are preserved as runnable research artifacts; some of them still point to the original study input/output layout, so they should be treated as provenance-preserving script copies rather than a fully re-wired standalone package.
