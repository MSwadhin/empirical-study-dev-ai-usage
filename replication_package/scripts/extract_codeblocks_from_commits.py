#!/usr/bin/env python3
"""
Build the commit-only final SRD dataset with commit-derived code blocks.

Inputs:
- src/newdata/srd_introduction/final_results.jsonl
- src/newdata/srd_recovery/recovered_final_results.jsonl

Outputs:
- src/newdata/srd_commit_only/all_results.jsonl
- src/newdata/srd_commit_only/with_blocks_confident.jsonl
- src/newdata/srd_commit_only/with_blocks_ambiguous.jsonl
- src/newdata/srd_commit_only/without_blocks.jsonl
- src/newdata/srd_commit_only/summary.json

This stage is intentionally commit-only:
- no blame fallback records are included
- the block is derived from the resolved introduction commit patch
"""

from __future__ import annotations

import argparse
import json
import shutil
import ssl
import time
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INTRO_JSONL = SCRIPT_DIR / "srd_introduction" / "final_results.jsonl"
DEFAULT_INTRO_DATES_JSONL = SCRIPT_DIR / "srd_introduction" / "introduction_dates.jsonl"
DEFAULT_RECOVERED_JSONL = SCRIPT_DIR / "srd_recovery" / "recovered_final_results.jsonl"
DEFAULT_RECOVERED_DATES_JSONL = SCRIPT_DIR / "srd_recovery" / "recovered_introduction_dates.jsonl"
DEFAULT_COMMENT_MATCHES_JSONL = SCRIPT_DIR / "srd" / "comment_matches.jsonl"
DEFAULT_OUTPUT_ROOT = SCRIPT_DIR / "srd_commit_only_recovered"
DEFAULT_CONFIG_JSON = SCRIPT_DIR.parent / "config.json"
DEFAULT_API_BASE_URL = "https://api.github.com"
DEFAULT_MAX_LOCAL_SPAN_LINES = 100

BRACE_EXTENSIONS = {
    ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".cc", ".cpp", ".cxx",
    ".cs", ".go", ".rs", ".swift", ".kt", ".kts", ".scala", ".php",
}
INDENT_EXTENSIONS = {".py", ".pyw"}
LINE_COMMENT_TOKENS = {
    ".py": ["#"],
    ".pyw": ["#"],
    ".rb": ["#"],
    ".sh": ["#"],
    ".bash": ["#"],
    ".zsh": ["#"],
    ".yaml": ["#"],
    ".yml": ["#"],
    ".toml": ["#"],
    ".ini": [";", "#"],
    ".js": ["//"],
    ".jsx": ["//"],
    ".ts": ["//"],
    ".tsx": ["//"],
    ".java": ["//"],
    ".c": ["//"],
    ".cc": ["//"],
    ".cpp": ["//"],
    ".cxx": ["//"],
    ".cs": ["//"],
    ".go": ["//"],
    ".rs": ["//"],
    ".swift": ["//"],
    ".kt": ["//"],
    ".kts": ["//"],
    ".scala": ["//"],
    ".sql": ["--"],
    ".lua": ["--"],
    ".hs": ["--"],
    ".elm": ["--"],
    ".php": ["//", "#"],
}
BLOCK_COMMENT_TOKENS = {
    ".js": [("/*", "*/")],
    ".jsx": [("/*", "*/")],
    ".ts": [("/*", "*/")],
    ".tsx": [("/*", "*/")],
    ".java": [("/*", "*/")],
    ".c": [("/*", "*/")],
    ".cc": [("/*", "*/")],
    ".cpp": [("/*", "*/")],
    ".cxx": [("/*", "*/")],
    ".cs": [("/*", "*/")],
    ".go": [("/*", "*/")],
    ".rs": [("/*", "*/")],
    ".swift": [("/*", "*/")],
    ".kt": [("/*", "*/")],
    ".kts": [("/*", "*/")],
    ".scala": [("/*", "*/")],
    ".css": [("/*", "*/")],
    ".scss": [("/*", "*/")],
    ".less": [("/*", "*/")],
    ".html": [("<!--", "-->")],
    ".htm": [("<!--", "-->")],
    ".xml": [("<!--", "-->")],
    ".svg": [("<!--", "-->")],
    ".md": [("<!--", "-->")],
    ".py": [('"""', '"""'), ("'''", "'''")],
    ".pyw": [('"""', '"""'), ("'''", "'''")],
}


@dataclass
class TokenState:
    slot: int
    token: str
    remaining: int = 5000
    reset_epoch: int = 0


class JsonlWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


