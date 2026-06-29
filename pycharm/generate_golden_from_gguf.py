"""Generate FPGA GEMV golden vectors from GGUF Q8_0 tensors.

The generated files are little-endian raw binaries intended to be read by C and
Verilog testbenches. The stream layout matches pc_tools/convert_q8_0_to_fpga_layout.py:

    for row_group in range(0, padded_out_features, lanes):
        for q8_block in range(in_features // 32):
            write scale_q[row_group + lane, q8_block] for lane in lanes
            for column_in_block in range(32):
                write weight[row_group + lane, block*32 + column_in_block]

Q8_0 fp16 scales are converted to fixed-point int32 with:

    scale_q = round(scale * 2^scale_shift)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYCHARM_REFERENCE_ROOT = PROJECT_ROOT / "pycharm" / "pc_reference"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PYCHARM_REFERENCE_ROOT) not in sys.path:
    sys.path.insert(0, str(PYCHARM_REFERENCE_ROOT))

from pc_tools.convert_q8_0_to_fpga_layout import (  # noqa: E402
    DEFAULT_GGUF,
    DEFAULT_Q8_BLOCK_SIZE,
    DEFAULT_SCALE_SHIFT,
    DEFAULT_TENSOR_MAP,
    LAYOUT_NAME,
    MapEntry,
    filter_entries,
    load_q8_0_quant_and_scales,
    load_tensor_map,
    pad_rows,
    project_relative,
    quantize_scales_to_i32,
    require_options,
    resolve_existing_path,
    resolve_output_path,
    safe_file_stem,
    write_combined_packet_layout,
    write_scale_q_lane_layout,
    write_weight_lane_layout,
)
from q8_0_decode_ref import Q8_0_BLOCK_SIZE, decode_tensor_slice  # noqa: E402


DEFAULT_OUT = Path("golden")
DEFAULT_LANES = 16
DEFAULT_SEED = 20260626
DEFAULT_LM_HEAD_ROW_START = 0
DEFAULT_LM_HEAD_ROW_COUNT = 32

FAKE_ROWS = 3
FAKE_COLS = 32


@dataclass(frozen=True)
class GoldenCase:
    case_name: str
    tensor_name: str
    internal_name: str
    role: str
    source_kind: str
    row_start: int
    row_count: int
    in_features: int
    out_features: int
    entry: MapEntry | None = None
    tied_lm_head: bool = False
    source_note: str = ""


@dataclass(frozen=True)
class CaseArrays:
    input_i16: np.ndarray
    weights_i8: np.ndarray
    scales_f16: np.ndarray
    scale_q_i32: np.ndarray
    block_acc_i32: np.ndarray
    output_scaled_i32: np.ndarray
    output_ref_float: np.ndarray


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate FPGA GEMV golden vectors from GGUF Q8_0 tensors."
    )
    parser.add_argument("--gguf", type=Path, default=DEFAULT_GGUF)
    parser.add_argument("--tensor-map", type=Path, default=DEFAULT_TENSOR_MAP)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--lanes", type=int, default=DEFAULT_LANES)
    parser.add_argument("--q8-block-size", type=int, default=DEFAULT_Q8_BLOCK_SIZE)
    parser.add_argument("--scale-shift", type=int, default=DEFAULT_SCALE_SHIFT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--lm-head-row-start", type=int, default=DEFAULT_LM_HEAD_ROW_START)
    parser.add_argument("--lm-head-row-count", type=int, default=DEFAULT_LM_HEAD_ROW_COUNT)
    parser.add_argument(
        "--emit-packet",
        action="store_true",
        help="Also write packet_scale_q_weight.bin with scale_q lanes followed by weight payload.",
    )
    return parser.parse_args()


def resolve_tensors_csv(tensor_map_payload: dict[str, Any]) -> Path:
    source = tensor_map_payload.get("source", {})
    inspect_csv = source.get("inspect_csv")
    candidates: list[Path] = []
    if inspect_csv:
        candidates.append(Path(str(inspect_csv)).expanduser())
    candidates.extend(
        [
            PROJECT_ROOT / "pycharm" / "reports" / "gguf_inspect" / "tensors.csv",
            PROJECT_ROOT / "old" / "reports" / "gguf_inspect" / "tensors.csv",
        ]
    )

    for candidate in candidates:
        if candidate.is_absolute() and candidate.exists():
            return candidate.resolve()
        resolved = resolve_existing_path(candidate)
        if resolved.exists():
            return resolved
    raise FileNotFoundError("could not resolve tensors.csv from tensor map source")


def find_entry(entries: list[MapEntry], internal_name: str) -> MapEntry:
    for entry in entries:
        if entry.internal_name == internal_name:
            return entry
    available = ", ".join(item.internal_name for item in entries[:8])
    raise KeyError(f"tensor map missing {internal_name!r}; first entries: {available}")


def q8_shape_from_entry(entry: MapEntry) -> tuple[int, int]:
    cols = entry.shape[0]
    rows = int(np.prod(entry.shape[1:], dtype=np.int64))
    return rows, cols


def build_cases(
    entries: list[MapEntry],
    *,
    lm_head_row_start: int,
    lm_head_row_count: int,
) -> list[GoldenCase]:
    q_proj = find_entry(entries, "layer_0_q_proj")
    gate_proj = find_entry(entries, "layer_0_gate_proj")
    lm_head_source = find_entry(entries, "tok_embeddings")

    q_rows, q_cols = q8_shape_from_entry(q_proj)
    gate_rows, gate_cols = q8_shape_from_entry(gate_proj)
    lm_rows, lm_cols = q8_shape_from_entry(lm_head_source)
    if lm_head_row_start < 0 or lm_head_row_count <= 0:
        raise ValueError("lm_head row start/count must select a positive slice")
    if lm_head_row_start + lm_head_row_count > lm_rows:
        raise ValueError("lm_head slice exceeds token embedding rows")

    return [
        GoldenCase(
            case_name="fake_gemv",
            tensor_name="fake_q8_0_hand_calc_weight",
            internal_name="fake_gemv_weight",
            role="synthetic_hand_calculation_and_padding_test",
            source_kind="synthetic_q8_0",
            row_start=0,
            row_count=FAKE_ROWS,
            in_features=FAKE_COLS,
            out_features=FAKE_ROWS,
            source_note="Small synthetic Q8_0-like matrix with one 32-column block and lane padding.",
        ),
        GoldenCase(
            case_name="layer0_q_proj",
            tensor_name=q_proj.original_name,
            internal_name=q_proj.internal_name,
            role=q_proj.role,
            source_kind="gguf_q8_0",
            row_start=0,
            row_count=q_rows,
            in_features=q_cols,
            out_features=q_rows,
            entry=q_proj,
        ),
        GoldenCase(
            case_name="layer0_gate_proj",
            tensor_name=gate_proj.original_name,
            internal_name=gate_proj.internal_name,
            role=gate_proj.role,
            source_kind="gguf_q8_0",
            row_start=0,
            row_count=gate_rows,
            in_features=gate_cols,
            out_features=gate_rows,
            entry=gate_proj,
        ),
        GoldenCase(
            case_name="lm_head_slice",
            tensor_name=lm_head_source.original_name,
            internal_name="lm_head_tied_tok_embeddings_slice",
            role="lm_head_tied_token_embedding_slice",
            source_kind="gguf_q8_0",
            row_start=lm_head_row_start,
            row_count=lm_head_row_count,
            in_features=lm_cols,
            out_features=lm_head_row_count,
            entry=lm_head_source,
            tied_lm_head=True,
            source_note=(
                "This GGUF has no separate output.weight tensor; the lm_head slice is "
                "generated from token_embd.weight as tied embeddings."
            ),
        ),
    ]


def random_input_i16(seed: int, in_features: int, *, fake: bool) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if fake:
        return rng.integers(-4, 5, size=in_features, dtype=np.int16).astype(np.dtype("<i2"))
    return rng.integers(-512, 512, size=in_features, dtype=np.int16).astype(np.dtype("<i2"))


def make_fake_weights_and_scales() -> tuple[np.ndarray, np.ndarray]:
    weights = np.zeros((FAKE_ROWS, FAKE_COLS), dtype=np.int8)
    weights[0, :] = np.arange(1, FAKE_COLS + 1, dtype=np.int16).astype(np.int8)
    weights[1, :] = np.where(np.arange(FAKE_COLS) % 2 == 0, 2, -2).astype(np.int8)
    weights[2, :] = np.array(
        [0, 1, -1, 2, -2, 3, -3, 4] * 4,
        dtype=np.int8,
    )
    # Binary fractions are exactly representable at scale_shift=20, keeping the
    # hand-check case easy to verify.
    scales = np.array([[0.25], [0.5], [0.125]], dtype=np.dtype("<f2"))
    return weights, scales


def dequantize_q8_0(weights_i8: np.ndarray, scales_f16: np.ndarray) -> np.ndarray:
    rows, cols = weights_i8.shape
    blocks = cols // Q8_0_BLOCK_SIZE
    q = weights_i8.reshape(rows, blocks, Q8_0_BLOCK_SIZE).astype(np.float32)
    s = scales_f16.astype(np.float32)[:, :, None]
    return (q * s).reshape(rows, cols)


def compute_block_acc(input_i16: np.ndarray, weights_i8: np.ndarray) -> np.ndarray:
    rows, cols = weights_i8.shape
    if input_i16.shape != (cols,):
        raise ValueError(f"input shape {input_i16.shape} does not match cols={cols}")
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
    return block_acc


def round_shift_signed(values_i64: np.ndarray, shift: int) -> np.ndarray:
    if shift == 0:
        shifted = values_i64
    else:
        rounding = np.int64(1 << (shift - 1))
        positive = values_i64 >= 0
        shifted = np.empty_like(values_i64, dtype=np.int64)
        shifted[positive] = (values_i64[positive] + rounding) >> shift
        shifted[~positive] = -(((-values_i64[~positive]) + rounding) >> shift)

    min_i32 = np.iinfo(np.int32).min
    max_i32 = np.iinfo(np.int32).max
    if int(shifted.min(initial=0)) < min_i32 or int(shifted.max(initial=0)) > max_i32:
        raise OverflowError("scaled fixed-point output exceeds int32 range")
    return shifted.astype(np.dtype("<i4"), copy=False)


def compute_scaled_i32(block_acc_i32: np.ndarray, scale_q_i32: np.ndarray, scale_shift: int) -> np.ndarray:
    scaled_acc_i64 = (
        block_acc_i32.astype(np.int64) * scale_q_i32.astype(np.int64)
    ).sum(axis=1, dtype=np.int64)
    return round_shift_signed(scaled_acc_i64, scale_shift)


def compute_float_output_from_decode(
    case: GoldenCase,
    input_i16: np.ndarray,
    weights_i8: np.ndarray,
    scales_f16: np.ndarray,
    *,
    gguf_path: Path,
    tensors_csv: Path,
) -> np.ndarray:
    if case.source_kind == "synthetic_q8_0":
        decoded = dequantize_q8_0(weights_i8, scales_f16)
    else:
        decoded = decode_tensor_slice(
            case.tensor_name,
            case.row_start,
            case.row_count,
            0,
            case.in_features,
            representation="float",
            gguf_path=gguf_path,
            tensors_csv=tensors_csv,
        )
    return (decoded.astype(np.float32) @ input_i16.astype(np.float32)).astype(np.dtype("<f4"))


def load_real_weights_and_scales(
    gguf_path: Path,
    case: GoldenCase,
) -> tuple[np.ndarray, np.ndarray]:
    if case.entry is None:
        raise ValueError(f"{case.case_name} has no tensor map entry")
    weights, scales, rows, _cols = load_q8_0_quant_and_scales(gguf_path, case.entry)
    if case.row_start + case.row_count > rows:
        raise ValueError(f"{case.case_name} row slice exceeds source rows")
    return (
        weights[case.row_start:case.row_start + case.row_count, :].copy(),
        scales[case.row_start:case.row_start + case.row_count, :].copy(),
    )


def generate_case_arrays(
    case: GoldenCase,
    *,
    gguf_path: Path,
    tensors_csv: Path,
    seed: int,
    scale_shift: int,
) -> CaseArrays:
    input_i16 = random_input_i16(seed, case.in_features, fake=case.source_kind == "synthetic_q8_0")
    if case.source_kind == "synthetic_q8_0":
        weights_i8, scales_f16 = make_fake_weights_and_scales()
    else:
        weights_i8, scales_f16 = load_real_weights_and_scales(gguf_path, case)

    scale_q_i32 = quantize_scales_to_i32(scales_f16, scale_shift)
    block_acc_i32 = compute_block_acc(input_i16, weights_i8)
    output_scaled_i32 = compute_scaled_i32(block_acc_i32, scale_q_i32, scale_shift)
    output_ref_float = compute_float_output_from_decode(
        case,
        input_i16,
        weights_i8,
        scales_f16,
        gguf_path=gguf_path,
        tensors_csv=tensors_csv,
    )

    return CaseArrays(
        input_i16=input_i16,
        weights_i8=weights_i8,
        scales_f16=scales_f16,
        scale_q_i32=scale_q_i32,
        block_acc_i32=block_acc_i32,
        output_scaled_i32=output_scaled_i32,
        output_ref_float=output_ref_float,
    )


def write_raw_binary(path: Path, array: np.ndarray, dtype: str | np.dtype[Any]) -> None:
    array.astype(np.dtype(dtype), copy=False).tofile(path)


def restore_weight_stream(path: Path, rows: int, cols: int, lanes: int, q8_block_size: int) -> np.ndarray:
    padded_rows, _row_padding = pad_rows(rows, lanes)
    groups = padded_rows // lanes
    blocks = cols // q8_block_size
    raw = np.fromfile(path, dtype=np.int8)
    expected = groups * blocks * q8_block_size * lanes
    if raw.size != expected:
        raise ValueError(f"{path} has {raw.size} int8 values, expected {expected}")
    stream = raw.reshape(groups, blocks, q8_block_size, lanes)
    restored = np.empty((padded_rows, cols), dtype=np.int8)
    for group_index in range(groups):
        row_base = group_index * lanes
        for block_index in range(blocks):
            col_base = block_index * q8_block_size
            restored[row_base:row_base + lanes, col_base:col_base + q8_block_size] = stream[
                group_index, block_index
            ].T
    return restored[:rows, :]


def restore_scale_q_stream(path: Path, rows: int, blocks: int, lanes: int) -> np.ndarray:
    padded_rows, _row_padding = pad_rows(rows, lanes)
    groups = padded_rows // lanes
    raw = np.fromfile(path, dtype=np.dtype("<i4"))
    expected = groups * blocks * lanes
    if raw.size != expected:
        raise ValueError(f"{path} has {raw.size} int32 values, expected {expected}")
    stream = raw.reshape(groups, blocks, lanes)
    restored = np.empty((padded_rows, blocks), dtype=np.dtype("<i4"))
    for group_index in range(groups):
        row_base = group_index * lanes
        restored[row_base:row_base + lanes, :] = stream[group_index].T
    return restored[:rows, :]


def write_stream_order(path: Path, *, lanes: int, q8_block_size: int, scale_shift: int, emit_packet: bool) -> None:
    lines = [
        "FPGA GEMV golden stream order",
        "",
        "scale_q_i32.bin:",
        "  dtype: little-endian int32",
        f"  scale_q formula: round(scale * 2^{scale_shift})",
        "  order:",
        "    for row_group in range(0, padded_out_features, lanes):",
        "      for q8_block in range(in_features // q8_block_size):",
        "        for lane in range(lanes):",
        "          write scale_q[row_group + lane, q8_block]",
        "",
        "weight_q8_fpga_layout.bin:",
        "  dtype: int8",
        "  order:",
        "    for row_group in range(0, padded_out_features, lanes):",
        "      for q8_block in range(in_features // q8_block_size):",
        "        for column_in_block in range(q8_block_size):",
        "          for lane in range(lanes):",
        "            write weight[row_group + lane, q8_block*q8_block_size + column_in_block]",
        "",
        "padding:",
        f"  lanes: {lanes}",
        f"  q8_block_size: {q8_block_size}",
        "  rows >= out_features are zero-padded in weight and scale_q streams.",
    ]
    if emit_packet:
        lines.extend(
            [
                "",
                "packet_scale_q_weight.bin:",
                "  dtype: mixed little-endian int32 scale header then int8 weights",
                "  order per row_group/q8_block:",
                "    scale_q lanes first",
                "    then q8_block_size * lanes int8 payload values",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_fake_hand_calc(path: Path, arrays: CaseArrays, scale_shift: int) -> None:
    payload = {
        "note": "Small fake_gemv vectors for manual checking.",
        "scale_shift": scale_shift,
        "input_i16": arrays.input_i16.astype(int).tolist(),
        "weights_i8": arrays.weights_i8.astype(int).tolist(),
        "scale_f16_as_float": arrays.scales_f16.astype(np.float32).tolist(),
        "scale_q_i32": arrays.scale_q_i32.astype(int).tolist(),
        "block_acc_i32": arrays.block_acc_i32.astype(int).tolist(),
        "output_scaled_ref_i32": arrays.output_scaled_i32.astype(int).tolist(),
        "output_ref_float": arrays.output_ref_float.astype(float).tolist(),
        "formulas": {
            "block_acc": "sum(input_i16[k] * weight_i8[row][k]) for k in the 32-column block",
            "scaled_i32": "round_away_from_zero(sum(block_acc * scale_q) / 2^scale_shift)",
            "float": "sum(input_i16[k] * weight_i8[row][k] * scale_f16[row][block])",
        },
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_case(
    case: GoldenCase,
    arrays: CaseArrays,
    *,
    out_root: Path,
    gguf_path: Path,
    tensor_map_path: Path,
    tensors_csv_path: Path,
    seed: int,
    lanes: int,
    q8_block_size: int,
    scale_shift: int,
    emit_packet: bool,
) -> dict[str, Any]:
    case_dir = out_root / case.case_name
    case_dir.mkdir(parents=True, exist_ok=True)

    rows, cols = arrays.weights_i8.shape
    blocks = cols // q8_block_size
    padded_rows, row_padding = pad_rows(rows, lanes)

    input_file = case_dir / "input_i16.bin"
    weight_file = case_dir / "weight_q8_fpga_layout.bin"
    scale_q_file = case_dir / "scale_q_i32.bin"
    packet_file = case_dir / "packet_scale_q_weight.bin"
    block_acc_file = case_dir / "output_block_acc_ref_i32.bin"
    scaled_i32_file = case_dir / "output_scaled_ref_i32.bin"
    output_float_file = case_dir / "output_ref_float.bin"
    stream_order_file = case_dir / "stream_order.txt"
    manifest_file = case_dir / "manifest.json"
    fake_hand_calc_file = case_dir / "fake_hand_calculation.json"

    write_raw_binary(input_file, arrays.input_i16, "<i2")
    weight_bytes = write_weight_lane_layout(
        weight_file,
        arrays.weights_i8,
        lanes=lanes,
        q8_block_size=q8_block_size,
        padded_rows=padded_rows,
    )
    scale_q_bytes = write_scale_q_lane_layout(
        scale_q_file,
        arrays.scale_q_i32,
        lanes=lanes,
        padded_rows=padded_rows,
    )
    packet_bytes = 0
    if emit_packet:
        packet_bytes = write_combined_packet_layout(
            packet_file,
            arrays.weights_i8,
            arrays.scale_q_i32,
            lanes=lanes,
            q8_block_size=q8_block_size,
            padded_rows=padded_rows,
        )
    write_raw_binary(block_acc_file, arrays.block_acc_i32, "<i4")
    write_raw_binary(scaled_i32_file, arrays.output_scaled_i32, "<i4")
    write_raw_binary(output_float_file, arrays.output_ref_float, "<f4")
    write_stream_order(
        stream_order_file,
        lanes=lanes,
        q8_block_size=q8_block_size,
        scale_shift=scale_shift,
        emit_packet=emit_packet,
    )
    if case.source_kind == "synthetic_q8_0":
        write_fake_hand_calc(fake_hand_calc_file, arrays, scale_shift)

    restored_weights = restore_weight_stream(weight_file, rows, cols, lanes, q8_block_size)
    restored_scale_q = restore_scale_q_stream(scale_q_file, rows, blocks, lanes)
    restored_block_acc = compute_block_acc(arrays.input_i16, restored_weights)
    restored_scaled_i32 = compute_scaled_i32(restored_block_acc, restored_scale_q, scale_shift)

    weight_exact = bool(np.array_equal(arrays.weights_i8, restored_weights))
    scale_q_exact = bool(np.array_equal(arrays.scale_q_i32, restored_scale_q))
    block_acc_exact = bool(np.array_equal(arrays.block_acc_i32, restored_block_acc))
    scaled_i32_exact = bool(np.array_equal(arrays.output_scaled_i32, restored_scaled_i32))
    fixed_float = (
        (arrays.block_acc_i32.astype(np.int64) * arrays.scale_q_i32.astype(np.int64)).sum(axis=1)
        / float(1 << scale_shift)
    ).astype(np.float32)
    fixed_vs_float_abs = np.abs(fixed_float - arrays.output_ref_float.astype(np.float32))

    files = {
        "input_i16": input_file.name,
        "weight_q8_fpga_layout": weight_file.name,
        "scale_q_i32": scale_q_file.name,
        "packet_scale_q_weight": packet_file.name if emit_packet else None,
        "output_block_acc_ref_i32": block_acc_file.name,
        "output_scaled_ref_i32": scaled_i32_file.name,
        "output_ref_float": output_float_file.name,
        "stream_order": stream_order_file.name,
        "fake_hand_calculation": fake_hand_calc_file.name if case.source_kind == "synthetic_q8_0" else None,
    }

    manifest = {
        "schema_version": 1,
        "case_name": case.case_name,
        "source_kind": case.source_kind,
        "source_note": case.source_note,
        "source": {
            "gguf": project_relative(gguf_path) if case.source_kind != "synthetic_q8_0" else None,
            "tensor_map": project_relative(tensor_map_path) if case.source_kind != "synthetic_q8_0" else None,
            "tensors_csv": project_relative(tensors_csv_path) if case.source_kind != "synthetic_q8_0" else None,
            "tensor_name": case.tensor_name,
            "internal_name": case.internal_name,
            "role": case.role,
            "row_start": case.row_start,
            "row_count": case.row_count,
            "tied_lm_head": case.tied_lm_head,
        },
        "random": {
            "seed": seed,
            "input_range_inclusive": [-4, 4] if case.source_kind == "synthetic_q8_0" else [-512, 511],
        },
        "geometry": {
            "lanes": lanes,
            "q8_block_size": q8_block_size,
            "scale_shift": scale_shift,
            "scale_dtype": "int32_le",
            "in_features": cols,
            "out_features": rows,
            "padded_out_features": padded_rows,
            "row_padding": row_padding,
            "q8_blocks_per_row": blocks,
        },
        "layout": {
            "name": LAYOUT_NAME,
            "scale_q_order": "row_group, q8_0_block_index, lane",
            "weight_order": "row_group, q8_0_block_index, column_in_block, lane",
            "packet_order": "row_group, q8_0_block_index, scale_q_lanes, weight_columns_lanes",
        },
        "files": files,
        "binary_format": {
            "input_i16": "little-endian int16 vector [in_features]",
            "weight_q8_fpga_layout": "int8 stream ordered row_group, q8_0_block_index, column_in_block, lane",
            "scale_q_i32": "little-endian int32 stream ordered row_group, q8_0_block_index, lane",
            "packet_scale_q_weight": "optional mixed packet: int32 scale_q lanes then int8 weight block payload",
            "output_block_acc_ref_i32": "little-endian int32 matrix [out_features, q8_blocks_per_row]",
            "output_scaled_ref_i32": "little-endian int32 vector [out_features], signed rounded fixed-point scaled result",
            "output_ref_float": "little-endian float32 vector [out_features], Q8_0 decode reference",
        },
        "formulas": {
            "scale_q": "round(scale_f16 * 2^scale_shift)",
            "output_block_acc_ref_i32[row][block]": "sum_k(input_i16[block*32+k] * weight_i8[row][block*32+k])",
            "output_scaled_ref_i32[row]": "round_away_from_zero(sum_block(block_acc_i32[row][block] * scale_q[row][block]) / 2^scale_shift)",
            "output_ref_float[row]": "sum_k(input_i16[k] * q8_0_decode_float(weight[row][k]))",
        },
        "verification": {
            "weight_stream_exact_match": weight_exact,
            "scale_q_stream_exact_match": scale_q_exact,
            "block_acc_i32_exact_match_after_stream_restore": block_acc_exact,
            "scaled_i32_exact_match_after_stream_restore": scaled_i32_exact,
            "fixed_vs_float_max_abs_error": float(fixed_vs_float_abs.max(initial=0.0)),
            "fixed_vs_float_mean_abs_error": float(fixed_vs_float_abs.mean() if fixed_vs_float_abs.size else 0.0),
        },
        "sizes": {
            "input_bytes": input_file.stat().st_size,
            "weight_bytes": weight_bytes,
            "scale_q_bytes": scale_q_bytes,
            "packet_bytes": packet_bytes,
            "block_acc_bytes": block_acc_file.stat().st_size,
            "scaled_i32_bytes": scaled_i32_file.stat().st_size,
            "output_float_bytes": output_float_file.stat().st_size,
        },
    }
    manifest_file.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def write_root_report(out_root: Path, manifests: list[dict[str, Any]]) -> None:
    lines = [
        "GGUF Q8_0 fixed-scale GEMV golden generation report",
        f"cases: {len(manifests)}",
        "",
    ]
    for manifest in manifests:
        geometry = manifest["geometry"]
        verification = manifest["verification"]
        lines.extend(
            [
                f"[{manifest['case_name']}]",
                f"source: {manifest['source']['tensor_name']}",
                f"shape: out={geometry['out_features']} in={geometry['in_features']} "
                f"padded_out={geometry['padded_out_features']} lanes={geometry['lanes']} "
                f"blocks={geometry['q8_blocks_per_row']}",
                f"weight exact: {verification['weight_stream_exact_match']}",
                f"scale_q exact: {verification['scale_q_stream_exact_match']}",
                f"block_acc exact: {verification['block_acc_i32_exact_match_after_stream_restore']}",
                f"scaled_i32 exact: {verification['scaled_i32_exact_match_after_stream_restore']}",
                f"fixed vs float max error: {verification['fixed_vs_float_max_abs_error']}",
                "",
            ]
        )
    (out_root / "generation_report.txt").write_text("\n".join(lines), encoding="utf-8")
    (out_root / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "cases": manifests}, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()

    try:
        require_options(args.lanes, args.q8_block_size, args.scale_shift, None)
        gguf_path = resolve_existing_path(args.gguf)
        tensor_map_path = resolve_existing_path(args.tensor_map)
        out_root = resolve_output_path(args.out)
        out_root.mkdir(parents=True, exist_ok=True)

        tensor_map_payload, all_entries = load_tensor_map(tensor_map_path)
        entries = filter_entries(all_entries, [], None)
        tensors_csv_path = resolve_tensors_csv(tensor_map_payload)
        cases = build_cases(
            entries,
            lm_head_row_start=args.lm_head_row_start,
            lm_head_row_count=args.lm_head_row_count,
        )

        manifests: list[dict[str, Any]] = []
        for case_index, case in enumerate(cases):
            case_seed = args.seed + case_index * 1009
            arrays = generate_case_arrays(
                case,
                gguf_path=gguf_path,
                tensors_csv=tensors_csv_path,
                seed=case_seed,
                scale_shift=args.scale_shift,
            )
            manifest = write_case(
                case,
                arrays,
                out_root=out_root,
                gguf_path=gguf_path,
                tensor_map_path=tensor_map_path,
                tensors_csv_path=tensors_csv_path,
                seed=case_seed,
                lanes=args.lanes,
                q8_block_size=args.q8_block_size,
                scale_shift=args.scale_shift,
                emit_packet=args.emit_packet,
            )
            manifests.append(manifest)
            print(
                f"[OK] {case.case_name}: out={case.out_features} in={case.in_features} "
                f"dir={out_root / case.case_name}"
            )

        write_root_report(out_root, manifests)
    except Exception as exc:
        print(f"[FAIL] golden generation failed: {exc}", file=sys.stderr)
        return 1

    print(f"golden root: {out_root}")
    print(f"report: {out_root / 'generation_report.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
