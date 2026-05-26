# Dataset Schema

## `comment_codeblock_dataset.jsonl.gz`

Each JSONL row contains:

- `match_id`
  The project-specific unique identifier for the comment instance.

- `trace_id`
  The per-comment trace identifier without the full match suffix.

- `repo_full_name`
  GitHub repository in `owner/repo` format.

- `path`
  Repository-relative file path.

- `language`
  Language label used in the local dataset.

- `file_url`
  GitHub link to the file version associated with the record.

- `comment`
  The AI-referencing code comment text.

- `code_block`
  The extracted code block containing the comment.

## `comment_codeblock_first_change_dataset.jsonl.gz`

This file contains all of the fields above, plus:

- `match_id`
  The project-specific unique identifier for the comment instance.

- `trace_id`
  The per-comment trace identifier without the full match suffix.

- `repo_full_name`
  GitHub repository in `owner/repo` format.

- `path`
  Repository-relative file path.

- `language`
  Language label used in the local dataset.

- `file_url`
  GitHub link to the file version associated with the connected record.

- `comment`
  The AI-referencing code comment text.

- `code_block`
  The extracted code block containing the comment.

- `first_change_commit_message`
  The message of the first later commit that changed the extracted block.

- `first_change_commit_oid`
  The Git commit SHA for the connected first-change commit.

- `first_change_commit_url`
  GitHub link to the connected first-change commit.

- `first_change_date`
  Timestamp of the connected first-change commit.

## `comment_codeblock_without_first_change_dataset.jsonl.gz`

This file uses the same schema as `comment_codeblock_dataset.jsonl.gz` and contains the subset of block-bearing records for which a first-change commit was not successfully collected.
