"""Convert GGUF Q8_0 tensors to FPGA GEMV lane and fixed-scale streams.

This converter keeps Q8_0 block structure visible to RTL:

    for row_group in rows step lanes:
        for q8_block in cols step q8_block_size:
            write scale_q[row_group + lane][q8_block] for each lane
            write int8 weights for each col in the 32-column block, lane-major

The default output uses separate files for the int8 weight payload and the
fixed-point scale stream. Use --emit-packet to also write a combined packet file
that stores the scale header immediately before each block payload.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYCHARM_ROOT = PROJECT_ROOT / "pycharm"
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
DEFAULT_LANES = 16
DEFAULT_Q8_BLOCK_SIZE = 32
DEFAULT_SCALE_SHIFT = 20
DEFAULT_DEBUG_SEED = 20260626

LAYOUT_NAME = "q8_0_lane_grouped_block_major_scale_q_v1"
SCALE_POLICY_NAME = "q8_0_fixed_i32_per_row_per_32_cols_lane_grouped_v1"
SCALE_DTYPE = "int32_le"


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
class ScaleErrorStats:
    scale_max_abs_error: float
    scale_mean_abs_error: float
    dequant_weight_max_abs_error: float
    dequant_weight_mean_abs_error: float
    max_abs_scale: float
    max_abs_scale_q: int


@dataclass(frozen=True)
class ConvertedTensor:
    entry: MapEntry
    out_features: int
    in_features: int
    padded_out_features: int
    row_padding: int
    q8_blocks_per_row: int
    weight_file: Path
    scale_q_file: Path
    packet_file: Path | None
    weight_bytes: int
    scale_q_bytes: int
    packet_bytes: int
    scale_error: ScaleErrorStats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert GGUF Q8_0 tensors to FPGA lane layout with fixed-point scales."
    )
    parser.add_argument("--gguf", type=Path, default=DEFAULT_GGUF)
    parser.add_argument("--tensor-map", type=Path, default=DEFAULT_TENSOR_MAP)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--lanes", type=int, default=DEFAULT_LANES)
    parser.add_argument("--q8-block-size", type=int, default=DEFAULT_Q8_BLOCK_SIZE)
    parser.add_argument("--scale-shift", type=int, default=DEFAULT_SCALE_SHIFT)
    parser.add_argument(
        "--verify-tensor",
        default=None,
        help="Q8_0 tensor to round-trip verify. Default: smallest converted tensor.",
    )
    parser.add_argument(
        "--only-tensor",
        action="append",
        default=[],
        help="Convert only this original_name or internal_name. Can be repeated.",
    )
    parser.add_argument(
        "--max-tensors",
        type=int,
        default=None,
        help="Convert at most N Q8_0 tensors. Useful for smoke tests.",
    )
    parser.add_argument(
        "--emit-packet",
        action="store_true",
        help="Also emit a combined block packet file: scale_q lanes followed by 32*lanes int8 weights.",
    )
    parser.add_argument(
        "--emit-unscaled-debug-reference",
        action="store_true",
        help="Emit input_i16.bin, block_acc_i32.bin, and output_ref_i32.bin for the verification tensor.",
    )
    parser.add_argument(
        "--debug-reference-tensor",
        default=None,
        help="Tensor for unscaled debug reference. Default: verification tensor.",
    )
    parser.add_argument("--debug-seed", type=int, default=DEFAULT_DEBUG_SEED)
    return parser.parse_args()


def resolve_existing_path(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded.resolve()

    candidates = (
        Path.cwd() / expanded,
        PROJECT_ROOT / expanded,
        PYCHARM_ROOT / expanded,
        PROJECT_ROOT / "old" / expanded,
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return (PROJECT_ROOT / expanded).resolve()


def resolve_output_path(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (PROJECT_ROOT / expanded).resolve()


def project_relative(path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def output_relative(path: Path, out_dir: Path) -> str:
    try:
        return path.resolve().relative_to(out_dir.resolve()).as_posix()
    except ValueError:
        return project_relative(path)


def safe_file_stem(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_")


def require_options(lanes: int, q8_block_size: int, scale_shift: int, max_tensors: int | None) -> None:
    if lanes <= 0:
        raise ValueError(f"--lanes must be positive, got {lanes}")
    if q8_block_size != Q8_0_BLOCK_SIZE:
        raise ValueError(
            f"GGUF Q8_0 block size is fixed at {Q8_0_BLOCK_SIZE}; got --q8-block-size {q8_block_size}"
        )
    if scale_shift < 0 or scale_shift > 30:
        raise ValueError(f"--scale-shift must be in [0, 30] for int32 scale_q, got {scale_shift}")
    if max_tensors is not None and max_tensors <= 0:
        raise ValueError(f"--max-tensors must be positive, got {max_tensors}")


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
        if item.get("offset") in (None, "") or item.get("nbytes") in (None, ""):
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


def filter_entries(entries: list[MapEntry], only_tensors: list[str], max_tensors: int | None) -> list[MapEntry]:
    selected = entries
    if only_tensors:
        wanted = set(only_tensors)
        selected = [
            entry
            for entry in selected
            if entry.original_name in wanted or entry.internal_name in wanted
        ]
        missing = wanted.difference(
            {entry.original_name for entry in selected} | {entry.internal_name for entry in selected}
        )
        if missing:
            raise ValueError(f"requested --only-tensor entries were not found: {', '.join(sorted(missing))}")
    if max_tensors is not None:
        selected = selected[:max_tensors]
    return selected


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


def load_q8_0_quant_and_scales(gguf_path: Path, entry: MapEntry) -> tuple[np.ndarray, np.ndarray, int, int]:
    """Return (weights_i8, scales_f16_le, out_features, in_features)."""

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
    weights_i8 = quant.view(np.int8).reshape(layout.rows, layout.cols).copy()

    scale_dtype = scale_dtype_for_endian(endian_info)
    scale_bytes = np.asarray(raw[:, :, :Q8_0_SCALE_NUM_BYTES], dtype=np.uint8)
    scales = scale_bytes.reshape(layout.rows * layout.blocks_per_row, Q8_0_SCALE_NUM_BYTES)
    scales_f16 = scales.copy().view(scale_dtype).reshape(layout.rows, layout.blocks_per_row)
    scales_f16_le = scales_f16.astype(np.dtype("<f2"), copy=False)

    return weights_i8, scales_f16_le, layout.rows, layout.cols


def quantize_scales_to_i32(scales_f16: np.ndarray, scale_shift: int) -> np.ndarray:
    factor = float(1 << scale_shift)
    scale_q_i64 = np.rint(scales_f16.astype(np.float64) * factor).astype(np.int64)
    min_i32 = np.iinfo(np.int32).min
    max_i32 = np.iinfo(np.int32).max
    if int(scale_q_i64.min(initial=0)) < min_i32 or int(scale_q_i64.max(initial=0)) > max_i32:
        raise OverflowError("scale_q exceeds int32 range; lower --scale-shift")
    return scale_q_i64.astype(np.dtype("<i4"), copy=False)


def fixed_scale_error_stats(
    weights_i8: np.ndarray,
    scales_f16: np.ndarray,
    scale_q_i32: np.ndarray,
    scale_shift: int,
) -> ScaleErrorStats:
    factor = float(1 << scale_shift)
    scale_float = scales_f16.astype(np.float64)
    fixed_scale = scale_q_i32.astype(np.float64) / factor
    scale_abs_error = np.abs(scale_float - fixed_scale)

    rows, cols = weights_i8.shape
    blocks = cols // Q8_0_BLOCK_SIZE
    weights_block_abs = np.abs(weights_i8.reshape(rows, blocks, Q8_0_BLOCK_SIZE).astype(np.int16))
    dequant_error = weights_block_abs.astype(np.float64) * scale_abs_error[:, :, None]

    return ScaleErrorStats(
        scale_max_abs_error=float(scale_abs_error.max(initial=0.0)),
        scale_mean_abs_error=float(scale_abs_error.mean() if scale_abs_error.size else 0.0),
        dequant_weight_max_abs_error=float(dequant_error.max(initial=0.0)),
        dequant_weight_mean_abs_error=float(dequant_error.mean() if dequant_error.size else 0.0),
        max_abs_scale=float(np.max(np.abs(scale_float)) if scale_float.size else 0.0),
        max_abs_scale_q=int(np.max(np.abs(scale_q_i32.astype(np.int64))) if scale_q_i32.size else 0),
    )


def write_weight_lane_layout(
    path: Path,
    weights_i8: np.ndarray,
    *,
    lanes: int,
    q8_block_size: int,
    padded_rows: int,
) -> int:
    out_features, in_features = weights_i8.shape
    blocks_per_row = in_features // q8_block_size
    zero_block = np.zeros((lanes, q8_block_size), dtype=np.int8)
    bytes_written = 0

    with path.open("wb") as file_obj:
        for row_group in range(0, padded_rows, lanes):
            valid_rows = max(0, min(lanes, out_features - row_group))
            for block_index in range(blocks_per_row):
                start = block_index * q8_block_size
                stop = start + q8_block_size
                group = zero_block.copy()
                if valid_rows:
                    group[:valid_rows, :] = weights_i8[row_group:row_group + valid_rows, start:stop]

                # stream order: col within Q8_0 block, then lane
                chunk = np.ascontiguousarray(group.T)
                file_obj.write(chunk.tobytes())
                bytes_written += chunk.nbytes

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
    weights_i8: np.ndarray,
    scale_q_i32: np.ndarray,
    *,
    lanes: int,
    q8_block_size: int,
    padded_rows: int,
) -> int:
    out_features, in_features = weights_i8.shape
    blocks_per_row = in_features // q8_block_size
    zero_scale_group = np.zeros((lanes,), dtype=np.dtype("<i4"))
    zero_weight_block = np.zeros((lanes, q8_block_size), dtype=np.int8)
    bytes_written = 0

    with path.open("wb") as file_obj:
        for row_group in range(0, padded_rows, lanes):
            valid_rows = max(0, min(lanes, out_features - row_group))
            for block_index in range(blocks_per_row):
                scale_group = zero_scale_group.copy()
                if valid_rows:
                    scale_group[:valid_rows] = scale_q_i32[row_group:row_group + valid_rows, block_index]
                file_obj.write(scale_group.tobytes())
                bytes_written += scale_group.nbytes

                start = block_index * q8_block_size
                stop = start + q8_block_size
                weight_group = zero_weight_block.copy()
                if valid_rows:
                    weight_group[:valid_rows, :] = weights_i8[row_group:row_group + valid_rows, start:stop]
                weight_chunk = np.ascontiguousarray(weight_group.T)
                file_obj.write(weight_chunk.tobytes())
                bytes_written += weight_chunk.nbytes

    return bytes_written


def restore_weight_lane_layout(path: Path, rows: int, cols: int, lanes: int, q8_block_size: int) -> np.ndarray:
    padded_rows, _row_padding = pad_rows(rows, lanes)
    row_groups = padded_rows // lanes
    blocks_per_row = cols // q8_block_size
    raw = np.fromfile(path, dtype=np.int8)
    expected = row_groups * blocks_per_row * q8_block_size * lanes
    if raw.size != expected:
        raise ValueError(f"{path} has {raw.size} int8 values, expected {expected}")

    stream = raw.reshape(row_groups, blocks_per_row, q8_block_size, lanes)
    restored = np.empty((padded_rows, cols), dtype=np.int8)
    for group_index in range(row_groups):
        row_base = group_index * lanes
        for block_index in range(blocks_per_row):
            col_base = block_index * q8_block_size
            restored[row_base:row_base + lanes, col_base:col_base + q8_block_size] = stream[
                group_index, block_index
            ].T
    return restored[:rows, :]


def restore_scale_q_lane_layout(path: Path, rows: int, blocks_per_row: int, lanes: int) -> np.ndarray:
    padded_rows, _row_padding = pad_rows(rows, lanes)
    row_groups = padded_rows // lanes
    raw = np.fromfile(path, dtype=np.dtype("<i4"))
    expected = row_groups * blocks_per_row * lanes
    if raw.size != expected:
        raise ValueError(f"{path} has {raw.size} int32 values, expected {expected}")

    stream = raw.reshape(row_groups, blocks_per_row, lanes)
    restored = np.empty((padded_rows, blocks_per_row), dtype=np.dtype("<i4"))
    for group_index in range(row_groups):
        row_base = group_index * lanes
        restored[row_base:row_base + lanes, :] = stream[group_index].T
    return restored[:rows, :]


def choose_verification_tensor(
    converted_tensors: list[ConvertedTensor], verify_tensor: str | None
) -> ConvertedTensor | None:
    if not converted_tensors:
        return None
    if verify_tensor is None:
        return min(converted_tensors, key=lambda item: item.entry.nbytes)
    for item in converted_tensors:
        if verify_tensor in (item.entry.original_name, item.entry.internal_name):
            return item
    raise ValueError(f"--verify-tensor {verify_tensor!r} was not converted")


def verify_converted_tensor(
    gguf_path: Path,
    converted: ConvertedTensor,
    *,
    lanes: int,
    q8_block_size: int,
    scale_shift: int,
) -> dict[str, Any]:
    weights_i8, scales_f16, rows, cols = load_q8_0_quant_and_scales(gguf_path, converted.entry)
    scale_q_i32 = quantize_scales_to_i32(scales_f16, scale_shift)
    restored_weights = restore_weight_lane_layout(
        converted.weight_file, rows, cols, lanes, q8_block_size
    )
    restored_scale_q = restore_scale_q_lane_layout(
        converted.scale_q_file, rows, converted.q8_blocks_per_row, lanes
    )
    return {
        "tensor_name": converted.entry.original_name,
        "internal_name": converted.entry.internal_name,
        "weight_int8_exact_match": bool(np.array_equal(weights_i8, restored_weights)),
        "scale_q_i32_exact_match": bool(np.array_equal(scale_q_i32, restored_scale_q)),
        "verified_weight_values": int(weights_i8.size),
        "verified_scale_values": int(scale_q_i32.size),
        "scale_shift": scale_shift,
        "scale_dtype": SCALE_DTYPE,
    }


def compute_unscaled_block_acc(input_i16: np.ndarray, weights_i8: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    rows, cols = weights_i8.shape
    blocks = cols // Q8_0_BLOCK_SIZE
    block_acc = np.empty((rows, blocks), dtype=np.dtype("<i4"))
    input_i32 = input_i16.astype(np.int32)
    weights_i32 = weights_i8.astype(np.int32)
    for block_index in range(blocks):
        start = block_index * Q8_0_BLOCK_SIZE
        stop = start + Q8_0_BLOCK_SIZE
        block_acc[:, block_index] = (
            weights_i32[:, start:stop] * input_i32[start:stop]
        ).sum(axis=1, dtype=np.int32)
    output_i32 = block_acc.sum(axis=1, dtype=np.int32).astype(np.dtype("<i4"))
    return block_acc, output_i32


def write_unscaled_debug_reference(
    out_dir: Path,
    gguf_path: Path,
    converted: ConvertedTensor,
    *,
    seed: int,
) -> dict[str, Any]:
    weights_i8, _scales_f16, rows, cols = load_q8_0_quant_and_scales(gguf_path, converted.entry)
    rng = np.random.default_rng(seed)
    input_i16 = rng.integers(-512, 512, size=cols, dtype=np.int16).astype(np.dtype("<i2"))
    block_acc_i32, output_ref_i32 = compute_unscaled_block_acc(input_i16, weights_i8)

    debug_dir = out_dir / "debug_reference" / safe_file_stem(converted.entry.internal_name)
    debug_dir.mkdir(parents=True, exist_ok=True)
    input_file = debug_dir / "input_i16.bin"
    block_acc_file = debug_dir / "block_acc_i32.bin"
    output_i32_file = debug_dir / "output_ref_i32.bin"
    input_i16.tofile(input_file)
    block_acc_i32.tofile(block_acc_file)
    output_ref_i32.tofile(output_i32_file)

    return {
        "tensor_name": converted.entry.original_name,
        "internal_name": converted.entry.internal_name,
        "seed": seed,
        "input_i16": output_relative(input_file, out_dir),
        "block_acc_i32": output_relative(block_acc_file, out_dir),
        "output_ref_i32": output_relative(output_i32_file, out_dir),
        "block_acc_shape": [rows, converted.q8_blocks_per_row],
        "output_ref_i32_shape": [rows],
        "note": "Unscaled debug reference; scale_q is not applied.",
    }


def convert_one_tensor(
    gguf_path: Path,
    entry: MapEntry,
    out_dir: Path,
    *,
    lanes: int,
    q8_block_size: int,
    scale_shift: int,
    emit_packet: bool,
) -> ConvertedTensor:
    weights_i8, scales_f16, out_features, in_features = load_q8_0_quant_and_scales(gguf_path, entry)
    if in_features % q8_block_size != 0:
        raise ValueError(f"{entry.original_name}: in_features={in_features} not divisible by {q8_block_size}")

    padded_out_features, row_padding = pad_rows(out_features, lanes)
    blocks_per_row = in_features // q8_block_size
    scale_q_i32 = quantize_scales_to_i32(scales_f16, scale_shift)
    scale_error = fixed_scale_error_stats(weights_i8, scales_f16, scale_q_i32, scale_shift)

    stem = safe_file_stem(entry.internal_name)
    weight_file = out_dir / f"{stem}.weights.i8.lane{lanes}.bin"
    scale_q_file = out_dir / f"{stem}.scales.q{scale_shift}.i32.lane{lanes}.bin"
    packet_file = (
        out_dir / f"{stem}.packets.scale_i32_w_i8.q{scale_shift}.lane{lanes}.bin"
        if emit_packet
        else None
    )

    weight_bytes = write_weight_lane_layout(
        weight_file,
        weights_i8,
        lanes=lanes,
        q8_block_size=q8_block_size,
        padded_rows=padded_out_features,
    )
    scale_q_bytes = write_scale_q_lane_layout(
        scale_q_file,
        scale_q_i32,
        lanes=lanes,
        padded_rows=padded_out_features,
    )
    packet_bytes = 0
    if packet_file is not None:
        packet_bytes = write_combined_packet_layout(
            packet_file,
            weights_i8,
            scale_q_i32,
            lanes=lanes,
            q8_block_size=q8_block_size,
            padded_rows=padded_out_features,
        )

    return ConvertedTensor(
        entry=entry,
        out_features=out_features,
        in_features=in_features,
        padded_out_features=padded_out_features,
        row_padding=row_padding,
        q8_blocks_per_row=blocks_per_row,
        weight_file=weight_file,
        scale_q_file=scale_q_file,
        packet_file=packet_file,
        weight_bytes=weight_bytes,
        scale_q_bytes=scale_q_bytes,
        packet_bytes=packet_bytes,
        scale_error=scale_error,
    )


def scale_error_to_dict(stats: ScaleErrorStats) -> dict[str, Any]:
    return {
        "scale_max_abs_error": stats.scale_max_abs_error,
        "scale_mean_abs_error": stats.scale_mean_abs_error,
        "dequant_weight_max_abs_error": stats.dequant_weight_max_abs_error,
        "dequant_weight_mean_abs_error": stats.dequant_weight_mean_abs_error,
        "max_abs_scale": stats.max_abs_scale,
        "max_abs_scale_q": stats.max_abs_scale_q,
    }


def manifest_tensor_entry(
    converted: ConvertedTensor,
    out_dir: Path,
    *,
    lanes: int,
    q8_block_size: int,
    scale_shift: int,
) -> dict[str, Any]:
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
        "q8_block_size": q8_block_size,
        "q8_blocks_per_row": converted.q8_blocks_per_row,
        "quant_type": entry.dtype_or_quant_type,
        "layout_name": LAYOUT_NAME,
        "layout_order": {
            "scale_q": "row_group, q8_0_block_index, lane",
            "weight": "row_group, q8_0_block_index, column_in_block, lane",
            "packet": "row_group, q8_0_block_index, scale_q_lanes, weight_columns_lanes",
        },
        "weight_file": output_relative(converted.weight_file, out_dir),
        "weight_bytes": converted.weight_bytes,
        "scale_q_file": output_relative(converted.scale_q_file, out_dir),
        "scale_q_bytes": converted.scale_q_bytes,
        "scale_shift": scale_shift,
        "scale_dtype": SCALE_DTYPE,
        "scale_policy": {
            "name": SCALE_POLICY_NAME,
            "scale_shift": scale_shift,
            "scale_dtype": SCALE_DTYPE,
            "formula": "scale_q = round(scale * 2^scale_shift)",
            "apply": "fixed_output = sum(block_acc_i32 * scale_q) / 2^scale_shift",
        },
        "packet_file": output_relative(converted.packet_file, out_dir) if converted.packet_file else None,
        "packet_bytes": converted.packet_bytes,
        "fixed_scale_error": scale_error_to_dict(converted.scale_error),
        "source": {
            "gguf_offset": entry.offset,
            "gguf_nbytes": entry.nbytes,
        },
    }


def build_manifest(
    *,
    gguf_path: Path,
    tensor_map_path: Path,
    out_dir: Path,
    lanes: int,
    q8_block_size: int,
    scale_shift: int,
    converted_tensors: list[ConvertedTensor],
    total_q8_entries: int,
    verification: dict[str, Any] | None,
    debug_reference: dict[str, Any] | None,
) -> dict[str, Any]:
    tensor_entries = [
        manifest_tensor_entry(
            item,
            out_dir,
            lanes=lanes,
            q8_block_size=q8_block_size,
            scale_shift=scale_shift,
        )
        for item in converted_tensors
    ]

    worst_scale = max(
        (item.scale_error.scale_max_abs_error for item in converted_tensors),
        default=0.0,
    )
    worst_dequant = max(
        (item.scale_error.dequant_weight_max_abs_error for item in converted_tensors),
        default=0.0,
    )

    return {
        "schema_version": 1,
        "layout_name": LAYOUT_NAME,
        "source": {
            "gguf": project_relative(gguf_path),
            "tensor_map": project_relative(tensor_map_path),
        },
        "parameters": {
            "lanes": lanes,
            "q8_block_size": q8_block_size,
            "scale_shift": scale_shift,
            "scale_dtype": SCALE_DTYPE,
            "scale_formula": "scale_q = round(scale * 2^scale_shift)",
        },
        "summary": {
            "converted_tensors": len(converted_tensors),
            "available_q8_tensors": total_q8_entries,
            "skipped_q8_tensors": total_q8_entries - len(converted_tensors),
            "total_weight_bytes": sum(item.weight_bytes for item in converted_tensors),
            "total_scale_q_bytes": sum(item.scale_q_bytes for item in converted_tensors),
            "total_packet_bytes": sum(item.packet_bytes for item in converted_tensors),
            "worst_scale_max_abs_error": worst_scale,
            "worst_dequant_weight_max_abs_error": worst_dequant,
        },
        "layout_order": {
            "scale_q_file": "row_group, q8_0_block_index, lane",
            "weight_file": "row_group, q8_0_block_index, column_in_block, lane",
            "packet_file": "row_group, q8_0_block_index, scale_q_lanes, weight_columns_lanes",
            "row_padding": "rows >= out_features use zero scale_q and zero int8 weights",
        },
        "verification": verification,
        "debug_reference": debug_reference,
        "tensors": tensor_entries,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, indent=2)
        file_obj.write("\n")


def build_fixed_scale_error_report(manifest: dict[str, Any]) -> str:
    summary = manifest["summary"]
    params = manifest["parameters"]
    verification = manifest.get("verification") or {}
    lines = [
        "# Fixed Scale Error Report",
        "",
        "## Summary",
        "",
        f"- layout: `{manifest['layout_name']}`",
        f"- lanes: `{params['lanes']}`",
        f"- q8 block size: `{params['q8_block_size']}`",
        f"- scale shift: `{params['scale_shift']}`",
        f"- scale dtype: `{params['scale_dtype']}`",
        f"- converted tensors: `{summary['converted_tensors']}`",
        f"- worst scale max abs error: `{summary['worst_scale_max_abs_error']}`",
        f"- worst dequantized weight max abs error: `{summary['worst_dequant_weight_max_abs_error']}`",
        "",
        "## Verification",
        "",
    ]
    if verification:
        for key, value in verification.items():
            lines.append(f"- {key}: `{value}`")
    else:
        lines.append("- not run")

    lines.extend(
        [
            "",
            "## Tensor Error Table",
            "",
            "| tensor | shape | scale max err | scale mean err | dequant max err | dequant mean err | max abs scale_q |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    rows = sorted(
        manifest["tensors"],
        key=lambda item: item["fixed_scale_error"]["dequant_weight_max_abs_error"],
        reverse=True,
    )
    for item in rows:
        err = item["fixed_scale_error"]
        lines.append(
            "| "
            f"`{item['internal_name']}` | `{item['logical_shape']}` | "
            f"{err['scale_max_abs_error']:.10g} | "
            f"{err['scale_mean_abs_error']:.10g} | "
            f"{err['dequant_weight_max_abs_error']:.10g} | "
            f"{err['dequant_weight_mean_abs_error']:.10g} | "
            f"{err['max_abs_scale_q']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()

    try:
        require_options(args.lanes, args.q8_block_size, args.scale_shift, args.max_tensors)
        gguf_path = resolve_existing_path(args.gguf)
        tensor_map_path = resolve_existing_path(args.tensor_map)
        out_dir = resolve_output_path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)

        _tensor_map_payload, entries = load_tensor_map(tensor_map_path)
        selected_entries = filter_entries(entries, args.only_tensor, args.max_tensors)
        if not selected_entries:
            raise ValueError("no Q8_0 tensor entries selected for conversion")

        converted_tensors: list[ConvertedTensor] = []
        for entry in selected_entries:
            converted = convert_one_tensor(
                gguf_path,
                entry,
                out_dir,
                lanes=args.lanes,
                q8_block_size=args.q8_block_size,
                scale_shift=args.scale_shift,
                emit_packet=args.emit_packet,
            )
            converted_tensors.append(converted)
            print(
                f"[OK] {entry.original_name} -> "
                f"{converted.weight_file.name}, {converted.scale_q_file.name}"
            )

        verification_target = choose_verification_tensor(converted_tensors, args.verify_tensor)
        verification = None
        if verification_target is not None:
            verification = verify_converted_tensor(
                gguf_path,
                verification_target,
                lanes=args.lanes,
                q8_block_size=args.q8_block_size,
                scale_shift=args.scale_shift,
            )

        debug_reference = None
        if args.emit_unscaled_debug_reference:
            debug_target = choose_verification_tensor(
                converted_tensors,
                args.debug_reference_tensor or (verification_target.entry.internal_name if verification_target else None),
            )
            if debug_target is not None:
                debug_reference = write_unscaled_debug_reference(
                    out_dir,
                    gguf_path,
                    debug_target,
                    seed=args.debug_seed,
                )

        manifest = build_manifest(
            gguf_path=gguf_path,
            tensor_map_path=tensor_map_path,
            out_dir=out_dir,
            lanes=args.lanes,
            q8_block_size=args.q8_block_size,
            scale_shift=args.scale_shift,
            converted_tensors=converted_tensors,
            total_q8_entries=len(entries),
            verification=verification,
            debug_reference=debug_reference,
        )
        manifest_path = out_dir / "manifest.json"
        report_path = out_dir / "fixed_scale_error_report.md"
        write_json(manifest_path, manifest)
        report_path.write_text(build_fixed_scale_error_report(manifest), encoding="utf-8")
    except Exception as exc:
        print(f"[FAIL] Q8_0 FPGA fixed-scale layout conversion failed: {exc}", file=sys.stderr)
        return 1

    print(f"manifest: {manifest_path}")
    print(f"fixed scale report: {report_path}")
    if verification:
        print(
            "verification: "
            f"tensor={verification['tensor_name']} "
            f"weight_exact={verification['weight_int8_exact_match']} "
            f"scale_q_exact={verification['scale_q_i32_exact_match']}"
        )
    if debug_reference:
        print(f"debug reference: {debug_reference['block_acc_i32']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
