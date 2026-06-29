"""Smoke-test the SmolLM2 tokenizer from the original Hugging Face repo.

GGUF 파일은 모델 weight 용도로 사용하고, tokenizer는 원본 Hugging Face
repo의 tokenizer 파일을 사용한다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from transformers import AutoTokenizer


PYCHARM_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PYCHARM_ROOT.parent

DEFAULT_REPO_ID = "HuggingFaceTB/SmolLM2-135M-Instruct"
DEFAULT_TEXT = "Hello, how are you?"
DEFAULT_OUT = PYCHARM_ROOT / "reports" / "tokenizer_smoke_test.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke-test the SmolLM2 tokenizer with transformers.AutoTokenizer."
    )
    parser.add_argument(
        "--repo-id",
        default=DEFAULT_REPO_ID,
        help=f"Hugging Face tokenizer repo ID. Default: {DEFAULT_REPO_ID}",
    )
    parser.add_argument(
        "--text",
        default=DEFAULT_TEXT,
        help=f"Input text to tokenize. Default: {DEFAULT_TEXT!r}",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Report output path. Default: {DEFAULT_OUT}",
    )
    parser.add_argument(
        "--local-files-only",
        action="store_true",
        help="Use only cached tokenizer files and do not contact Hugging Face.",
    )
    return parser.parse_args()


def resolve_path(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (PROJECT_ROOT / expanded).resolve()


def format_special_tokens(tokenizer: Any) -> list[str]:
    lines: list[str] = []

    special_tokens_map = getattr(tokenizer, "special_tokens_map", {}) or {}
    for name, value in sorted(special_tokens_map.items()):
        lines.append(f"- {name}: {value!r}")

    all_special_tokens = list(getattr(tokenizer, "all_special_tokens", []) or [])
    all_special_ids = list(getattr(tokenizer, "all_special_ids", []) or [])
    if all_special_tokens:
        lines.append("- all_special_tokens:")
        for token, token_id in zip(all_special_tokens, all_special_ids):
            lines.append(f"  - {token!r}: {token_id}")

    return lines or ["- none"]


def build_report(repo_id: str, text: str, tokenizer: Any) -> str:
    token_ids = tokenizer.encode(text, add_special_tokens=True)
    decoded_text = tokenizer.decode(token_ids, skip_special_tokens=False)
    chat_template = getattr(tokenizer, "chat_template", None)

    lines = [
        "SmolLM2 tokenizer smoke test",
        f"repo id: {repo_id}",
        f"tokenizer class: {tokenizer.__class__.__name__}",
        f"test text: {text!r}",
        "",
        "Token IDs:",
        repr(token_ids),
        "",
        "Decode result:",
        repr(decoded_text),
        "",
        "Special tokens:",
        *format_special_tokens(tokenizer),
        "",
        f"Chat template exists: {bool(chat_template)}",
    ]

    if chat_template:
        lines.extend(
            [
                f"Chat template length: {len(chat_template)} characters",
                "Chat template preview:",
                chat_template[:500],
            ]
        )

    return "\n".join(lines) + "\n"


def write_report(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    args = parse_args()
    out_path = resolve_path(args.out)

    try:
        tokenizer = AutoTokenizer.from_pretrained(
            args.repo_id,
            local_files_only=args.local_files_only,
        )
        report = build_report(args.repo_id, args.text, tokenizer)
    except Exception as exc:
        report = "\n".join(
            [
                "SmolLM2 tokenizer smoke test",
                f"repo id: {args.repo_id}",
                f"test text: {args.text!r}",
                "",
                "Result: FAIL",
                f"error type: {type(exc).__name__}",
                f"error detail: {exc}",
                "",
                "Install/check:",
                "- python -m pip install -r pycharm/requirements.txt",
                "- Hugging Face network access is required unless tokenizer files are cached.",
            ]
        )
        write_report(out_path, report + "\n")
        print(f"[FAIL] tokenizer smoke test failed: {exc}", file=sys.stderr)
        print(f"report: {out_path}")
        return 1

    write_report(out_path, report)
    print(report, end="")
    print(f"report: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