class GitHubClient:
    def __init__(
        self,
        api_base_url: str,
        tokens: list[str],
        sleep_rest: float = 0.2,
        max_retries: int = 3,
        retry_delay_seconds: float = 2.0,
    ) -> None:
        if not tokens:
            raise ValueError("No github_tokens found in config.json")
        self.api_base_url = api_base_url.rstrip("/")
        self.tokens = [TokenState(slot=index + 1, token=token) for index, token in enumerate(tokens)]
        self.sleep_rest = sleep_rest
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds
        self.ssl_context = self._build_ssl_context()
        self.commit_cache: dict[tuple[str, str], dict[str, Any]] = {}

    def _build_ssl_context(self) -> ssl.SSLContext:
        try:
            import certifi  # type: ignore

            return ssl.create_default_context(cafile=certifi.where())
        except Exception:
            return ssl.create_default_context()

    def _choose_token(self) -> TokenState:
        now = int(time.time())
        healthy = [token for token in self.tokens if token.remaining >= 100 or token.reset_epoch <= now]
        pool = healthy if healthy else self.tokens
        return max(pool, key=lambda token: token.remaining)

    def _sleep_until_reset_if_needed(self) -> None:
        now = int(time.time())
        if all(token.remaining < 5 and token.reset_epoch > now for token in self.tokens):
            sleep_seconds = max(1, min(token.reset_epoch for token in self.tokens) - now + 5)
            time.sleep(sleep_seconds)

    def _update_token_from_headers(self, token: TokenState, headers: Any) -> None:
        remaining = headers.get("X-RateLimit-Remaining")
        reset = headers.get("X-RateLimit-Reset")
        if remaining is not None:
            try:
                token.remaining = int(remaining)
            except ValueError:
                pass
        if reset is not None:
            try:
                token.reset_epoch = int(reset)
            except ValueError:
                pass

    def _request_json(self, url: str) -> tuple[dict[str, Any], dict[str, Any]]:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._sleep_until_reset_if_needed()
            token_state = self._choose_token()
            headers = {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "oss-lllm-comments-commit-only-final/1.0",
                "Authorization": f"Bearer {token_state.token}",
            }
            request = Request(url, headers=headers, method="GET")

            def do_open(context: ssl.SSLContext) -> tuple[Any, Any]:
                with urlopen(request, timeout=120, context=context) as response:
                    raw = response.read().decode("utf-8")
                    payload = json.loads(raw)
                    return payload, response

            try:
                payload, response = do_open(self.ssl_context)
                self._update_token_from_headers(token_state, response.headers)
                time.sleep(self.sleep_rest)
                if not isinstance(payload, dict):
                    raise ValueError(f"Unexpected payload for {url}")
                return payload, {"token_slot": token_state.slot, "attempt": attempt}
            except HTTPError as exc:
                self._update_token_from_headers(token_state, exc.headers)
                last_error = exc
                if exc.code in {403, 500, 502, 503, 504} and attempt < self.max_retries:
                    time.sleep(self.retry_delay_seconds * attempt)
                    continue
                raise
            except URLError as exc:
                reason = getattr(exc, "reason", exc)
                if "CERTIFICATE_VERIFY_FAILED" in str(reason):
                    try:
                        payload, response = do_open(ssl._create_unverified_context())
                        self._update_token_from_headers(token_state, response.headers)
                        time.sleep(self.sleep_rest)
                        if not isinstance(payload, dict):
                            raise ValueError(f"Unexpected payload for {url}")
                        return payload, {"token_slot": token_state.slot, "attempt": attempt}
                    except Exception as inner_exc:  # pragma: no cover
                        last_error = inner_exc
                else:
                    last_error = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay_seconds * attempt)
                    continue
                raise
            except Exception as exc:  # pragma: no cover
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay_seconds * attempt)
                    continue
                raise

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Request failed without explicit error for {url}")

    def fetch_commit(self, owner: str, repo: str, sha: str) -> tuple[dict[str, Any], dict[str, Any]]:
        key = (f"{owner}/{repo}", sha)
        cached = self.commit_cache.get(key)
        if cached is not None:
            return cached, {"token_slot": None, "cached": True, "attempt": 0}
        url = f"{self.api_base_url}/repos/{owner}/{repo}/commits/{sha}"
        payload, meta = self._request_json(url)
        self.commit_cache[key] = payload
        return payload, meta


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the commit-only final dataset with commit-derived code blocks.")
    parser.add_argument("--intro-jsonl", default=str(DEFAULT_INTRO_JSONL), help=f"Introduction final results JSONL. Default: {DEFAULT_INTRO_JSONL}")
    parser.add_argument("--intro-dates-jsonl", default=str(DEFAULT_INTRO_DATES_JSONL), help=f"Introduction dates JSONL. Default: {DEFAULT_INTRO_DATES_JSONL}")
    parser.add_argument("--recovered-jsonl", default=str(DEFAULT_RECOVERED_JSONL), help=f"Recovered final results JSONL. Default: {DEFAULT_RECOVERED_JSONL}")
    parser.add_argument("--recovered-dates-jsonl", default=str(DEFAULT_RECOVERED_DATES_JSONL), help=f"Recovered introduction dates JSONL. Default: {DEFAULT_RECOVERED_DATES_JSONL}")
    parser.add_argument("--comment-matches-jsonl", default=str(DEFAULT_COMMENT_MATCHES_JSONL), help=f"Comment matches JSONL. Default: {DEFAULT_COMMENT_MATCHES_JSONL}")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help=f"Output directory. Default: {DEFAULT_OUTPUT_ROOT}")
    parser.add_argument("--config-json", default=str(DEFAULT_CONFIG_JSON), help=f"Config JSON with github_tokens. Default: {DEFAULT_CONFIG_JSON}")
    parser.add_argument("--overwrite", action="store_true", help="Clear existing outputs and rebuild from scratch.")
    parser.add_argument("--start-index", type=int, default=1, help="1-based record index to start from.")
    parser.add_argument("--end-index", type=int, default=None, help="1-based record index to stop at, inclusive.")
    parser.add_argument("--log-every", type=int, default=50, help="Print progress every N newly processed records. Default: 50")
    parser.add_argument("--flush-every", type=int, default=100, help="Refresh the summary every N newly processed records. Default: 100")
    parser.add_argument("--max-local-span-lines", type=int, default=DEFAULT_MAX_LOCAL_SPAN_LINES, help=f"Maximum line length for local span refinement within a matched hunk. Default: {DEFAULT_MAX_LOCAL_SPAN_LINES}")
    return parser.parse_args()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def unique_match_ids(records: list[dict[str, Any]]) -> set[str]:
    return {record_key(record) for record in records}


