# File Map

## Main package overview

- `README.md`
  Top-level explanation of what is in the package.

## Human annotations

- `human_annotations/human_annotations_500_ai_task_and_contribution_types.csv`
  The 500 human-annotated examples for the two coding questions, with `AI task type` and `AI contribution type` column names.

## Human annotation prompts

- `human_annotation_prompts/ai_task_type_annotation_prompt.txt`
  Human annotation instructions for the task-type question.

- `human_annotation_prompts/ai_contribution_type_annotation_prompt.txt`
  Human annotation instructions for the contribution-type question.

## LLM annotation prompts

- `llm_annotation_prompts/prompt_1a_ai_task_type.txt`
  Model prompt for the task-type annotation question.

- `llm_annotation_prompts/prompt_1b_ai_contribution_type.txt`
  Model prompt for the contribution-type annotation question.

## Connected dataset

- `dataset/comment_codeblock_first_change_dataset.jsonl`
  Compact connected dataset with the comment, code block, and first-change commit message, plus IDs and GitHub links.

- `dataset/summary.json`
  Record count summary for the compact connected dataset.

## Scripts

- `scripts/extract_codeblocks_from_commits.py`
  Script used to extract commit-derived code blocks from introduction commits.

- `scripts/run_llm_annotations.py`
  Script used to run large-scale LLM annotation over the comment/code-block dataset.

- `scripts/cluster_first_change_commits.py`
  Script used to cluster first-change commit messages with BERTopic.
