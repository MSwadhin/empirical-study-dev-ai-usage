# Replication Package

This repository contains the replication package for the paper. It is organized around five components:

1. `human_annotations/`
   Human annotations for the two coding questions, adapted from the `oss-llm` repository's 500-comment annotation set. The column names use `AI task type` and `AI contribution type` wording.

2. `human_annotation_prompts/`
   Text versions of the human-annotation prompts, rewritten to use `AI` wording while preserving the original content and category definitions.

3. `llm_annotation_prompts/`
   The prompt templates used for large-scale model annotation in this project, with `AI` wording substituted for `LLM` wording.

4. `dataset/`
   A compact connected dataset containing only:
   - the AI-referencing code comment
   - the extracted code block
   - the connected first-change commit message
   along with GitHub links and identifiers for traceability.

5. `scripts/`
   The main scripts needed to reproduce the key computational stages:
   - code-block extraction from introduction commits
   - LLM-based annotation
   - first-change commit clustering

This repository intentionally excludes semantic grouping, since that stage is meant to be performed manually by human researchers.

## Counts

- Human annotations: `500`
- Connected comment + code block + first-change commit records: `12,996`

## Provenance

- Human annotations and the original annotation prompts were taken from the public repository:
  `https://github.com/MSwadhin/oss-llm/tree/main`
- The connected dataset and scripts come from the study workspace used to build the paper artifacts.

## Notes

- The compact dataset is stored as JSONL for easier scripting and streaming.
- The copied scripts are preserved as runnable research artifacts; some of them still point to the original study input/output layout, so they should be treated as provenance-preserving script copies rather than a fully re-wired standalone package.
