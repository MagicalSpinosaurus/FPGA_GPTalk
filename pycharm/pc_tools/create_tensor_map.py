"""Create a draft tensor map from a GGUF tensor inspection CSV."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PYCHARM_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PYCHARM_ROOT.parent

DEFAULT_INSPECT_CSV = Path("reports") / "gguf_inspect" / "tensors.csv"
DEFAULT_OUT = Path("fpga_layout") / "tensor_map.json"

LAYER_RE = re.compile(r"^blk\.(?P<layer>\d+)\.(?P<suffix>.+)$")

DIRECT_NAME_RULES = {
    "token_embd.weight": ("tok_embeddings", "token_embedding", True),
    "tok_embeddings.weight": ("tok_embeddings", "token_embedding", True),
    "model.embed_tokens.weight": ("tok_embeddings", "token_embedding", True),
    "output.weight": ("lm_head", "lm_head", True),
    "lm_head.weight": ("lm_head", "lm_head", True),
    "model.lm_head.weight": ("lm_head", "lm_head", True),
    "output_norm.weight": ("final_norm", "final_norm", True),
    "norm.weight": ("final_norm", "final_norm", True),
    "model.norm.weight": ("final_norm", "final_norm", True),
}

LAYER_SUFFIX_RULES = {
    "attn_q.weight": ("layer_{layer}_q_proj", "attention_q_projection", True),
    "attn_k.weight": ("layer_{layer}_k_proj", "attention_k_projection", True),
    "attn_v.weight": ("layer_{layer}_v_proj", "attention_v_projection", True),
    "attn_output.weight": ("layer_{layer}_o_proj", "attention_output_projection", True),
    "ffn_gate.weight": ("layer_{layer}_gate_proj", "mlp_gate_projection", True),
    "ffn_up.weight": ("layer_{layer}_up_proj", "mlp_up_projection", True),
    "ffn_down.weight": ("layer_{layer}_down_proj", "mlp_down_projection", True),
    "attn_norm.weight": ("layer_{layer}_input_norm", "attention_input_norm", True),
    "ffn_norm.weight": ("layer_{layer}_post_attn_norm", "post_attention_norm", True),
}

# Extra common non-GGUF/HF-style candidates. They are useful when the inspected
# names come from a conversion step that preserved Hugging Face module names.
HF_LAYER_SUFFIX_RULES = {
    "self_attn.q_proj.weight": ("layer_{layer}_q_proj", "attention_q_projection", True),
    "self_attn.k_proj.weight": ("layer_{layer}_k_proj", "attention_k_projection", True),
    "self_attn.v_proj.weight": ("layer_{layer}_v_proj", "attention_v_projection", True),
    "self_attn.o_proj.weight": ("layer_{layer}_o_proj", "attention_output_projection", True),
    "mlp.gate_proj.weight": ("layer_{layer}_gate_proj", "mlp_gate_projection", True),
    "mlp.up_proj.weight": ("layer_{layer}_up_proj", "mlp_up_projection", True),
    "mlp.down_proj.weight": ("layer_{layer}_down_proj", "mlp_down_projection", True),
    "input_layernorm.weight": ("layer_{layer}_input_norm", "attention_input_norm", True),
    "post_attention_layernorm.weight": (
        "layer_{layer}_post_attn_norm",
        "post_attention_norm",
        True,
    ),
}

EDITING_INSTRUCTIONS = [
    "이 파일은 초안이다. runtime의 실제 loader가 기대하는 이름과 다르면 internal_name을 수정한다.",
    "internal_name이 unknown인 항목은 자동 규칙으로 역할을 확정하지 못한 tensor다.",
    "unknown 항목을 수정할 때는 original_name, shape, dtype_or_quant_type을 보고 대응되는 runtime buffer 이름을 정한다.",
    "runtime에서 사용하지 않는 tensor는 used를 false로 둔다. 로드해야 하는 weight/bias/norm이면 used를 true로 바꾼다.",
    "shape는 GGUF CSV에서 읽은 원본 shape다. runtime이 transpose를 요구하면 이 JSON의 shape를 바꾸지 말고 loader 쪽 변환 규칙을 별도로 기록한다.",
    "새 GGUF naming variant를 발견하면 create_tensor_map.py의 DIRECT_NAME_RULES 또는 LAYER_SUFFIX_RULES에 규칙을 추가한 뒤 다시 생성한다.",
    "매핑을 수동 수정한 뒤에는 mapping_report.txt의 unknown 목록이 남아 있는지 확인하고, loader smoke test에서 모든 used=true tensor가 소비되는지 확인한다.",
]


@dataclass
class TensorRow:
    tensor_name: str
    shape: list[int] | str
    dtype_or_quant_type: str
    offset: int | None
    nbytes: int | None


@dataclass
class MappingResult:
    internal_name: str
    role: str
    used: bool
    confidence: str
    note: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a draft runtime tensor map from GGUF tensors.csv."
    )
    parser.add_argument(
        "--inspect-csv",
        type=Path,
        default=DEFAULT_INSPECT_CSV,
        help=f"Input tensors.csv path. Default: {DEFAULT_INSPECT_CSV}",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output tensor_map.json path. Default: {DEFAULT_OUT}",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Output mapping_report.txt path. Default: next to --out.",
    )
    return parser.parse_args()


def resolve_existing_input(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded.resolve()

    candidates = [
        Path.cwd() / expanded,
        PYCHARM_ROOT / expanded,
        PROJECT_ROOT / expanded,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    # Prefer the pycharm-local interpretation in error messages because the
    # pc_tools reports are written under pycharm/reports by default.
    return (PYCHARM_ROOT / expanded).resolve()


def resolve_output(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (PYCHARM_ROOT / expanded).resolve()


def project_relative(path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def parse_optional_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def parse_shape(value: str) -> list[int] | str:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    if isinstance(parsed, list) and all(isinstance(item, int) for item in parsed):
        return parsed
    return value


def read_tensor_rows(path: Path) -> list[TensorRow]:
    with path.open("r", encoding="utf-8", newline="") as file_obj:
        reader = csv.DictReader(file_obj)
        required = {"tensor_name", "shape", "dtype_or_quant_type", "offset", "nbytes"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"missing required CSV columns: {', '.join(sorted(missing))}")

        rows = []
        for row in reader:
            rows.append(
                TensorRow(
                    tensor_name=row["tensor_name"],
                    shape=parse_shape(row["shape"]),
                    dtype_or_quant_type=row["dtype_or_quant_type"],
                    offset=parse_optional_int(row.get("offset")),
                    nbytes=parse_optional_int(row.get("nbytes")),
                )
            )
    return rows


def map_tensor_name(tensor_name: str) -> MappingResult:
    direct_rule = DIRECT_NAME_RULES.get(tensor_name)
    if direct_rule is not None:
        internal_name, role, used = direct_rule
        return MappingResult(
            internal_name=internal_name,
            role=role,
            used=used,
            confidence="auto",
            note="matched direct GGUF/HF tensor name rule",
        )

    layer_match = LAYER_RE.match(tensor_name)
    if layer_match:
        layer = int(layer_match.group("layer"))
        suffix = layer_match.group("suffix")
        layer_rule = LAYER_SUFFIX_RULES.get(suffix)
        if layer_rule is not None:
            internal_template, role, used = layer_rule
            return MappingResult(
                internal_name=internal_template.format(layer=layer),
                role=role,
                used=used,
                confidence="auto",
                note="matched blk.N GGUF tensor name rule",
            )

    hf_layer_match = re.match(r"^model\.layers\.(?P<layer>\d+)\.(?P<suffix>.+)$", tensor_name)
    if hf_layer_match:
        layer = int(hf_layer_match.group("layer"))
        suffix = hf_layer_match.group("suffix")
        hf_rule = HF_LAYER_SUFFIX_RULES.get(suffix)
        if hf_rule is not None:
            internal_template, role, used = hf_rule
            return MappingResult(
                internal_name=internal_template.format(layer=layer),
                role=role,
                used=used,
                confidence="auto",
                note="matched Hugging Face layer tensor name rule",
            )

    return MappingResult(
        internal_name="unknown",
        role="unknown",
        used=False,
        confidence="unknown",
        note="manual mapping required; see instructions and mapping_report.txt",
    )


def build_tensor_map(
    rows: list[TensorRow],
    inspect_csv: Path,
) -> dict[str, Any]:
    mappings = []
    unknown_count = 0
    used_count = 0

    for row in rows:
        result = map_tensor_name(row.tensor_name)
        if result.internal_name == "unknown":
            unknown_count += 1
        if result.used:
            used_count += 1

        mappings.append(
            {
                "original_name": row.tensor_name,
                "internal_name": result.internal_name,
                "shape": row.shape,
                "role": result.role,
                "used": result.used,
                "dtype_or_quant_type": row.dtype_or_quant_type,
                "offset": row.offset,
                "nbytes": row.nbytes,
                "confidence": result.confidence,
                "note": result.note,
            }
        )

    return {
        "schema_version": 1,
        "source": {
            "inspect_csv": project_relative(inspect_csv),
            "generator": "pycharm/pc_tools/create_tensor_map.py",
        },
        "summary": {
            "total_tensors": len(rows),
            "mapped_tensors": len(rows) - unknown_count,
            "unknown_tensors": unknown_count,
            "used_tensors": used_count,
        },
        "editing_instructions": EDITING_INSTRUCTIONS,
        "mappings": mappings,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, indent=2)
        file_obj.write("\n")


def build_report(payload: dict[str, Any], out_path: Path) -> str:
    mappings = payload["mappings"]
    summary = payload["summary"]
    unknown = [item for item in mappings if item["internal_name"] == "unknown"]
    unused = [item for item in mappings if not item["used"]]
    roles: dict[str, int] = {}
    for item in mappings:
        roles[item["role"]] = roles.get(item["role"], 0) + 1

    lines = [
        "Tensor map generation report",
        f"source csv: {payload['source']['inspect_csv']}",
        f"tensor map: {out_path}",
        "",
        "Summary:",
        f"- total tensors: {summary['total_tensors']}",
        f"- mapped tensors: {summary['mapped_tensors']}",
        f"- unknown tensors: {summary['unknown_tensors']}",
        f"- used tensors: {summary['used_tensors']}",
        "",
        "Role counts:",
    ]
    for role, count in sorted(roles.items()):
        lines.append(f"- {role}: {count}")

    lines.extend(["", "Manual editing instructions:"])
    for index, instruction in enumerate(EDITING_INSTRUCTIONS, start=1):
        lines.append(f"{index}. {instruction}")

    lines.extend(["", "Unknown tensors:"])
    if unknown:
        for item in unknown:
            lines.append(
                "- "
                f"{item['original_name']} shape={item['shape']} "
                f"dtype={item['dtype_or_quant_type']} -> internal_name=unknown"
            )
    else:
        lines.append("- none")

    lines.extend(["", "Unused tensors:"])
    if unused:
        for item in unused:
            lines.append(
                "- "
                f"{item['original_name']} role={item['role']} "
                f"internal_name={item['internal_name']}"
            )
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "Common automatic GGUF rules included:",
            "- token_embd.weight -> tok_embeddings",
            "- output.weight -> lm_head",
            "- output_norm.weight -> final_norm",
            "- blk.N.attn_q.weight -> layer_N_q_proj",
            "- blk.N.attn_k.weight -> layer_N_k_proj",
            "- blk.N.attn_v.weight -> layer_N_v_proj",
            "- blk.N.attn_output.weight -> layer_N_o_proj",
            "- blk.N.ffn_gate.weight -> layer_N_gate_proj",
            "- blk.N.ffn_up.weight -> layer_N_up_proj",
            "- blk.N.ffn_down.weight -> layer_N_down_proj",
            "- blk.N.attn_norm.weight -> layer_N_input_norm",
            "- blk.N.ffn_norm.weight -> layer_N_post_attn_norm",
        ]
    )

    return "\n".join(lines) + "\n"


def write_report(path: Path, report: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")


def main() -> int:
    args = parse_args()
    inspect_csv = resolve_existing_input(args.inspect_csv)
    out_path = resolve_output(args.out)
    report_path = resolve_output(args.report) if args.report else out_path.parent / "mapping_report.txt"

    try:
        rows = read_tensor_rows(inspect_csv)
        payload = build_tensor_map(rows, inspect_csv)
        write_json(out_path, payload)
        report = build_report(payload, out_path)
        write_report(report_path, report)
    except Exception as exc:
        print(f"[FAIL] tensor map generation failed: {exc}", file=sys.stderr)
        return 1

    summary = payload["summary"]
    print(f"tensor map: {out_path}")
    print(f"mapping report: {report_path}")
    print(f"total tensors: {summary['total_tensors']}")
    print(f"mapped tensors: {summary['mapped_tensors']}")
    print(f"unknown tensors: {summary['unknown_tensors']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
