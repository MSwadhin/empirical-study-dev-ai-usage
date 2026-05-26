#!/usr/bin/env python3
"""
Run a prompt template against the SRD commit-only dataset using either:
- a local Ollama daemon
- or an Ollama-compatible cloud endpoint

The script:
- reads a prompt template from --prompt-path
- reads records from the SRD commit-only JSONL dataset
- fills {comment} from comment_text
- fills {code_block} from commit_diff_block
- sends each prompt to /api/generate
- writes results to a JSON file as stable_record_key -> category
- flushes results every N processed items

Default key order:
- match_id
- trace_id
- 1-based record position fallback
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PROMPT_PATH = SCRIPT_DIR / "prompt_1a.txt"
DEFAULT_INPUT_JSONL = Path(__file__).resolve().parents[1] / "newdata" / "srd_commit_only" / "with_blocks_confident.jsonl"
DEFAULT_OUTPUT_JSON = SCRIPT_DIR / "srd_commit_only_prompt_results.json"
DEFAULT_FLUSH_EVERY = 100
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11434
DEFAULT_COMMENT_FIELD = "comment_text"
DEFAULT_CODE_FIELD = "commit_diff_block"
DEFAULT_KEY_FIELD = "match_id"
DEFAULT_UNPROCESSED_LABEL = "UNPROCESSED"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a prompt template against the SRD commit-only JSONL dataset using local Ollama or an Ollama-compatible cloud endpoint."
    )
    parser.add_argument("--model", required=True, help="Model name to send to Ollama.")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OLLAMA_BASE_URL", ""),
        help=(
            "Optional cloud base URL. If omitted, the script uses local Ollama via "
            "--host/--port. Can also be set with OLLAMA_BASE_URL."
        ),
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Local Ollama host. Default: {DEFAULT_HOST}")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Local Ollama port. Default: {DEFAULT_PORT}")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("OLLAMA_API_KEY", ""),
        help="API key for cloud mode. Can also be set with OLLAMA_API_KEY.",
    )
    parser.add_argument(
        "--prompt-path",
        default=str(DEFAULT_PROMPT_PATH),
        help=f"Prompt template path. Default: {DEFAULT_PROMPT_PATH}",
    )
    parser.add_argument(
        "--input-jsonl",
        default=str(DEFAULT_INPUT_JSONL),
        help=f"Input SRD commit-only JSONL file. Default: {DEFAULT_INPUT_JSONL}",
    )
    parser.add_argument(
        "--output-json",
        default=str(DEFAULT_OUTPUT_JSON),
        help=f"Output JSON file. Default: {DEFAULT_OUTPUT_JSON}",
    )
    parser.add_argument("--comment-field", default=DEFAULT_COMMENT_FIELD, help=f"Record field to use as comment. Default: {DEFAULT_COMMENT_FIELD}")
    parser.add_argument("--code-field", default=DEFAULT_CODE_FIELD, help=f"Record field to use as code block. Default: {DEFAULT_CODE_FIELD}")
    parser.add_argument(
        "--key-field",
        default=DEFAULT_KEY_FIELD,
        help=f"Stable record key field to use in the output JSON. Default: {DEFAULT_KEY_FIELD}",
    )
    parser.add_argument("--start-index", type=int, default=1, help="1-based record position to start from. Default: 1")
    parser.add_argument("--end-index", type=int, default=None, help="1-based record position to stop at, inclusive.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite the output JSON file instead of resuming.")
    parser.add_argument("--temperature", type=float, default=0.0, help="Generation temperature. Default: 0.0")
    parser.add_argument("--timeout-seconds", type=int, default=120, help="HTTP timeout for each request. Default: 120")
    parser.add_argument("--retries", type=int, default=2, help="Number of retries for timeout or transient failures. Default: 2")
    parser.add_argument("--retry-delay-seconds", type=float, default=5.0, help="Delay between retries in seconds. Default: 5.0")
    parser.add_argument("--flush-every", type=int, default=DEFAULT_FLUSH_EVERY, help=f"Write buffered results every N items. Default: {DEFAULT_FLUSH_EVERY}")
    parser.add_argument(
        "--unprocessed-label",
        default=DEFAULT_UNPROCESSED_LABEL,
        help=f"Label to write when an item cannot be processed. Default: {DEFAULT_UNPROCESSED_LABEL}",
    )
    return parser.parse_args()


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                records.append(parsed)
    return records


def render_prompt(template: str, item: dict[str, Any], comment_field: str, code_field: str) -> str:
    comment = str(item.get(comment_field) or "")
    code_block = str(item.get(code_field) or "")
    rendered = template.replace("{comment}", comment)
    rendered = rendered.replace("{code_block}", code_block)
    return rendered


def build_generate_url(base_url: str, host: str, port: int) -> str:
    cleaned = base_url.strip().rstrip("/")
    if cleaned:
        return f"{cleaned}/api/generate"
    return f"http://{host}:{port}/api/generate"


def call_ollama(
    base_url: str,
    host: str,
    port: int,
    api_key: str,
    model: str,
    prompt: str,
    temperature: float,
    timeout_seconds: int,
    retries: int,
    retry_delay_seconds: float,
) -> str:
    url = build_generate_url(base_url, host, port)
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
        },
    }
    headers = {"Content-Type": "application/json"}
    if base_url.strip() and api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"

    body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers=headers, method="POST")
    attempts = retries + 1
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                response_json = json.loads(response.read().decode("utf-8"))
            return str(response_json.get("response") or "").strip()
        except (TimeoutError, socket.timeout) as exc:
            last_error = exc
        except HTTPError as exc:
            last_error = exc
            if exc.code < 500 and exc.code not in {403, 404, 429}:
                raise
        except URLError as exc:
            last_error = exc

        if attempt < attempts:
            time.sleep(retry_delay_seconds)

    raise TimeoutError(
        f"Ollama request timed out or failed after {attempts} attempts. "
        f"Last error: {last_error}"
    )


def parse_category(text: str) -> str:
    stripped = text.strip()

    json_like_match = re.search(r"output\s*[:=]\s*\"?([^\n\"}]+)\"?", stripped, re.IGNORECASE)
    if json_like_match:
        return json_like_match.group(1).strip()

    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict) and "output" in parsed:
            return str(parsed["output"]).strip()
    except json.JSONDecodeError:
        pass

    line_match = re.search(r"([A-Za-z &]+|False Positive)\s*$", stripped)
    if line_match:
        return line_match.group(1).strip()

    return stripped


def load_existing_output(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = load_json(path)
    if not isinstance(data, dict):
        raise ValueError("Existing output JSON must contain a top-level object.")
    return {str(key): str(value) for key, value in data.items()}


def write_output(path: Path, results: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def record_key(item: dict[str, Any], key_field: str, position: int) -> str:
    preferred = str(item.get(key_field) or "").strip()
    if preferred:
        return preferred
    trace_id = str(item.get("trace_id") or "").strip()
    if trace_id:
        return trace_id
    return str(position)


def main() -> int:
    args = parse_args()

    prompt_path = Path(args.prompt_path).expanduser().resolve()
    input_jsonl = Path(args.input_jsonl).expanduser().resolve()
    output_json = Path(args.output_json).expanduser().resolve()

    if args.start_index < 1:
        raise ValueError("--start-index must be at least 1.")

    prompt_template = load_text(prompt_path)
    data = load_jsonl(input_jsonl)

    end_index = args.end_index if args.end_index is not None else len(data)
    if end_index < args.start_index:
        raise ValueError("--end-index must be greater than or equal to --start-index.")

    selected = data[args.start_index - 1:end_index]
    results: dict[str, str] = {} if args.overwrite else load_existing_output(output_json)
    buffered = 0

    for offset, item in enumerate(selected, start=args.start_index):
        prompt = render_prompt(prompt_template, item, args.comment_field, args.code_field)
        output_key = record_key(item, args.key_field, offset)
        if not args.overwrite and output_key in results:
            continue

        try:
            raw_response = call_ollama(
                base_url=args.base_url,
                host=args.host,
                port=args.port,
                api_key=args.api_key,
                model=args.model,
                prompt=prompt,
                temperature=args.temperature,
                timeout_seconds=args.timeout_seconds,
                retries=args.retries,
                retry_delay_seconds=args.retry_delay_seconds,
            )
            parsed_category = parse_category(raw_response)
        except (TimeoutError, socket.timeout, URLError) as exc:
            parsed_category = args.unprocessed_label
            raw_response = f"{args.unprocessed_label}: {exc}"
        except HTTPError as exc:
            if exc.code in {403, 404}:
                parsed_category = args.unprocessed_label
                raw_response = f"{args.unprocessed_label}: HTTP {exc.code} {exc.reason}"
            else:
                raise

        results[output_key] = parsed_category
        buffered += 1

        if buffered >= args.flush_every:
            write_output(output_json, results)
            buffered = 0

        print(
            f"Processed record {offset}: "
            f"{item.get('repo_full_name')} {item.get('path')} "
            f"-> {parsed_category}"
        )

    if buffered > 0 or args.overwrite or not output_json.exists():
        write_output(output_json, results)

    print(f"Finished. Results written to {output_json}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, HTTPError, URLError, TimeoutError, socket.timeout) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
