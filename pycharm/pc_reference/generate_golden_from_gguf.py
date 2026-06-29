"""Generate FPGA GEMV golden vectors from a quantized GGUF Q8_0 model.

Outputs are little-endian raw binaries so C and Verilog testbenches can read
the same files without Python-specific serialization.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYCHARM_ROOT = PROJECT_ROOT / "pycharm"
PYCHARM_REFERENCE_ROOT = PYCHARM_ROOT / "pc_reference"
if str(PYCHARM_REFERENCE_ROOT) not in sys.path:
    sys.path.insert(0, str(PYCHARM_REFERENCE_ROOT))

from q8_0_decode_ref import (  # noqa: E402
    Q8_0_BLOCK_NUM_BYTES,
    Q8_0_BLOCK_SIZE,
    Q8_0_SCALE_NUM_BYTES,
    TensorMeta,
    decode_tensor_slice,
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
DEFAULT_TENSOR_MAP = PYCHARM_ROOT / "fpga_layout" / "tensor_map.json"
DEFAULT_TENSORS_CSV = PYCHARM_ROOT / "reports" / "gguf_inspect" / "tensors.csv"
DEFAULT_OUT = PROJECT_ROOT / "golden"
DEFAULT_LANES = 16
DEFAULT_SEED = 20260625

FAKE_ROWS = 5
FAKE_COLS = 64
LM_HEAD_SLICE_ROWS = 32


@dataclass(frozen=True)
class TensorMapEntry:
    original_name: str
    internal_name: str
    shape: tuple[int, ...]
    role: str
    dtype_or_quant_type: str
    offset: int
    nbytes: int


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
    tied_lm_head: bool = False
    source_note: str = ""


@dataclass(frozen=True)
class CaseArrays:
    input_i16: np.ndarray
    weights_i8: np.ndarray
    scales_f16: np.ndarray
    block_acc_i32: np.ndarray
    output_ref_i32: np.ndarray
    output_ref_float: np.ndarray


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate FPGA GEMV golden vectors from GGUF Q8_0 tensors."
    )
    parser.add_argument("--gguf", type=Path, default=DEFAULT_GGUF)
    parser.add_argument("--tensor-map", type=Path, default=DEFAULT_TENSOR_MAP)
    parser.add_argument("--tensors-csv", type=Path, default=DEFAULT_TENSORS_CSV)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--lanes", type=int, default=DEFAULT_LANES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--lm-head-row-start", type=int, default=0)
    parser.add_argument("--lm-head-row-count", type=int, default=LM_HEAD_SLICE_ROWS)
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


def require_lanes(lanes: int) -> None:
    if lanes <= 0:
        raise ValueError(f"--lanes must be positive, got {lanes}")


def pad_rows(rows: int, lanes: int) -> tuple[int, int]:
    padded = ((rows + lanes - 1) // lanes) * lanes
    return padded, padded - rows


def load_tensor_map(path: Path) -> list[TensorMapEntry]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = []
    for item in payload.get("mappings", []):
        if item.get("dtype_or_quant_type") != "Q8_0":
            continue
        if not item.get("used", False):
            continue
        if item.get("internal_name") in (None, "", "unknown"):
            continue

        entries.append(
            TensorMapEntry(
                original_name=str(item["original_name"]),
                internal_name=str(item["internal_name"]),
                shape=tuple(int(value) for value in item["shape"]),
                role=str(item["role"]),
                dtype_or_quant_type=str(item["dtype_or_quant_type"]),
                offset=int(item["offset"]),
                nbytes=int(item["nbytes"]),
            )
        )
    return entries


def find_entry(entries: list[TensorMapEntry], internal_name: str) -> TensorMapEntry:
    for entry in entries:
        if entry.internal_name == internal_name:
            return entry
    raise KeyError(f"tensor_map does not contain internal_name={internal_name!r}")


def tensor_meta(entry: TensorMapEntry) -> TensorMeta:
    return TensorMeta(
        tensor_name=entry.original_name,
        gguf_shape=entry.shape,
        dtype_or_quant_type=entry.dtype_or_quant_type,
        offset=entry.offset,
        nbytes=entry.nbytes,
    )


def make_real_case(
    case_name: str,
    entry: TensorMapEntry,
    *,
    row_start: int,
    row_count: int | None,
    source_note: str = "",
    tied_lm_head: bool = False,
) -> GoldenCase:
    layout = matrix_layout_from_gguf_shape(tensor_meta(entry))
    actual_row_count = layout.rows - row_start if row_count is None else row_count
    if row_start < 0 or actual_row_count <= 0:
        raise ValueError(f"invalid row slice for {case_name}: {row_start}, {actual_row_count}")
    if row_start + actual_row_count > layout.rows:
        raise ValueError(
            f"{case_name} row slice [{row_start}, {row_start + actual_row_count}) "
            f"exceeds rows={layout.rows}"
        )

    return GoldenCase(
        case_name=case_name,
        tensor_name=entry.original_name,
        internal_name=entry.internal_name,
        role=entry.role,
        source_kind="gguf_q8_0",
        row_start=row_start,
        row_count=actual_row_count,
        in_features=layout.cols,
        out_features=actual_row_count,
        tied_lm_head=tied_lm_head,
        source_note=source_note,
    )


def build_cases(
    entries: list[TensorMapEntry],
    *,
    lm_head_row_start: int,
    lm_head_row_count: int,
) -> list[GoldenCase]:
    q_proj = find_entry(entries, "layer_0_q_proj")
    gate_proj = find_entry(entries, "layer_0_gate_proj")

    # This GGUF has no separate output.weight/lm_head tensor. SmolLM-style
    # tied embeddings use token_embd.weight as the LM head matrix, so this
    # slice is generated from token_embd.weight and marked as tied_lm_head.
    lm_head_source = find_entry(entries, "tok_embeddings")

    return [
        GoldenCase(
            case_name="fake_gemv",
            tensor_name="fake_q8_0_weight",
            internal_name="fake_gemv_weight",
            role="synthetic_padding_and_stream_order_test",
            source_kind="synthetic_q8_0",
            row_start=0,
            row_count=FAKE_ROWS,
            in_features=FAKE_COLS,
            out_features=FAKE_ROWS,
            source_note="Synthetic Q8_0-like tensor used to exercise lane padding.",
        ),
        make_real_case("layer0_q_proj", q_proj, row_start=0, row_count=None),
        make_real_case("layer0_gate_proj", gate_proj, row_start=0, row_count=None),
        make_real_case(
            "lm_head_slice",
            lm_head_source,
            row_start=lm_head_row_start,
            row_count=lm_head_row_count,
            tied_lm_head=True,
            source_note=(
                "No separate output.weight exists in this GGUF; using "
                "token_embd.weight rows as tied lm_head rows."
            ),
        ),
    ]


def random_input_i16(seed: int, in_features: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    # Keep values modest so int32 reference accumulation is comfortably within
    # range while still exercising signs and non-trivial products.
    return rng.integers(-512, 512, size=in_features, dtype=np.int16).astype(np.dtype("<i2"))


def load_real_weights_and_scales(
    gguf_path: Path,
    entry: TensorMapEntry,
    row_start: int,
    row_count: int,
) -> tuple[np.ndarray, np.ndarray]:
    meta = tensor_meta(entry)
    layout = matrix_layout_from_gguf_shape(meta)
    endian_info = detect_gguf_endian(gguf_path)
    if row_start + row_count > layout.rows:
        raise ValueError(f"{entry.original_name} row slice exceeds tensor rows")

    raw = np.memmap(
        gguf_path,
        mode="r",
        dtype=np.uint8,
        offset=entry.offset,
        shape=(layout.rows, layout.blocks_per_row, Q8_0_BLOCK_NUM_BYTES),
    )
    raw_slice = raw[row_start:row_start + row_count, :, :]

    weights = np.asarray(raw_slice[:, :, Q8_0_SCALE_NUM_BYTES:], dtype=np.uint8)
    weights_i8 = weights.view(np.int8).reshape(row_count, layout.cols).copy()

    scale_dtype = scale_dtype_for_endian(endian_info)
    scale_bytes = np.asarray(raw_slice[:, :, :Q8_0_SCALE_NUM_BYTES], dtype=np.uint8)
    scales = scale_bytes.reshape(row_count * layout.blocks_per_row, Q8_0_SCALE_NUM_BYTES)
    scales_f16 = scales.copy().view(scale_dtype).reshape(row_count, layout.blocks_per_row)
    scales_f16 = scales_f16.astype(np.dtype("<f2"), copy=False)

    return weights_i8, scales_f16


def make_fake_weights_and_scales(seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    weights = rng.integers(-127, 128, size=(FAKE_ROWS, FAKE_COLS), dtype=np.int16).astype(np.int8)
    scales = rng.uniform(0.002, 0.05, size=(FAKE_ROWS, FAKE_COLS // Q8_0_BLOCK_SIZE))
    return weights, scales.astype(np.dtype("<f2"))


def dequantize_q8_0(weights_i8: np.ndarray, scales_f16: np.ndarray) -> np.ndarray:
    rows, cols = weights_i8.shape
    blocks = cols // Q8_0_BLOCK_SIZE
    q = weights_i8.reshape(rows, blocks, Q8_0_BLOCK_SIZE).astype(np.float32)
    s = scales_f16.astype(np.float32)[:, :, None]
    return (q * s).reshape(rows, cols)


def compute_outputs(
    input_i16: np.ndarray,
    weights_i8: np.ndarray,
    scales_f16: np.ndarray,
    *,
    decoded_float_matrix: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows, cols = weights_i8.shape
    if input_i16.shape != (cols,):
        raise ValueError(f"input shape {input_i16.shape} does not match cols={cols}")
    if cols % Q8_0_BLOCK_SIZE != 0:
        raise ValueError(f"cols={cols} must be divisible by Q8_0 block size")

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

    if decoded_float_matrix is None:
        decoded_float_matrix = dequantize_q8_0(weights_i8, scales_f16)
    output_float = (
        decoded_float_matrix.astype(np.float32) @ input_i16.astype(np.float32)
    ).astype(np.dtype("<f4"))

    return block_acc, output_i32, output_float


def write_weight_stream(path: Path, weights_i8: np.ndarray, lanes: int) -> int:
    rows, cols = weights_i8.shape
    padded_rows, _row_padding = pad_rows(rows, lanes)
    zero_group = np.zeros((lanes, cols), dtype=np.int8)
    bytes_written = 0

    with path.open("wb") as file_obj:
        for row_group in range(0, padded_rows, lanes):
            group = zero_group.copy()
            valid_rows = max(0, min(lanes, rows - row_group))
            if valid_rows:
                group[:valid_rows, :] = weights_i8[row_group:row_group + valid_rows, :]
            stream_chunk = np.ascontiguousarray(group.T)
            file_obj.write(stream_chunk.tobytes())
            bytes_written += stream_chunk.nbytes

    return bytes_written


def write_scale_stream(path: Path, scales_f16: np.ndarray, lanes: int) -> int:
    rows, blocks = scales_f16.shape
    padded_rows, _row_padding = pad_rows(rows, lanes)
    zero_group = np.zeros((lanes, blocks), dtype=np.dtype("<f2"))
    bytes_written = 0

    with path.open("wb") as file_obj:
        for row_group in range(0, padded_rows, lanes):
            group = zero_group.copy()
            valid_rows = max(0, min(lanes, rows - row_group))
            if valid_rows:
                group[:valid_rows, :] = scales_f16[row_group:row_group + valid_rows, :]
            stream_chunk = np.ascontiguousarray(group.T)
            file_obj.write(stream_chunk.tobytes())
            bytes_written += stream_chunk.nbytes

    return bytes_written


def restore_weight_stream(path: Path, rows: int, cols: int, lanes: int) -> np.ndarray:
    padded_rows, _padding = pad_rows(rows, lanes)
    groups = padded_rows // lanes
    raw = np.fromfile(path, dtype=np.int8)
    expected = groups * cols * lanes
    if raw.size != expected:
        raise ValueError(f"{path} has {raw.size} int8 values, expected {expected}")

    stream = raw.reshape(groups, cols, lanes)
    restored = np.empty((padded_rows, cols), dtype=np.int8)
    for group_index in range(groups):
        row_base = group_index * lanes
        restored[row_base:row_base + lanes, :] = stream[group_index].T
    return restored[:rows, :]


def restore_scale_stream(path: Path, rows: int, blocks: int, lanes: int) -> np.ndarray:
    padded_rows, _padding = pad_rows(rows, lanes)
    groups = padded_rows // lanes
    raw = np.fromfile(path, dtype=np.dtype("<f2"))
    expected = groups * blocks * lanes
    if raw.size != expected:
        raise ValueError(f"{path} has {raw.size} fp16 values, expected {expected}")

    stream = raw.reshape(groups, blocks, lanes)
    restored = np.empty((padded_rows, blocks), dtype=np.dtype("<f2"))
    for group_index in range(groups):
        row_base = group_index * lanes
        restored[row_base:row_base + lanes, :] = stream[group_index].T
    return restored[:rows, :]


def write_raw_binary(path: Path, array: np.ndarray, dtype: str | np.dtype[Any]) -> None:
    array.astype(np.dtype(dtype), copy=False).tofile(path)


def write_stream_order(path: Path, lanes: int) -> None:
    path.write_text(
        "\n".join(
            [
                "FPGA GEMV golden stream order",
                "",
                "weight_q8_fpga_layout.bin:",
                "  dtype: int8",
                "  order:",
                "    for row_group in range(0, padded_out_features, lanes):",
                "      for col in range(in_features):",
                "        for lane in range(lanes):",
                "          write weight[row_group + lane, col]",
                "",
                "scale.bin:",
                "  dtype: little-endian float16",
                "  order:",
                "    for row_group in range(0, padded_out_features, lanes):",
                "      for q8_block in range(in_features // 32):",
                "        for lane in range(lanes):",
                "          write scale[row_group + lane, q8_block]",
                "",
                "padding:",
                f"  lanes: {lanes}",
                "  rows >= out_features are zero-padded in weight and scale streams.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def generate_case_arrays(
    case: GoldenCase,
    *,
    gguf_path: Path,
    tensors_csv: Path,
    entries_by_internal_name: dict[str, TensorMapEntry],
    seed: int,
) -> CaseArrays:
    input_i16 = random_input_i16(seed, case.in_features)

    if case.source_kind == "synthetic_q8_0":
        weights_i8, scales_f16 = make_fake_weights_and_scales(seed + 1000)
        decoded_float = dequantize_q8_0(weights_i8, scales_f16)
    else:
        entry = entries_by_internal_name[case.internal_name]
        weights_i8, scales_f16 = load_real_weights_and_scales(
            gguf_path,
            entry,
            case.row_start,
            case.row_count,
        )
        decoded_float = decode_tensor_slice(
            entry.original_name,
            case.row_start,
            case.row_count,
            0,
            case.in_features,
            representation="float",
            gguf_path=gguf_path,
            tensors_csv=tensors_csv,
        )

        decoded_i8 = decode_tensor_slice(
            entry.original_name,
            case.row_start,
            case.row_count,
            0,
            case.in_features,
            representation="int8",
            gguf_path=gguf_path,
            tensors_csv=tensors_csv,
        )
        if not np.array_equal(weights_i8, decoded_i8):
            raise AssertionError(f"{case.case_name}: int8 decode reference mismatch")

    block_acc, output_i32, output_float = compute_outputs(
        input_i16,
        weights_i8,
        scales_f16,
        decoded_float_matrix=decoded_float,
    )
    return CaseArrays(
        input_i16=input_i16,
        weights_i8=weights_i8,
        scales_f16=scales_f16,
        block_acc_i32=block_acc,
        output_ref_i32=output_i32,
        output_ref_float=output_float,
    )


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
) -> dict[str, Any]:
    case_dir = out_root / case.case_name
    case_dir.mkdir(parents=True, exist_ok=True)

    rows, cols = arrays.weights_i8.shape
    blocks = cols // Q8_0_BLOCK_SIZE
    padded_rows, row_padding = pad_rows(rows, lanes)

    input_file = case_dir / "input_i16.bin"
    weight_file = case_dir / "weight_q8_fpga_layout.bin"
    scale_file = case_dir / "scale.bin"
    block_acc_file = case_dir / "block_acc_i32.bin"
    output_i32_file = case_dir / "output_ref_i32.bin"
    output_float_file = case_dir / "output_ref_float.bin"
    scale_meta_file = case_dir / "scale_meta.json"
    stream_order_file = case_dir / "stream_order.txt"
    manifest_file = case_dir / "manifest.json"

    write_raw_binary(input_file, arrays.input_i16, "<i2")
    weight_bytes = write_weight_stream(weight_file, arrays.weights_i8, lanes)
    scale_bytes = write_scale_stream(scale_file, arrays.scales_f16, lanes)
    write_raw_binary(block_acc_file, arrays.block_acc_i32, "<i4")
    write_raw_binary(output_i32_file, arrays.output_ref_i32, "<i4")
    write_raw_binary(output_float_file, arrays.output_ref_float, "<f4")
    write_stream_order(stream_order_file, lanes)

    restored_weights = restore_weight_stream(weight_file, rows, cols, lanes)
    restored_scales = restore_scale_stream(scale_file, rows, blocks, lanes)
    restored_block_acc, restored_i32, restored_float = compute_outputs(
        arrays.input_i16,
        restored_weights,
        restored_scales,
    )
    weight_exact = bool(np.array_equal(arrays.weights_i8, restored_weights))
    scale_exact = bool(
        np.array_equal(arrays.scales_f16.view(np.uint16), restored_scales.view(np.uint16))
    )
    i32_exact = bool(np.array_equal(arrays.output_ref_i32, restored_i32))
    block_acc_exact = bool(np.array_equal(arrays.block_acc_i32, restored_block_acc))
    max_float_error = float(np.max(np.abs(arrays.output_ref_float - restored_float)))

    scale_meta = {
        "file": scale_file.name,
        "dtype": "float16_le",
        "shape": [padded_rows, blocks],
        "unpadded_shape": [rows, blocks],
        "order": "row_group, q8_0_block_index, lane",
        "q8_0_block_size": Q8_0_BLOCK_SIZE,
        "row_padding": row_padding,
        "bytes": scale_bytes,
    }
    scale_meta_file.write_text(json.dumps(scale_meta, indent=2) + "\n", encoding="utf-8")

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
            "input_range_inclusive": [-512, 511],
        },
        "geometry": {
            "lanes": lanes,
            "in_features": cols,
            "out_features": rows,
            "padded_out_features": padded_rows,
            "row_padding": row_padding,
            "q8_0_block_size": Q8_0_BLOCK_SIZE,
            "q8_0_blocks_per_row": blocks,
        },
        "files": {
            "input_i16": input_file.name,
            "weight_q8_fpga_layout": weight_file.name,
            "scale": scale_file.name,
            "scale_meta": scale_meta_file.name,
            "block_acc_i32": block_acc_file.name,
            "output_ref_i32": output_i32_file.name,
            "output_ref_float": output_float_file.name,
            "stream_order": stream_order_file.name,
        },
        "binary_format": {
            "input_i16": "little-endian int16 vector, length=in_features",
            "weight_q8_fpga_layout": "int8 stream ordered row_group, col, lane",
            "scale": "little-endian float16 stream ordered row_group, q8_0_block_index, lane",
            "block_acc_i32": "little-endian int32 matrix [out_features, q8_0_blocks_per_row]",
            "output_ref_i32": "little-endian int32 vector, unscaled sum(input_i16 * weight_i8)",
            "output_ref_float": "little-endian float32 vector, sum(block_acc_i32 * fp16_scale)",
        },
        "verification": {
            "weight_stream_exact_match": weight_exact,
            "scale_stream_exact_match": scale_exact,
            "block_acc_i32_exact_match": block_acc_exact,
            "output_ref_i32_exact_match": i32_exact,
            "output_ref_float_max_abs_error_after_stream_restore": max_float_error,
        },
        "sizes": {
            "input_bytes": input_file.stat().st_size,
            "weight_bytes": weight_bytes,
            "scale_bytes": scale_bytes,
            "block_acc_bytes": block_acc_file.stat().st_size,
            "output_i32_bytes": output_i32_file.stat().st_size,
            "output_float_bytes": output_float_file.stat().st_size,
        },
    }
    manifest_file.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def write_root_report(out_root: Path, manifests: list[dict[str, Any]]) -> None:
    lines = [
        "GGUF Q8_0 GEMV golden generation report",
        f"cases: {len(manifests)}",
        "",
    ]
    for manifest in manifests:
        verification = manifest["verification"]
        geometry = manifest["geometry"]
        lines.extend(
            [
                f"[{manifest['case_name']}]",
                f"source: {manifest['source']['tensor_name']}",
                f"shape: out={geometry['out_features']} in={geometry['in_features']} "
                f"padded_out={geometry['padded_out_features']} lanes={geometry['lanes']}",
                f"weight exact: {verification['weight_stream_exact_match']}",
                f"scale exact: {verification['scale_stream_exact_match']}",
                f"output i32 exact: {verification['output_ref_i32_exact_match']}",
                "output float restore max error: "
                f"{verification['output_ref_float_max_abs_error_after_stream_restore']}",
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
        require_lanes(args.lanes)
        gguf_path = resolve_existing_path(args.gguf)
        tensor_map_path = resolve_existing_path(args.tensor_map)
        tensors_csv_path = resolve_existing_path(args.tensors_csv)
        out_root = resolve_output_path(args.out)
        out_root.mkdir(parents=True, exist_ok=True)

        entries = load_tensor_map(tensor_map_path)
        entries_by_internal_name = {entry.internal_name: entry for entry in entries}
        cases = build_cases(
            entries,
            lm_head_row_start=args.lm_head_row_start,
            lm_head_row_count=args.lm_head_row_count,
        )

        manifests = []
        for case_index, case in enumerate(cases):
            case_seed = args.seed + case_index * 1009
            arrays = generate_case_arrays(
                case,
                gguf_path=gguf_path,
                tensors_csv=tensors_csv_path,
                entries_by_internal_name=entries_by_internal_name,
                seed=case_seed,
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
            )
            manifests.append(manifest)
            print(
                f"[OK] {case.case_name}: "
                f"out={case.out_features} in={case.in_features} "
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