def unique_trace_ids(records: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for record in records:
        value = str(record.get("trace_id") or "").strip()
        if value:
            ids.add(value)
    return ids


def unique_repo_paths(records: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for record in records:
        repo = str(record.get("repo_full_name") or "").strip()
        path = str(record.get("path") or "").strip()
        if repo and path:
            ids.add(f"{repo}::{path}")
    return ids


def reset_outputs(output_root: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    for rel in [
        "all_results.jsonl",
        "with_blocks_confident.jsonl",
        "with_blocks_ambiguous.jsonl",
        "without_blocks.jsonl",
        "summary.json",
    ]:
        path = output_root / rel
        if path.exists():
            path.unlink()


def normalize_text(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"^[+>#/\-*;\s<>'\"!]+", "", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()


def tokenize(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.lower()))


def indentation_of(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def is_blank(line: str) -> bool:
    return not line.strip()


def is_single_line_comment(line: str, extension: str) -> tuple[bool, str | None]:
    stripped = line.strip()
    for token in LINE_COMMENT_TOKENS.get(extension, ["#", "//", "--"]):
        if stripped.startswith(token):
            return True, token
    return False, None


def is_code_line(line: str, extension: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    is_comment, _ = is_single_line_comment(line, extension)
    if is_comment:
        return False
    for open_token, _ in BLOCK_COMMENT_TOKENS.get(extension, [("/*", "*/"), ("<!--", "-->")]):
        if stripped.startswith(open_token):
            return False
    return True


def find_next_code_line(lines: list[str], start_index: int, extension: str) -> int | None:
    index = start_index
    while index < len(lines):
        if is_code_line(lines[index], extension):
            return index
        index += 1
    return None


def find_previous_code_line(lines: list[str], start_index: int, extension: str) -> int | None:
    index = start_index
    while index >= 0:
        if is_code_line(lines[index], extension):
            return index
        index -= 1
    return None


def parse_brace_blocks(lines: list[str]) -> list[tuple[int, int]]:
    blocks: list[tuple[int, int]] = []
    stack: list[int] = []
    for line_index, line in enumerate(lines):
        for char in line:
            if char == "{":
                stack.append(line_index)
            elif char == "}" and stack:
                start = stack.pop()
                blocks.append((start, line_index))
    return sorted(blocks, key=lambda item: (item[0], item[1] - item[0]))


def parse_indent_blocks(lines: list[str], extension: str) -> list[tuple[int, int]]:
    blocks: list[tuple[int, int]] = []
    stack: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        if not is_code_line(line, extension):
            continue
        current_indent = indentation_of(line)
        while stack and current_indent <= stack[-1][1]:
            start, _ = stack.pop()
            end = index - 1
            if end > start:
                blocks.append((start, end))
        if line.strip().endswith(":"):
            stack.append((index, current_indent))

    while stack:
        start, _ = stack.pop()
        end = len(lines) - 1
        if end > start:
            blocks.append((start, end))
    return sorted(blocks, key=lambda item: (item[0], item[1] - item[0]))


def find_containing_block(blocks: list[tuple[int, int]], anchor_index: int) -> tuple[int, int] | None:
    containing = [block for block in blocks if block[0] <= anchor_index <= block[1]]
    if not containing:
        return None
    return min(containing, key=lambda item: item[1] - item[0])


def find_next_block(blocks: list[tuple[int, int]], start_index: int) -> tuple[int, int] | None:
    for block in blocks:
        if block[0] >= start_index:
            return block
    return None


def find_previous_block(blocks: list[tuple[int, int]], end_index: int) -> tuple[int, int] | None:
    previous = [block for block in blocks if block[1] <= end_index]
    if not previous:
        return None
    return max(previous, key=lambda item: (item[1], item[1] - item[0]))


def merge_ranges(comment_start: int, comment_end: int, block_start: int, block_end: int) -> tuple[int, int]:
    return min(comment_start, block_start), max(comment_end, block_end)


def chunk_around_code_line(
    lines: list[str],
    code_index: int,
    extension: str,
    max_lines: int,
) -> tuple[int, int] | None:
    if code_index < 0 or code_index >= len(lines):
        return None
    if not is_code_line(lines[code_index], extension):
        return None
    start = code_index
    end = code_index
    while start - 1 >= 0 and not is_blank(lines[start - 1]) and (end - (start - 1) + 1) <= max_lines:
        start -= 1
    while end + 1 < len(lines) and not is_blank(lines[end + 1]) and ((end + 1) - start + 1) <= max_lines:
        end += 1
    return start, end


def find_comment_anchor(lines: list[str], comment_text: str) -> tuple[int, int] | None:
    target_lines = [normalize_text(line) for line in comment_text.splitlines() if normalize_text(line)]
    if not target_lines:
        return None

    normalized_lines = [normalize_text(line) for line in lines]
    joined_target = "\n".join(target_lines)
    if len(target_lines) == 1:
        target = target_lines[0]
        for index, line in enumerate(normalized_lines):
            if target and target in line:
                return index, index

    for start in range(len(normalized_lines)):
        pos = 0
        end = start
        for index in range(start, len(normalized_lines)):
            line = normalized_lines[index]
            if not line:
                continue
            if pos < len(target_lines) and (target_lines[pos] in line or line in target_lines[pos]):
                pos += 1
                end = index
                if pos == len(target_lines):
                    return start, end
    if joined_target:
        for index, line in enumerate(normalized_lines):
            if joined_target in line:
                return index, index
    return None


def extract_structural_region_within_hunk(
    lines: list[str],
    extension: str,
    comment_text: str,
    comment_type: str,
) -> dict[str, Any] | None:
    anchor = find_comment_anchor(lines, comment_text)
    if anchor is None:
        return None
    comment_start, comment_end = anchor
    blocks = parse_indent_blocks(lines, extension) if extension in INDENT_EXTENSIONS else parse_brace_blocks(lines)

    containing = find_containing_block(blocks, comment_start)
    if containing is not None and containing[1] >= comment_end:
        return {
            "start": containing[0],
            "end": containing[1],
            "strategy": "structural_block",
            "notes": ["used containing structural block within matched hunk"],
        }

    previous_code_index = find_previous_code_line(lines, comment_start - 1, extension)
    previous_block = None
    if previous_code_index is not None:
        previous_block = find_containing_block(blocks, previous_code_index)
    if previous_block is None:
        previous_block = find_previous_block(blocks, comment_start - 1)
    if previous_block is not None and previous_block[1] < comment_start:
        start, end = merge_ranges(comment_start, comment_end, previous_block[0], previous_block[1])
        return {
            "start": start,
            "end": end,
            "strategy": "structural_block",
            "notes": ["used previous structural block within matched hunk and extended region to include comment"],
        }

    next_code_index = find_next_code_line(lines, comment_end + 1, extension)
    if next_code_index is not None:
        next_block = find_next_block(blocks, next_code_index)
        if next_block is not None:
            start, end = merge_ranges(comment_start, comment_end, next_block[0], next_block[1])
            return {
                "start": start,
                "end": end,
                "strategy": "structural_block",
                "notes": ["used next structural block within matched hunk and extended region to include comment"],
            }

    if comment_type == "inline_comment":
        return {
            "start": comment_start,
            "end": comment_end,
            "strategy": "comment_only",
            "notes": ["comment remained inline within matched hunk; no structural refinement justified"],
        }
    return None


def extract_local_code_span_within_hunk(
    lines: list[str],
    extension: str,
    comment_text: str,
    max_span_lines: int,
) -> dict[str, Any] | None:
    anchor = find_comment_anchor(lines, comment_text)
    if anchor is None:
        return None
    comment_start, comment_end = anchor

    candidates: list[dict[str, Any]] = []
    if comment_start < len(lines) and is_code_line(lines[comment_start], extension):
        chunk = chunk_around_code_line(lines, comment_start, extension, max_span_lines)
        if chunk is not None:
            start, end = merge_ranges(comment_start, comment_end, chunk[0], chunk[1])
            if (end - start + 1) <= max_span_lines:
                candidates.append({
                    "start": start,
                    "end": end,
                    "strategy": "local_code_span",
                    "notes": ["used local contiguous code span on the same line as the matched comment"],
                    "priority": 0,
                })

    previous_code_index = find_previous_code_line(lines, comment_start - 1, extension)
    if previous_code_index is not None and comment_start - previous_code_index <= 3:
        chunk = chunk_around_code_line(lines, previous_code_index, extension, max_span_lines)
        if chunk is not None:
            gap = comment_start - chunk[1] - 1
            start, end = merge_ranges(comment_start, comment_end, chunk[0], chunk[1])
            if gap <= 1 and (end - start + 1) <= max_span_lines:
                candidates.append({
                    "start": start,
                    "end": end,
                    "strategy": "local_code_span",
                    "notes": ["used nearby preceding contiguous code span within the matched hunk"],
                    "priority": 2,
                })

    next_code_index = find_next_code_line(lines, comment_end + 1, extension)
    if next_code_index is not None and next_code_index - comment_end <= 3:
        chunk = chunk_around_code_line(lines, next_code_index, extension, max_span_lines)
        if chunk is not None:
            gap = chunk[0] - comment_end - 1
            start, end = merge_ranges(comment_start, comment_end, chunk[0], chunk[1])
            if gap <= 1 and (end - start + 1) <= max_span_lines:
                candidates.append({
                    "start": start,
                    "end": end,
                    "strategy": "local_code_span",
                    "notes": ["used nearby following contiguous code span within the matched hunk"],
                    "priority": 1,
                })

    if not candidates:
        return None
    candidates.sort(key=lambda item: ((item["end"] - item["start"]), item["priority"], item["start"]))
    best = candidates[0]
    del best["priority"]
    return best


def record_key(record: dict[str, Any]) -> str:
    match_id = str(record.get("match_id") or "").strip()
    if match_id:
        return match_id
    return (
        f"{record.get('trace_id')}::"
        f"{record.get('start_line')}::"
        f"{record.get('end_line')}::"
        f"{normalize_text(str(record.get('comment_text') or ''))}"
    )


def merge_commit_only_records(intro_records: list[dict[str, Any]], recovered_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for record in intro_records:
        merged[record_key(record)] = {**record, "commit_dataset_source": "srd_introduction"}
    for record in recovered_records:
        merged[record_key(record)] = {**record, "commit_dataset_source": "srd_recovery"}
    return list(merged.values())


def find_file_patch(commit_payload: dict[str, Any], path: str) -> dict[str, Any] | None:
    files = commit_payload.get("files")
    if not isinstance(files, list):
        return None
    for file_info in files:
        if str(file_info.get("filename") or "") == path:
            return file_info
    return None


def split_patch_hunks(patch: str) -> list[dict[str, Any]]:
    hunks: list[dict[str, Any]] = []
    current_header: str | None = None
    current_lines: list[str] = []
    for line in patch.splitlines():
        if line.startswith("@@"):
            if current_header is not None:
                hunks.append({"header": current_header, "lines": current_lines[:]})
            current_header = line
            current_lines = []
        elif current_header is not None:
            current_lines.append(line)
    if current_header is not None:
        hunks.append({"header": current_header, "lines": current_lines[:]})
    return hunks


def derive_hunk_block(hunk_lines: list[str], prefer_added: bool = True) -> tuple[str, str]:
    added_lines = [line[1:] for line in hunk_lines if line.startswith("+") and not line.startswith("+++")]
    if prefer_added and added_lines:
        return "".join(f"{line}\n" for line in added_lines), "added_lines_in_matching_hunk"
    non_deleted = [
        line[1:] if line[:1] in {"+", " "} else line
        for line in hunk_lines
        if not line.startswith("-") and not line.startswith("\\")
    ]
    return "".join(f"{line}\n" for line in non_deleted), "non_deleted_hunk_body"


def render_block_lines(lines: list[str], start: int, end: int) -> str:
    return "".join(f"{line}\n" for line in lines[start:end + 1])


def score_hunk(hunk: dict[str, Any], comment_text: str) -> tuple[int, bool, bool]:
    target_lines = [normalize_text(line) for line in comment_text.splitlines() if normalize_text(line)]
    target_joined = "\n".join(target_lines)
    target_tokens = tokenize(comment_text)

    added_lines = [normalize_text(line[1:]) for line in hunk["lines"] if line.startswith("+") and not line.startswith("+++")]
    context_lines = [normalize_text(line[1:]) for line in hunk["lines"] if line.startswith(" ")]
    added_joined = "\n".join(line for line in added_lines if line)
    context_joined = "\n".join(line for line in context_lines if line)

    exact_added = bool(target_joined and target_joined in added_joined)
    exact_context = bool(target_joined and target_joined in context_joined)
    if exact_added:
        return (100000, True, False)
    if exact_context:
        return (90000, False, True)

    token_pool = set()
    for line in added_lines + context_lines:
        token_pool |= tokenize(line)
    overlap = len(target_tokens & token_pool)
    added_bonus = len(added_lines)
    return (overlap * 100 + added_bonus, False, False)


def classify_hunk_granularity(hunks: list[dict[str, Any]], best_hunk: dict[str, Any]) -> str:
    if len(hunks) == 1 and best_hunk["header"].startswith("@@ -0,0 +1,"):
        return "file_addition_block"
    return "hunk_block"


def is_ambiguous(record: dict[str, Any]) -> bool:
    if str(record.get("commit_diff_block_status") or "") != "ok":
        return False
    strategy = str(record.get("commit_diff_block_strategy") or "")
    return strategy.endswith("_from_context_match") or strategy.endswith("_from_best_hunk")


def has_block(record: dict[str, Any]) -> bool:
    return str(record.get("commit_diff_block_status") or "") == "ok" and bool(record.get("commit_diff_block"))


def process_record(record: dict[str, Any], gh: GitHubClient, max_local_span_lines: int) -> dict[str, Any]:
    result = {
        **record,
        "resolved_date": record.get("introduction_date"),
        "resolved_date_source": "commit_patch_introduction",
        "resolved_commit_oid": record.get("introduction_commit_oid"),
        "resolved_commit_url": record.get("introduction_commit_url"),
        "resolved_commit_details": record.get("introduction_commit_details"),
        "commit_diff_block": "",
        "commit_diff_block_status": "",
        "commit_diff_block_strategy": "",
        "commit_diff_block_granularity": "",
        "commit_diff_notes": [],
        "commit_diff_hunk_header": None,
    }

    repo_full_name = str(record.get("repo_full_name") or "")
    path = str(record.get("path") or "")
    commit_oid = str(record.get("introduction_commit_oid") or "")
    if "/" not in repo_full_name or not path or not commit_oid:
        result["commit_diff_block_status"] = "missing_commit_context"
        result["commit_diff_block_strategy"] = "missing_commit_context"
        result["commit_diff_notes"] = ["missing repo, path, or introduction commit oid"]
        return result

    owner, repo = repo_full_name.split("/", 1)
    try:
        commit_payload, meta = gh.fetch_commit(owner, repo, commit_oid)
        result["commit_fetch_token_slot"] = meta.get("token_slot")
        file_info = find_file_patch(commit_payload, path)
        if file_info is None:
            result["commit_diff_block_status"] = "missing_file_patch"
            result["commit_diff_block_strategy"] = "missing_file_patch"
            result["commit_diff_notes"] = ["introduction commit does not include a patch for the matched file"]
            return result

        patch = file_info.get("patch")
        if not isinstance(patch, str) or not patch:
            result["commit_diff_block_status"] = "missing_patch_text"
            result["commit_diff_block_strategy"] = "missing_patch_text"
            result["commit_diff_notes"] = ["introduction commit file entry has no patch text"]
            return result

        hunks = split_patch_hunks(patch)
        if not hunks:
            result["commit_diff_block_status"] = "no_hunks"
            result["commit_diff_block_strategy"] = "no_hunks"
            result["commit_diff_notes"] = ["patch text did not contain any hunks"]
            return result

        scored = [(score_hunk(hunk, str(record.get("comment_text") or "")), hunk) for hunk in hunks]
        scored.sort(key=lambda item: item[0], reverse=True)
        (score_value, exact_added, exact_context), best_hunk = scored[0]
        if score_value <= 0:
            result["commit_diff_block_status"] = "no_matching_hunk"
            result["commit_diff_block_strategy"] = "no_matching_hunk"
            result["commit_diff_block_granularity"] = "unresolved"
            result["commit_diff_notes"] = ["could not relate any commit hunk to the matched comment"]
            return result

        hunk_body_lines = [
            line[1:] if line[:1] in {"+", " "} else line
            for line in best_hunk["lines"]
            if not line.startswith("-") and not line.startswith("\\")
        ]
        extension = Path(str(record.get("path") or "")).suffix.lower()
        structural_region = extract_structural_region_within_hunk(
            lines=hunk_body_lines,
            extension=extension,
            comment_text=str(record.get("comment_text") or ""),
            comment_type=str(record.get("comment_type") or ""),
        )
        local_span_region = extract_local_code_span_within_hunk(
            lines=hunk_body_lines,
            extension=extension,
            comment_text=str(record.get("comment_text") or ""),
            max_span_lines=max_local_span_lines,
        )

        if structural_region is not None and structural_region["strategy"] == "structural_block":
            start = structural_region["start"]
            end = structural_region["end"]
            block = render_block_lines(hunk_body_lines, start, end)
            final_strategy = "entity_block_within_matching_hunk"
            granularity = "entity_block"
            notes = list(structural_region["notes"])
        elif local_span_region is not None:
            start = local_span_region["start"]
            end = local_span_region["end"]
            block = render_block_lines(hunk_body_lines, start, end)
            final_strategy = "local_code_span_within_matching_hunk"
            granularity = "local_code_span"
            notes = list(local_span_region["notes"])
        elif structural_region is not None and structural_region["strategy"] == "comment_only":
            start = structural_region["start"]
            end = structural_region["end"]
            block = render_block_lines(hunk_body_lines, start, end)
            final_strategy = "comment_only_within_matching_hunk"
            granularity = "comment_only"
            notes = list(structural_region["notes"])
        else:
            block, strategy = derive_hunk_block(best_hunk["lines"], prefer_added=True)
            final_strategy = strategy if exact_added else (f"{strategy}_from_context_match" if exact_context else f"{strategy}_from_best_hunk")
            granularity = classify_hunk_granularity(hunks, best_hunk)
            notes = []

        if granularity == "local_code_span":
            notes.append(f"refined the historically matched hunk to a bounded local code span within {max_local_span_lines} lines")
        elif granularity == "entity_block":
            notes.append("refined the historically matched hunk to an entity-like code block supported within the same hunk")
        elif granularity == "comment_only":
            notes.append("kept comment-only region because no larger structural unit was justified within the matched hunk")
        elif granularity == "file_addition_block":
            notes.append("kept the file-level addition because the historical evidence corresponded to a whole-file addition")
        else:
            notes.append("kept the matched commit hunk because no smaller structural unit was historically justified")

        if exact_added:
            notes.append("matched comment directly in added lines of the introduction commit hunk")
        elif exact_context:
            notes.append("matched comment in context lines and derived the historical unit from the same introduction commit hunk")
        else:
            notes.append("used best token-overlap introduction commit hunk to derive the historical unit")

        result["commit_diff_block"] = block
        result["commit_diff_block_status"] = "ok"
        result["commit_diff_block_strategy"] = final_strategy
        result["commit_diff_block_granularity"] = granularity
        result["commit_diff_notes"] = notes
        result["commit_diff_hunk_header"] = best_hunk["header"]
        return result
    except Exception as exc:
        result["commit_diff_block_status"] = "commit_fetch_error"
        result["commit_diff_block_strategy"] = "commit_fetch_error"
        result["commit_diff_block_granularity"] = "unresolved"
        result["commit_diff_notes"] = [str(exc)]
        return result


def load_completed_ids(output_root: Path) -> set[str]:
    completed: set[str] = set()
    all_results = output_root / "all_results.jsonl"
    if not all_results.exists():
        return completed
    with all_results.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            completed.add(record_key(obj))
    return completed


def summarize(
    output_root: Path,
    *,
    comment_matches_records: list[dict[str, Any]] | None = None,
    intro_date_records: list[dict[str, Any]] | None = None,
    recovered_date_records: list[dict[str, Any]] | None = None,
    intro_final_records: list[dict[str, Any]] | None = None,
    recovered_final_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    counts = {
        "all_results.jsonl": 0,
        "with_blocks_confident.jsonl": 0,
        "with_blocks_ambiguous.jsonl": 0,
        "without_blocks.jsonl": 0,
        "status_counts": {},
        "strategy_counts": {},
        "granularity_counts": {},
        "source_counts": {},
        "total_files": 0,
        "total_repo_path_files": 0,
        "total_comment_matches": 0,
        "total_introduction_dates_found": 0,
        "total_introduction_dates_in_window": 0,
        "total_blocks_extracted": 0,
        "block_type_counts": {},
    }

    status_counts: dict[str, int] = {}
    strategy_counts: dict[str, int] = {}
    granularity_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    all_results = output_root / "all_results.jsonl"
    if all_results.exists():
        with all_results.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                counts["all_results.jsonl"] += 1
                obj = json.loads(line)
                status = str(obj.get("commit_diff_block_status") or "unknown")
                strategy = str(obj.get("commit_diff_block_strategy") or "unknown")
                granularity = str(obj.get("commit_diff_block_granularity") or "unknown")
                source = str(obj.get("commit_dataset_source") or "unknown")
                status_counts[status] = status_counts.get(status, 0) + 1
                strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1
                granularity_counts[granularity] = granularity_counts.get(granularity, 0) + 1
                source_counts[source] = source_counts.get(source, 0) + 1

    for rel in ["with_blocks_confident.jsonl", "with_blocks_ambiguous.jsonl", "without_blocks.jsonl"]:
        path = output_root / rel
        if path.exists():
            counts[rel] = sum(1 for _ in path.open("r", encoding="utf-8"))

    counts["status_counts"] = status_counts
    counts["strategy_counts"] = strategy_counts
    counts["granularity_counts"] = granularity_counts
    counts["source_counts"] = source_counts
    counts["total_blocks_extracted"] = counts["with_blocks_confident.jsonl"] + counts["with_blocks_ambiguous.jsonl"]
    counts["block_type_counts"] = {
        key: value for key, value in granularity_counts.items() if key != "unresolved"
    }

    if comment_matches_records is not None:
        counts["total_comment_matches"] = len(unique_match_ids(comment_matches_records))
        counts["total_files"] = len(unique_trace_ids(comment_matches_records))
        counts["total_repo_path_files"] = len(unique_repo_paths(comment_matches_records))

    if intro_date_records is not None or recovered_date_records is not None:
        all_found: set[str] = set()
        if intro_date_records is not None:
            all_found |= unique_match_ids(intro_date_records)
        if recovered_date_records is not None:
            all_found |= unique_match_ids(recovered_date_records)
        counts["total_introduction_dates_found"] = len(all_found)

    if intro_final_records is not None or recovered_final_records is not None:
        in_window: set[str] = set()
        if intro_final_records is not None:
            in_window |= unique_match_ids(intro_final_records)
        if recovered_final_records is not None:
            in_window |= unique_match_ids(recovered_final_records)
        counts["total_introduction_dates_in_window"] = len(in_window)

    (output_root / "summary.json").write_text(json.dumps(counts, indent=2), encoding="utf-8")
    return counts


def main() -> int:
    args = parse_args()
    intro_jsonl = Path(args.intro_jsonl).expanduser().resolve()
    intro_dates_jsonl = Path(args.intro_dates_jsonl).expanduser().resolve()
    recovered_jsonl = Path(args.recovered_jsonl).expanduser().resolve()
    recovered_dates_jsonl = Path(args.recovered_dates_jsonl).expanduser().resolve()
    comment_matches_jsonl = Path(args.comment_matches_jsonl).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    config_json = Path(args.config_json).expanduser().resolve()

    if args.overwrite:
        reset_outputs(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    config = load_json(config_json)
    api_base_url = str(config.get("api_base_url") or DEFAULT_API_BASE_URL)
    tokens = list(config.get("github_tokens") or [])
    gh = GitHubClient(api_base_url=api_base_url, tokens=tokens)

    intro_records = load_jsonl(intro_jsonl)
    intro_date_records = load_jsonl(intro_dates_jsonl)
    recovered_records = load_jsonl(recovered_jsonl)
    recovered_date_records = load_jsonl(recovered_dates_jsonl)
    comment_matches_records = load_jsonl(comment_matches_jsonl)
    merged_records = merge_commit_only_records(intro_records, recovered_records)
    merged_records.sort(key=record_key)

    start_index = max(args.start_index, 1)
    end_index = args.end_index if args.end_index is not None else len(merged_records)
    if end_index < start_index:
        raise ValueError("--end-index must be greater than or equal to --start-index.")
    selected = merged_records[start_index - 1:end_index]

    completed_ids = set() if args.overwrite else load_completed_ids(output_root)
    remaining = [record for record in selected if record_key(record) not in completed_ids]

    writers = {
        "all": JsonlWriter(output_root / "all_results.jsonl"),
        "confident": JsonlWriter(output_root / "with_blocks_confident.jsonl"),
        "ambiguous": JsonlWriter(output_root / "with_blocks_ambiguous.jsonl"),
        "without": JsonlWriter(output_root / "without_blocks.jsonl"),
    }

    print(
        f"[start] merged_records={len(merged_records)} selected={len(selected)} "
        f"existing={len(completed_ids)} remaining={len(remaining)} output_root={output_root}",
        flush=True,
    )
    print(
        f"[phase] name=commit_only_merge intro_records={len(intro_records)} recovered_records={len(recovered_records)}",
        flush=True,
    )

    processed_now = 0
    started_at = time.time()
    confident_count = 0
    ambiguous_count = 0
    without_count = 0
    last_summary_update = 0

    for record in remaining:
        result = process_record(record, gh, args.max_local_span_lines)
        writers["all"].write(result)
        if has_block(result):
            if is_ambiguous(result):
                writers["ambiguous"].write(result)
                ambiguous_count += 1
            else:
                writers["confident"].write(result)
                confident_count += 1
        else:
            writers["without"].write(result)
            without_count += 1

        processed_now += 1
        completed_ids.add(record_key(result))

        if processed_now == 1:
            print(
                f"[event] outcome={result.get('commit_diff_block_status')} "
                f"repo={result.get('repo_full_name')} path={result.get('path')} "
                f"strategy={result.get('commit_diff_block_strategy')}",
                flush=True,
            )

        if args.log_every > 0 and processed_now % args.log_every == 0:
            elapsed = max(time.time() - started_at, 0.001)
            rate = processed_now / elapsed
            print(
                f"[progress] processed={processed_now}/{len(remaining)} rate={rate:.2f}/s "
                f"confident={confident_count} ambiguous={ambiguous_count} without_block={without_count} "
                f"repo={result.get('repo_full_name')} path={result.get('path')} "
                f"status={result.get('commit_diff_block_status')}",
                flush=True,
            )

        if processed_now == 1 or (args.flush_every > 0 and processed_now % args.flush_every == 0):
            counts = summarize(
                output_root,
                comment_matches_records=comment_matches_records,
                intro_date_records=intro_date_records,
                recovered_date_records=recovered_date_records,
                intro_final_records=intro_records,
                recovered_final_records=recovered_records,
            )
            last_summary_update = processed_now
            print(
                f"[flush] processed={processed_now}/{len(remaining)} "
                f"all={counts['all_results.jsonl']} confident={counts['with_blocks_confident.jsonl']} "
                f"ambiguous={counts['with_blocks_ambiguous.jsonl']} without={counts['without_blocks.jsonl']}",
                flush=True,
            )

    if processed_now != last_summary_update:
        counts = summarize(
            output_root,
            comment_matches_records=comment_matches_records,
            intro_date_records=intro_date_records,
            recovered_date_records=recovered_date_records,
            intro_final_records=intro_records,
            recovered_final_records=recovered_records,
        )
    else:
        counts = load_json(output_root / "summary.json")

    elapsed = time.time() - started_at
    print(
        f"[done] processed={processed_now}/{len(remaining)} elapsed_seconds={elapsed:.1f} "
        f"all={counts['all_results.jsonl']} confident={counts['with_blocks_confident.jsonl']} "
        f"ambiguous={counts['with_blocks_ambiguous.jsonl']} without={counts['without_blocks.jsonl']} "
        f"output_root={output_root}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
