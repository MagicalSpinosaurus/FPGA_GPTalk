"""Download an already-quantized SmolLM2 GGUF model from Hugging Face.

이 스크립트는 GGUF 파일을 다운로드만 한다.
양자화 변환은 수행하지 않는다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download

try:
    from huggingface_hub.errors import (
        EntryNotFoundError,
        HfHubHTTPError,
        LocalEntryNotFoundError,
        RepositoryNotFoundError,
    )
except ImportError:  # pragma: no cover - older huggingface_hub compatibility
    from huggingface_hub.utils import (  # type: ignore[attr-defined]
        EntryNotFoundError,
        HfHubHTTPError,
        LocalEntryNotFoundError,
        RepositoryNotFoundError,
    )


PYCHARM_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PYCHARM_ROOT.parent

DEFAULT_REPO_ID = "lmstudio-community/SmolLM2-135M-Instruct-GGUF"
DEFAULT_FILENAME = "SmolLM2-135M-Instruct-Q8_0.gguf"
DEFAULT_OUT_DIR = PROJECT_ROOT / "quantized_model" / "original_gguf"

# 다른 GGUF로 바꿀 때는 repo_id와 filename만 교체하면 된다.
MODEL_PRESETS = {
    "smollm2_135m_q8_0": {
        "repo_id": DEFAULT_REPO_ID,
        "filename": DEFAULT_FILENAME,
    },
    # 예시: 360M 또는 Q4_K_M 파일을 사용할 때 아래 값을 명시적으로 넘긴다.
    # "smollm2_360m_q4_k_m": {
    #     "repo_id": "<repo-id>",
    #     "filename": "<filename-Q4_K_M.gguf>",
    # },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download an already-quantized SmolLM2 GGUF model file."
    )
    parser.add_argument(
        "--repo-id",
        default=DEFAULT_REPO_ID,
        help=f"Hugging Face repository ID. Default: {DEFAULT_REPO_ID}",
    )
    parser.add_argument(
        "--filename",
        default=DEFAULT_FILENAME,
        help=f"GGUF filename in the repository. Default: {DEFAULT_FILENAME}",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"Output directory. Default: {DEFAULT_OUT_DIR}",
    )
    return parser.parse_args()


def resolve_output_path(out_dir: Path, filename: str) -> Path:
    return out_dir.expanduser().resolve() / filename


def file_size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)


def print_download_info(repo_id: str, filename: str, local_path: Path) -> None:
    print("Download result")
    print(f"repo id: {repo_id}")
    print(f"filename: {filename}")
    print(f"local path: {local_path}")
    print(f"file size MB: {file_size_mb(local_path):.2f}")


def download_gguf_model(repo_id: str, filename: str, out_dir: Path) -> Path:
    out_dir = out_dir.expanduser().resolve()
    local_path = resolve_output_path(out_dir, filename)

    if local_path.is_file():
        print(f"Already exists, skip download: {local_path}")
        return local_path

    out_dir.mkdir(parents=True, exist_ok=True)

    # hf_hub_download은 이미 양자화된 GGUF 파일을 받는 용도로만 사용한다.
    downloaded_path = hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        local_dir=out_dir,
    )
    return Path(downloaded_path).resolve()


def main() -> int:
    args = parse_args()

    try:
        local_path = download_gguf_model(
            repo_id=args.repo_id,
            filename=args.filename,
            out_dir=args.out_dir,
        )
    except RepositoryNotFoundError:
        print(f"[FAIL] Hugging Face repo를 찾을 수 없습니다: {args.repo_id}", file=sys.stderr)
        print("repo id가 맞는지, private repo라면 로그인/토큰이 필요한지 확인하세요.", file=sys.stderr)
        return 1
    except EntryNotFoundError:
        print(f"[FAIL] repo 안에서 파일을 찾을 수 없습니다: {args.filename}", file=sys.stderr)
        print(f"repo id: {args.repo_id}", file=sys.stderr)
        print("파일명이 Q8_0, Q4_K_M 등 실제 GGUF 파일명과 정확히 일치하는지 확인하세요.", file=sys.stderr)
        return 1
    except LocalEntryNotFoundError:
        print("[FAIL] 네트워크 연결 문제로 Hugging Face에서 파일 정보를 가져오지 못했습니다.", file=sys.stderr)
        print("인터넷 연결, 프록시, Hugging Face 접속 가능 여부를 확인하세요.", file=sys.stderr)
        return 1
    except HfHubHTTPError as exc:
        print("[FAIL] Hugging Face 다운로드 중 HTTP 오류가 발생했습니다.", file=sys.stderr)
        print(f"detail: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print("[FAIL] 로컬 파일 저장 중 오류가 발생했습니다.", file=sys.stderr)
        print(f"detail: {exc}", file=sys.stderr)
        return 1

    print_download_info(args.repo_id, args.filename, local_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
