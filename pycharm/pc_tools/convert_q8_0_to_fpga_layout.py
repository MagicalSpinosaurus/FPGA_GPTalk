"""Convert GGUF Q8_0 tensors into a 16-lane FPGA GEMV stream layout.

Weight stream layout:

    for row_group in range(0, padded_rows, lanes):
        for col in range(in_features):
            for lane in range(lanes):
                store int8 weight[row_group + lane, col]

Scale stream layout:

    for row_group in range(0, padded_rows, lanes):
        for q8_block in range(in_features // 32):
            for lane in range(lanes):
                store fp16 scale[row_group + lane, q8_block]

Rows beyond the original out_features are zero-padded in both streams.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


PYCHARM_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PYCHARM_ROOT.parent
REFERENCE_ROOT = PYCHARM_ROOT / "pc_reference"
if str(REFERENCE_ROOT) not in sys.path:
    sys.path.insert(0, str(REFERENCE_ROOT))

from q8_0_decode_ref import (  # noqa: E402
    Q8_0_BLOCK_NUM_BYTES,
    Q8_0_BLOCK_SIZE,
    Q8_0_SCALE_NUM_BYTES,
    TensorMeta,
    detect_gguf_endian,
    matrix_layout_from_gguf_shape,
    scale_dtype_for_endian,
)


DEFAULT_GGUF = (
    PROJECT_ROOT
    / "quantized_model"
    / "original_gguf"
    / "SmolLM2-135M-Instruct-Q8_0.gguf"
)
DEFAULT_TENSOR_MAP = Path("fpga_layout") / "tensor_map.json"
DEFAULT_OUT_DIR = Path("fpga_layout") / "q8_0_lane16"

# Public defaults used by golden/reference generators. Keep these aliases stable
# even if the underlying Q8_0 helper constant names change.
DEFAULT_Q8_BLOCK_SIZE = Q8_0_BLOCK_SIZE
DEFAULT_LANES = 16
DEFAULT_SCALE_SHIFT = 20
DEFAULT_SCALE_Q_BITS = 24
DEFAULT_ACC_BITS = 48

LAYOUT_NAME = "q8_0_lane_grouped_col_major_v1"
SCALE_POLICY_NAME = "q8_0_fp16_per_row_per_32_cols_lane_grouped_v1"


@dataclass(frozen=True)
class MapEntry:
    original_name: str
    internal_name: str
    shape: tuple[int, ...]
    role: str
    used: bool
    dtype_or_quant_type: str
    offset: int
    nbytes: int


@dataclass(frozen=True)
class ConvertedTensor:
    entry: MapEntry
    out_features: int
    in_features: int
    padded_out_features: int
    row_padding: int
    blocks_per_row: int
    weight_file: Path
    scale_file: Path
    weight_bytes: int
    scale_bytes: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert GGUF Q8_0 tensors into an FPGA lane-stream layout."
    )
    parser.add_argument("--gguf", type=Path, default=DEFAULT_GGUF)
    parser.add_argument("--tensor-map", type=Path, default=DEFAULT_TENSOR_MAP)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--lanes", type=int, default=16)
    parser.add_argument(
        "--verify-tensor",
        default=None,
        help="Q8_0 tensor to verify after conversion. Default: smallest converted tensor.",
    )
    return parser.parse_args()


def resolve_existing_path(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded.resolve()

    candidates = (
        Path.cwd() / expanded,
        PYCHARM_ROOT / expanded,
        PROJECT_ROOT / expanded,
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return (PYCHARM_ROOT / expanded).resolve()


def resolve_output_path(path: Path) -> Path:
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


def safe_file_stem(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_")


def require_positive_lanes(lanes: int) -> None:
    if lanes <= 0:
        raise ValueError(f"--lanes must be positive, got {lanes}")


def require_options(
    lanes: int,
    q8_block_size: int,
    scale_shift: int,
    max_tensors: int | None = None,
) -> None:
    require_positive_lanes(lanes)
    if q8_block_size != Q8_0_BLOCK_SIZE:
        raise ValueError(
            f"GGUF Q8_0 block size is fixed at {Q8_0_BLOCK_SIZE}; "
            f"got --q8-block-size {q8_block_size}"
        )
    if scale_shift < 0 or scale_shift > 30:
        raise ValueError(f"--scale-shift must be in [0, 30], got {scale_shift}")
    if max_tensors is not None and max_tensors <= 0:
        raise ValueError(f"--max-tensors must be positive, got {max_tensors}")


def filter_entries(
    entries: list[MapEntry],
    only_tensors: list[str],
    max_tensors: int | None,
) -> list[MapEntry]:
    selected = entries
    if only_tensors:
        wanted = set(only_tensors)
        selected = [
            entry
            for entry in selected
            if entry.original_name in wanted or entry.internal_name in wanted
        ]
        matched = {entry.original_name for entry in selected} | {entry.internal_name for entry in selected}
        missing = wanted.difference(matched)
        if missing:
            raise ValueError(
                "requested tensor entries were not found: "
                f"{', '.join(sorted(missing))}"
            )
    if max_tensors is not None:
        selected = selected[:max_tensors]
    return selected


def quantize_scales_to_i32(scales_f16: np.ndarray, scale_shift: int) -> np.ndarray:
    factor = float(1 << scale_shift)
    scale_q_i64 = np.rint(scales_f16.astype(np.float64) * factor).astype(np.int64)
    min_i32 = np.iinfo(np.int32).min
    max_i32 = np.iinfo(np.int32).max
    if int(scale_q_i64.min(initial=0)) < min_i32 or int(scale_q_i64.max(initial=0)) > max_i32:
        raise OverflowError("scale_q exceeds int32 range; lower --scale-shift")
    return scale_q_i64.astype(np.dtype("<i4"), copy=False)


def load_tensor_map(path: Path) -> tuple[dict[str, Any], list[MapEntry]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries: list[MapEntry] = []
    for item in payload.get("mappings", []):
        if not item.get("used", False):
            continue
        if item.get("dtype_or_quant_type") != "Q8_0":
            continue
        if item.get("internal_name") in (None, "", "unknown"):
            continue

        entries.append(
            MapEntry(
                original_name=str(item["original_name"]),
                internal_name=str(item["internal_name"]),
                shape=tuple(int(value) for value in item["shape"]),
                role=str(item["role"]),
                used=bool(item["used"]),
                dtype_or_quant_type=str(item["dtype_or_quant_type"]),
                offset=int(item["offset"]),
                nbytes=int(item["nbytes"]),
            )
        )
    return payload, entries


def map_entry_to_tensor_meta(entry: MapEntry) -> TensorMeta:
    return TensorMeta(
        tensor_name=entry.original_name,
        gguf_shape=entry.shape,
        dtype_or_quant_type=entry.dtype_or_quant_type,
        offset=entry.offset,
        nbytes=entry.nbytes,
    )


def pad_rows(rows: int, lanes: int) -> tuple[int, int]:
    padded = ((rows + lanes - 1) // lanes) * lanes
    return padded, padded - rows


def load_q8_0_quant_and_scales(
    gguf_path: Path,
    entry: MapEntry,
) -> tuple[np.ndarray, np.ndarray, int, int]:
    """Return (int8_weights, fp16_scales_le, out_features, in_features).

    int8_weights shape is [out_features, in_features].
    fp16_scales_le shape is [out_features, in_features // 32].
    """

    meta = map_entry_to_tensor_meta(entry)
    layout = matrix_layout_from_gguf_shape(meta)
    endian_info = detect_gguf_endian(gguf_path)

    raw = np.memmap(
        gguf_path,
        mode="r",
        dtype=np.uint8,
        offset=entry.offset,
        shape=(layout.rows, layout.blocks_per_row, Q8_0_BLOCK_NUM_BYTES),
    )

    quant = np.asarray(raw[:, :, Q8_0_SCALE_NUM_BYTES:], dtype=np.uint8)
    weights = quant.view(np.int8).reshape(layout.rows, layout.cols).copy()

    scale_dtype = scale_dtype_for_endian(endian_info)
    scale_bytes = np.asarray(raw[:, :, :Q8_0_SCALE_NUM_BYTES], dtype=np.uint8)
    scales = scale_bytes.reshape(layout.rows, layout.blocks_per_row, Q8_0_SCALE_NUM_BYTES)
    scales = scales.reshape(layout.rows * layout.blocks_per_row, Q8_0_SCALE_NUM_BYTES)
    scales = scales.copy().view(scale_dtype).reshape(layout.rows, layout.blocks_per_row)
    scales_le = scales.astype(np.dtype("<f2"), copy=False)

    return weights, scales_le, layout.rows, layout.cols


def write_weight_lane_layout(
    path: Path,
    weights: np.ndarray,
    lanes: int,
    padded_rows: int,
    q8_block_size: int = Q8_0_BLOCK_SIZE,
) -> int:
    if q8_block_size != Q8_0_BLOCK_SIZE:
        raise ValueError(
            f"GGUF Q8_0 block size is fixed at {Q8_0_BLOCK_SIZE}; "
            f"got q8_block_size={q8_block_size}"
        )
    out_features, in_features = weights.shape
    zero_group = np.zeros((lanes, in_features), dtype=np.int8)
    bytes_written = 0

    with path.open("wb") as file_obj:
        for row_group in range(0, padded_rows, lanes):
            group = zero_group.copy()
            valid_rows = max(0, min(lanes, out_features - row_group))
            if valid_rows:
                group[:valid_rows, :] = weights[row_group:row_group + valid_rows, :]

            lane_major_by_col = np.ascontiguousarray(group.T)
            file_obj.write(lane_major_by_col.tobytes())
            bytes_written += lane_major_by_col.nbytes

    return bytes_written


def write_scale_lane_layout(
    path: Path,
    scales_le: np.ndarray,
    lanes: int,
    padded_rows: int,
) -> int:
    out_features, blocks_per_row = scales_le.shape
    zero_group = np.zeros((lanes, blocks_per_row), dtype=np.dtype("<f2"))
    bytes_written = 0

    with path.open("wb") as file_obj:
        for row_group in range(0, padded_rows, lanes):
            group = zero_group.copy()
            valid_rows = max(0, min(lanes, out_features - row_group))
            if valid_rows:
                group[:valid_rows, :] = scales_le[row_group:row_group + valid_rows, :]

            lane_major_by_block = np.ascontiguousarray(group.T)
            file_obj.write(lane_major_by_block.tobytes())
            bytes_written += lane_major_by_block.nbytes

    return bytes_written


def write_scale_q_lane_layout(
    path: Path,
    scale_q_i32: np.ndarray,
    *,
    lanes: int,
    padded_rows: int,
) -> int:
    out_features, blocks_per_row = scale_q_i32.shape
    zero_group = np.zeros((lanes,), dtype=np.dtype("<i4"))
    bytes_written = 0

    with path.open("wb") as file_obj:
        for row_group in range(0, padded_rows, lanes):
            valid_rows = max(0, min(lanes, out_features - row_group))
            for block_index in range(blocks_per_row):
                group = zero_group.copy()
                if valid_rows:
                    group[:valid_rows] = scale_q_i32[row_group:row_group + valid_rows, block_index]
                file_obj.write(group.tobytes())
                bytes_written += group.nbytes

    return bytes_written


def write_combined_packet_layout(
    path: Path,
    weights: np.ndarray,
    scale_q_i32: np.ndarray,
    *,
    lanes: int,
    q8_block_size: int,
    padded_rows: int,
) -> int:
    if q8_block_size != Q8_0_BLOCK_SIZE:
        raise ValueError(
            f"GGUF Q8_0 block size is fixed at {Q8_0_BLOCK_SIZE}; "
            f"got q8_block_size={q8_block_size}"
        )

    out_features, in_features = weights.shape
    blocks_per_row = in_features // q8_block_size
    zero_scale_group = np.zeros((lanes,), dtype=np.dtype("<i4"))
    zero_weight_group = np.zeros((lanes, in_features), dtype=np.int8)
    bytes_written = 0

    with path.open("wb") as file_obj:
        for row_group in range(0, padded_rows, lanes):
            valid_rows = max(0, min(lanes, out_features - row_group))
            weight_group = zero_weight_group.copy()
            if valid_rows:
                weight_group[:valid_rows, :] = weights[row_group:row_group + valid_rows, :]

            for block_index in range(blocks_per_row):
                scale_group = zero_scale_group.copy()
                if valid_rows:
                    scale_group[:valid_rows] = scale_q_i32[row_group:row_group + valid_rows, block_index]
                file_obj.write(scale_group.tobytes())
                bytes_written += scale_group.nbytes

                col_start = block_index * q8_block_size
                col_stop = col_start + q8_block_size
                weight_chunk = np.ascontiguousarray(weight_group[:, col_start:col_stop].T)
                file_obj.write(weight_chunk.tobytes())
                bytes_written += weight_chunk.nbytes

    return bytes_written


def convert_one_tensor(
    gguf_path: Path,
    entry: MapEntry,
    out_dir: Path,
    lanes: int,
) -> ConvertedTensor:
    weights, scales_le, out_features, in_features = load_q8_0_quant_and_scales(
        gguf_path,
        entry,
    )
    padded_out_features, row_padding = pad_rows(out_features, lanes)
    blocks_per_row = in_features // Q8_0_BLOCK_SIZE

    stem = safe_file_stem(entry.internal_name)
    weight_file = out_dir / f"{stem}.weights.i8.lane{lanes}.bin"
    scale_file = out_dir / f"{stem}.scales.f16.lane{lanes}.bin"

    weight_bytes = write_weight_lane_layout(weight_file, weights, lanes, padded_out_features)
    scale_bytes = write_scale_lane_layout(scale_file, scales_le, lanes, padded_out_features)

    return ConvertedTensor(
        entry=entry,
        out_features=out_features,
        in_features=in_features,
        padded_out_features=padded_out_features,
        row_padding=row_padding,
        blocks_per_row=blocks_per_row,
        weight_file=weight_file,
        scale_file=scale_file,
        weight_bytes=weight_bytes,
        scale_bytes=scale_bytes,
    )


def restore_weight_lane_layout(
    path: Path,
    out_features: int,
    in_features: int,
    lanes: int,
) -> np.ndarray:
    padded_out_features, _row_padding = pad_rows(out_features, lanes)
    groups = padded_out_features // lanes
    raw = np.fromfile(path, dtype=np.int8)
    expected = groups * in_features * lanes
    if raw.size != expected:
        raise ValueError(f"{path} has {raw.size} int8 values, expected {expected}")

    restored = np.empty((padded_out_features, in_features), dtype=np.int8)
    stream = raw.reshape(groups, in_features, lanes)
    for group_index in range(groups):
        row_group = group_index * lanes
        restored[row_group:row_group + lanes, :] = stream[group_index].T
    return restored[:out_features, :]


def restore_scale_lane_layout(
    path: Path,
    out_features: int,
    blocks_per_row: int,
    lanes: int,
) -> np.ndarray:
    padded_out_features, _row_padding = pad_rows(out_features, lanes)
    groups = padded_out_features // lanes
    raw = np.fromfile(path, dtype=np.dtype("<f2"))
    expected = groups * blocks_per_row * lanes
    if raw.size != expected:
        raise ValueError(f"{path} has {raw.size} fp16 values, expected {expected}")

    restored = np.empty((padded_out_features, blocks_per_row), dtype=np.dtype("<f2"))
    stream = raw.reshape(groups, blocks_per_row, lanes)
    for group_index in range(groups):
        row_group = group_index * lanes
        restored[row_group:row_group + lanes, :] = stream[group_index].T
    return restored[:out_features, :]


def dequantize_q8_0(weights: np.ndarray, scales_le: np.ndarray) -> np.ndarray:
    out_features, in_features = weights.shape
    blocks_per_row = in_features // Q8_0_BLOCK_SIZE
    q = weights.reshape(out_features, blocks_per_row, Q8_0_BLOCK_SIZE).astype(np.float32)
    s = scales_le.astype(np.float32)[:, :, None]
    return (q * s).reshape(out_features, in_features)


def verify_converted_tensor(
    gguf_path: Path,
    converted: ConvertedTensor,
    lanes: int,
) -> dict[str, Any]:
    original_weights, original_scales, out_features, in_features = load_q8_0_quant_and_scales(
        gguf_path,
        converted.entry,
    )
    restored_weights = restore_weight_lane_layout(
        converted.weight_file,
        out_features,
        in_features,
        lanes,
    )
    restored_scales = restore_scale_lane_layout(
        converted.scale_file,
        out_features,
        converted.blocks_per_row,
        lanes,
    )

    weight_exact = bool(np.array_equal(original_weights, restored_weights))
    scale_exact = bool(np.array_equal(original_scales.view(np.uint16), restored_scales.view(np.uint16)))
    original_float = dequantize_q8_0(original_weights, original_scales)
    restored_float = dequantize_q8_0(restored_weights, restored_scales)
    diff = np.abs(original_float - restored_float)

    return {
        "tensor_name": converted.entry.original_name,
        "internal_name": converted.entry.internal_name,
        "weight_int8_exact_match": weight_exact,
        "scale_fp16_exact_match": scale_exact,
        "dequant_max_abs_error": float(np.max(diff)) if diff.size else 0.0,
        "dequant_allclose": bool(np.allclose(original_float, restored_float, rtol=0.0, atol=0.0)),
        "verified_elements": int(original_weights.size),
        "verified_scale_values": int(original_scales.size),
    }


def manifest_tensor_entry(converted: ConvertedTensor, out_dir: Path, lanes: int) -> dict[str, Any]:
    entry = converted.entry
    return {
        "tensor_name": entry.original_name,
        "internal_name": entry.internal_name,
        "role": entry.role,
        "original_shape": list(entry.shape),
        "logical_shape": [converted.out_features, converted.in_features],
        "padded_shape": [converted.padded_out_features, converted.in_features],
        "in_features": converted.in_features,
        "out_features": converted.out_features,
        "padded_out_features": converted.padded_out_features,
        "row_padding": converted.row_padding,
        "lanes": lanes,
        "quant_type": entry.dtype_or_quant_type,
        "layout_name": LAYOUT_NAME,
        "weight_file": str(converted.weight_file.relative_to(out_dir)),
        "weight_bytes": converted.weight_bytes,
        "scale_policy": {
            "name": SCALE_POLICY_NAME,
            "scale_dtype": "fp16_le",
            "scale_scope": "one scale per output row per 32 input columns",
            "scale_order": "row_group, q8_0_block_index, lane",
            "q8_0_block_size": Q8_0_BLOCK_SIZE,
            "scale_file": str(converted.scale_file.relative_to(out_dir)),
            "scale_bytes": converted.scale_bytes,
        },
        "source": {
            "gguf_offset": entry.offset,
            "gguf_nbytes": entry.nbytes,
        },
    }


def build_manifest(
    gguf_path: Path,
    tensor_map_path: Path,
    out_dir: Path,
    lanes: int,
    converted_tensors: list[ConvertedTensor],
    skipped_count: int,
    verification: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "layout_name": LAYOUT_NAME,
        "lanes": lanes,
        "source": {
            "gguf": project_relative(gguf_path),
            "tensor_map": project_relative(tensor_map_path),
        },
        "summary": {
            "converted_tensors": len(converted_tensors),
            "skipped_tensors": skipped_count,
            "total_weight_bytes": sum(item.weight_bytes for item in converted_tensors),
            "total_scale_bytes": sum(item.scale_bytes for item in converted_tensors),
        },
        "scale_policy": {
            "name": SCALE_POLICY_NAME,
            "scale_dtype": "fp16_le",
            "scale_order": "row_group, q8_0_block_index, lane",
            "q8_0_block_size": Q8_0_BLOCK_SIZE,
        },
        "verification": verification,
        "tensors": [
            manifest_tensor_entry(item, out_dir, lanes)
            for item in converted_tensors
        ],
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, indent=2)
        file_obj.write("\n")


def build_report(manifest: dict[str, Any]) -> str:
    verification = manifest.get("verification") or {}
    summary = manifest["summary"]
    lines = [
        "Q8_0 FPGA lane layout conversion report",
        f"layout: {manifest['layout_name']}",
        f"lanes: {manifest['lanes']}",
        f"source gguf: {manifest['source']['gguf']}",
        f"source tensor map: {manifest['source']['tensor_map']}",
        "",
        "Summary:",
        f"- converted tensors: {summary['converted_tensors']}",
        f"- skipped tensors: {summary['skipped_tensors']}",
        f"- total weight bytes: {summary['total_weight_bytes']}",
        f"- total scale bytes: {summary['total_scale_bytes']}",
        "",
        "Layout:",
        "- weight order: row_group, col, lane",
        "- scale order: row_group, q8_0_block_index, lane",
        "- row padding: zero int8 weights and zero fp16 scales",
        "- q8_0_block_index maps to columns [block*32, block*32 + 32)",
        "",
        "Verification:",
    ]

    if verification:
        for key, value in verification.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- not run")

    lines.extend(["", "Converted tensors:"])
    for item in manifest["tensors"]:
        lines.append(
            "- "
            f"{item['tensor_name']} -> {item['weight_file']} "
            f"shape={item['logical_shape']} padded={item['padded_shape']} "
            f"scale={item['scale_policy']['scale_file']}"
        )

    return "\n".join(lines) + "\n"


def choose_verification_tensor(
    converted_tensors: list[ConvertedTensor],
    verify_tensor: str | None,
) -> ConvertedTensor | None:
    if not converted_tensors:
        return None
    if verify_tensor is None:
        return min(converted_tensors, key=lambda item: item.entry.nbytes)
    for item in converted_tensors:
        if verify_tensor in (item.entry.original_name, item.entry.internal_name):
            return item
    raise ValueError(f"--verify-tensor {verify_tensor!r} was not converted")


def main() -> int:
    args = parse_args()

    try:
        require_positive_lanes(args.lanes)
        gguf_path = resolve_existing_path(args.gguf)
        tensor_map_path = resolve_existing_path(args.tensor_map)
        out_dir = resolve_output_path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)

        tensor_map_payload, entries = load_tensor_map(tensor_map_path)
        all_mappings = tensor_map_payload.get("mappings", [])
        skipped_count = len(all_mappings) - len(entries)

        converted_tensors: list[ConvertedTensor] = []
        for entry in entries:
            converted = convert_one_tensor(gguf_path, entry, out_dir, args.lanes)
            converted_tensors.append(converted)
            print(
                f"[OK] {entry.original_name} -> "
                f"{converted.weight_file.name}, {converted.scale_file.name}"
            )

        verification_target = choose_verification_tensor(converted_tensors, args.verify_tensor)
        verification = (
            verify_converted_tensor(gguf_path, verification_target, args.lanes)
            if verification_target is not None
            else None
        )

        manifest = build_manifest(
            gguf_path=gguf_path,
            tensor_map_path=tensor_map_path,
            out_dir=out_dir,
            lanes=args.lanes,
            converted_tensors=converted_tensors,
            skipped_count=skipped_count,
            verification=verification,
        )
        manifest_path = out_dir / "manifest.json"
        report_path = out_dir / "layout_report.txt"
        write_json(manifest_path, manifest)
        report_path.write_text(build_report(manifest), encoding="utf-8")
    except Exception as exc:
        print(f"[FAIL] Q8_0 FPGA layout conversion failed: {exc}", file=sys.stderr)
        return 1

    print(f"manifest: {manifest_path}")
    print(f"report: {report_path}")
    if verification:
        print(
            "verification: "
            f"tensor={verification['tensor_name']} "
            f"weight_exact={verification['weight_int8_exact_match']} "
            f"scale_exact={verification['scale_fp16_exact_match']} "
            f"max_error={verification['dequant_max_abs_error']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
