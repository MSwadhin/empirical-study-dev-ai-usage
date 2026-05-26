# Scripts

This folder contains the three main scripts requested for the replication package.

- `extract_codeblocks_from_commits.py`
  Extracts comment-associated code blocks from introduction commits.

- `run_llm_annotations.py`
  Runs prompt-based LLM annotation over comment/code-block records.

- `cluster_first_change_commits.py`
  Clusters first-change commit messages with BERTopic.

These files are copied from the working project so their logic is preserved as used in the study. They are included here as research artifacts for inspection and rerunning, but some default paths still point to the original project layout rather than this package folder.

## Package-local usage notes

### 1. Run LLM annotations on the compact dataset

Use `run_llm_annotations.py` with the package-local dataset and prompt files, for example:

```bash
python3 scripts/run_llm_annotations.py \
  --model <MODEL_NAME> \
  --prompt-path llm_annotation_prompts/prompt_1a_ai_task_type.txt \
  --input-jsonl dataset/comment_codeblock_first_change_dataset.jsonl \
  --output-json outputs_task_type.json \
  --comment-field comment \
  --code-field code_block
```

### 2. Cluster first-change commit messages

The compact dataset already contains the `first_change_commit_message` field expected by the clustering script:

```bash
python3 scripts/cluster_first_change_commits.py \
  --input-jsonl dataset/comment_codeblock_first_change_dataset.jsonl \
  --output-root clustering_output \
  --overwrite
```

### 3. Code-block extraction script

`extract_codeblocks_from_commits.py` is included as the provenance-preserving extraction script used in the project. It expects the larger project input layout and GitHub-token configuration rather than only the compact dataset in this package.
