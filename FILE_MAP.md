# File Map

## Main package overview

- `README.md`
  Top-level explanation of what is in the package.

## Human annotations

- `human_annotations/human_annotations_500_ai_task_and_contribution_types.csv`
  The 500 human-annotated examples for the two coding questions, with `AI task type` and `AI contribution type` column names.

- `human_annotations/open_coding_annotation_guidelines.pdf`
  The original open-coding guideline used for manual coding, axial coding, and taxonomy development.

## LLM annotation prompts

- `llm_annotation_prompts/prompt_1a_ai_task_type.txt`
  Model prompt for the task-type annotation question.

- `llm_annotation_prompts/prompt_1b_ai_contribution_type.txt`
  Model prompt for the contribution-type annotation question.

## Dataset files

- `dataset/comment_codeblock_dataset.jsonl.gz`
  Full block-bearing dataset with the AI-referencing comment, the extracted code block, and the identifiers and links needed to trace each record.

- `dataset/comment_codeblock_first_change_dataset.jsonl.gz`
  Connected subset with the comment, code block, and first-change commit message, plus IDs and GitHub links.

- `dataset/comment_codeblock_without_first_change_dataset.jsonl.gz`
  The subset of block-bearing records for which a first-change commit was not successfully collected.

- `dataset/summary.json`
  Record count summary for the dataset files.

## Scripts

- `scripts/extract_codeblocks_from_commits.py`
  Script used to extract commit-derived code blocks from introduction commits.

- `scripts/run_llm_annotations.py`
  Script used to run large-scale LLM annotation over the comment/code-block dataset.

- `scripts/cluster_first_change_commits.py`
  Script used to cluster first-change commit messages with BERTopic.
