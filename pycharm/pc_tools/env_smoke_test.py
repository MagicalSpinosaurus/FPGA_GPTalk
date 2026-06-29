"""SmolLM2 Zybo Z7-20 Python environment smoke test.

이 파일은 개발 환경이 준비되었는지만 확인한다.
모델 파일 다운로드, Hugging Face 로그인, 네트워크 접근은 수행하지 않는다.
"""

from __future__ import annotations

import importlib
from pathlib import Path


PYCHARM_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PYCHARM_ROOT.parent

REQUIRED_DIRECTORIES = (
    "pycharm/pc_tools",
    "reference/python",
    "reference/c",
    "hw",
    "linux",
    "tests/golden",
    "models",
)

REQUIRED_PACKAGES = (
    ("torch", "torch"),
    ("transformers", "transformers"),
    ("safetensors", "safetensors"),
    ("huggingface_hub", "huggingface_hub"),
    ("numpy", "numpy"),
    ("tqdm", "tqdm"),
)


def check_directories() -> list[str]:
    missing: list[str] = []
    for relative_path in REQUIRED_DIRECTORIES:
        path = PROJECT_ROOT / relative_path
        if not path.is_dir():
            missing.append(relative_path)
    return missing


def check_packages() -> list[str]:
    missing: list[str] = []
    for import_name, package_name in REQUIRED_PACKAGES:
        try:
            module = importlib.import_module(import_name)
        except ImportError:
            missing.append(package_name)
            continue

        version = getattr(module, "__version__", "unknown")
        print(f"[OK] {package_name}: {version}")
    return missing


def main() -> int:
    print("SmolLM2 Zybo Z7-20 environment smoke test")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"PyCharm root: {PYCHARM_ROOT}")
    print()

    missing_directories = check_directories()
    if missing_directories:
        print("[FAIL] Missing directories:")
        for relative_path in missing_directories:
            print(f"  - {relative_path}")
        return 1
    print("[OK] Project directory structure")
    print()

    missing_packages = check_packages()
    if missing_packages:
        print()
        print("[FAIL] Missing Python packages:")
        for package_name in missing_packages:
            print(f"  - {package_name}")
        print()
        print("Install them with:")
        print("  cd pycharm")
        print("  python -m pip install -r requirements.txt")
        return 1

    print()
    print("[OK] Python environment is ready.")
    print("No model files were downloaded or loaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
