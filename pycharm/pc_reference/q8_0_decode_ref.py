"""Reference GGUF Q8_0 block decoder.

This module is intentionally explicit because the FPGA path should match this
layout and offset math. GGUF/GGML tensor shapes use ne[0] as the contiguous
dimension. For 2-D tensors in this project, a CSV shape of [cols, rows] is
decoded as a logical numpy matrix shaped [rows, cols].
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np


PYCHARM_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PYCHARM_ROOT.parent

DEFAULT_GGUF_PATH = (
    PROJECT_ROOT
    / "quantized_model"
    / "original_gguf"
    / "SmolLM2-135M-Instruct-Q8_0.gguf"
)
DEFAULT_TENSORS_CSV = PYCHARM_ROOT / "reports" / "gguf_inspect" / "tensors.csv"

DEFAULT_TENSOR_NAME = "blk.0.ffn_down.weight"

# GGML Q8_0 block layout, exactly as used by llama.cpp/ggml:
#
#   struct block_q8_0 {
#       ggml_half d;        // 2 bytes, IEEE-754 binary16 scale
#       int8_t qs[32];      // 32 signed int8 quantized payload values
#   };
#
# A tensor row is split into consecutive 32-value blocks along the contiguous
# column dimension ne[0]. Value j in a block is dequantized as:
#
#   float_value = float32(scale_fp16) * float32(qs[j])
#
# The int8 payload is endian-independent. The 16-bit scale follows the GGUF
# file byte order. Current project files are little-endian GGUF v3.
Q8_0_BLOCK_SIZE = 32
Q8_0_SCALE_DTYPE_NAME = "float16"
Q8_0_SCALE_NUM_BYTES = 2
Q8_0_QUANT_DTYPE_NAME = "int8"
Q8_0_QUANT_PAYLOAD_NUM_BYTES = Q8_0_BLOCK_SIZE
Q8_0_BLOCK_NUM_BYTES = Q8_0_SCALE_NUM_BYTES + Q8_0_QUANT_PAYLOAD_NUM_BYTES

SUPPORTED_GGUF_VERSIONS = {1, 2, 3}


Endian = Literal["little", "big"]
Representation = Literal["float", "int8"]


@dataclass(frozen=True)
class TensorMeta:
    tensor_name: str
    gguf_shape: tuple[int, ...]
    dtype_or_quant_type: str
    offset: int
    nbytes: int


@dataclass(frozen=True)
class MatrixLayout:
    rows: int
    cols: int
    blocks_per_row: int
    row_stride_bytes: int


@dataclass(frozen=True)
class GGUFEndianInfo:
    endian: Endian
    numpy_byte_order: Literal["<", ">"]
    version: int


def resolve_path(path: Path | str) -> Path:
    expanded = Path(path).expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (PROJECT_ROOT / expanded).resolve()


def resolve_existing_path(path: Path | str) -> Path:
    expanded = Path(path).expanduser()
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


def detect_gguf_endian(gguf_path: Path) -> GGUFEndianInfo:
    """Read GGUF magic/version and return the file byte order.

    The gguf Python reader checks the magic as little-endian uint32 and detects
    swapped files from the version field. This function keeps that decision
    isolated from the Q8_0 block decoder.
    """

    with gguf_path.open("rb") as file_obj:
        magic = file_obj.read(4)
        version_bytes = file_obj.read(4)

    if magic != b"GGUF":
        raise ValueError(f"invalid GGUF magic bytes: {magic!r}")
    if len(version_bytes) != 4:
        raise ValueError("truncated GGUF header: missing version")

    version_le = int.from_bytes(version_bytes, byteorder="little", signed=False)
    version_be = int.from_bytes(version_bytes, byteorder="big", signed=False)

    if version_le in SUPPORTED_GGUF_VERSIONS:
        return GGUFEndianInfo(endian="little", numpy_byte_order="<", version=version_le)
    if version_be in SUPPORTED_GGUF_VERSIONS:
        return GGUFEndianInfo(endian="big", numpy_byte_order=">", version=version_be)

    raise ValueError(
        "unsupported or corrupt GGUF version bytes: "
        f"little={version_le}, big={version_be}"
    )


def scale_dtype_for_endian(endian_info: GGUFEndianInfo) -> np.dtype[np.float16]:
    return np.dtype(f"{endian_info.numpy_byte_order}f2")


def parse_shape(text: str) -> tuple[int, ...]:
    parsed = json.loads(text)
    if not isinstance(parsed, list) or not all(isinstance(item, int) for item in parsed):
        raise ValueError(f"invalid tensor shape field: {text!r}")
    return tuple(parsed)


def load_tensor_metadata(tensors_csv: Path) -> dict[str, TensorMeta]:
    with tensors_csv.open("r", encoding="utf-8", newline="") as file_obj:
        reader = csv.DictReader(file_obj)
        required = {"tensor_name", "shape", "dtype_or_quant_type", "offset", "nbytes"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"missing CSV columns: {', '.join(sorted(missing))}")

        tensors: dict[str, TensorMeta] = {}
        for row in reader:
            name = row["tensor_name"]
            if not row["offset"] or not row["nbytes"]:
                continue
            tensors[name] = TensorMeta(
                tensor_name=name,
                gguf_shape=parse_shape(row["shape"]),
                dtype_or_quant_type=row["dtype_or_quant_type"],
                offset=int(row["offset"]),
                nbytes=int(row["nbytes"]),
            )

    return tensors


def get_tensor_meta(tensor_name: str, tensors_csv: Path) -> TensorMeta:
    tensors = load_tensor_metadata(tensors_csv)
    try:
        return tensors[tensor_name]
    except KeyError as exc:
        available = ", ".join(sorted(tensors)[:8])
        raise KeyError(
            f"tensor {tensor_name!r} not found in {tensors_csv}; first names: {available}"
        ) from exc


def matrix_layout_from_gguf_shape(meta: TensorMeta) -> MatrixLayout:
    """Convert GGUF shape into logical row/column layout for Q8_0.

    GGUF stores the contiguous dimension first: ne[0]. Quantized block formats
    split that dimension into blocks. For a usual 2-D tensor with shape
    [cols, rows], this returns rows=shape[1] and cols=shape[0]. Higher-rank
    tensors are flattened over ne[1:].
    """

    if meta.dtype_or_quant_type != "Q8_0":
        raise ValueError(
            f"{meta.tensor_name} is {meta.dtype_or_quant_type}, not Q8_0; "
            "this reference decoder handles Q8_0 tensors only"
        )
    if len(meta.gguf_shape) < 2:
        raise ValueError(
            f"{meta.tensor_name} has shape {meta.gguf_shape}; expected at least 2-D Q8_0"
        )

    cols = meta.gguf_shape[0]
    rows = math.prod(meta.gguf_shape[1:])
    if cols % Q8_0_BLOCK_SIZE != 0:
        raise ValueError(
            f"{meta.tensor_name} has cols={cols}, not divisible by Q8_0 block size "
            f"{Q8_0_BLOCK_SIZE}"
        )

    blocks_per_row = cols // Q8_0_BLOCK_SIZE
    row_stride_bytes = blocks_per_row * Q8_0_BLOCK_NUM_BYTES
    expected_nbytes = rows * row_stride_bytes
    if expected_nbytes != meta.nbytes:
        raise ValueError(
            f"{meta.tensor_name} byte size mismatch: metadata nbytes={meta.nbytes}, "
            f"computed Q8_0 bytes={expected_nbytes}"
        )

    return MatrixLayout(
        rows=rows,
        cols=cols,
        blocks_per_row=blocks_per_row,
        row_stride_bytes=row_stride_bytes,
    )


def validate_slice(
    layout: MatrixLayout,
    row_start: int,
    row_count: int,
    col_start: int,
    col_count: int,
) -> None:
    values = {
        "row_start": row_start,
        "row_count": row_count,
        "col_start": col_start,
        "col_count": col_count,
    }
    for name, value in values.items():
        if value < 0:
            raise ValueError(f"{name} must be non-negative, got {value}")
    if row_count == 0 or col_count == 0:
        raise ValueError("row_count and col_count must be positive for a decode slice")
    if row_start + row_count > layout.rows:
        raise ValueError(
            f"row slice [{row_start}, {row_start + row_count}) exceeds rows={layout.rows}"
        )
    if col_start + col_count > layout.cols:
        raise ValueError(
            f"col slice [{col_start}, {col_start + col_count}) exceeds cols={layout.cols}"
        )


def q8_0_block_file_offset(
    tensor_offset: int,
    layout: MatrixLayout,
    row_index: int,
    block_index: int,
) -> int:
    """Return absolute file offset of a Q8_0 block."""

    return (
        tensor_offset
        + row_index * layout.row_stride_bytes
        + block_index * Q8_0_BLOCK_NUM_BYTES
    )


def q8_0_block_range_for_cols(col_start: int, col_count: int) -> range:
    first_block = col_start // Q8_0_BLOCK_SIZE
    last_col_exclusive = col_start + col_count
    last_block = (last_col_exclusive - 1) // Q8_0_BLOCK_SIZE
    return range(first_block, last_block + 1)


def decode_q8_0_block(
    raw_block: bytes,
    endian_info: GGUFEndianInfo,
) -> tuple[np.float32, np.ndarray, np.ndarray]:
    """Decode one 34-byte Q8_0 block.

    Returns (scale, int8_payload, float32_values). The returned int8 payload has
    shape [32], and float32_values equals scale * int8_payload elementwise.
    """

    if len(raw_block) != Q8_0_BLOCK_NUM_BYTES:
        raise ValueError(
            f"Q8_0 block must be {Q8_0_BLOCK_NUM_BYTES} bytes, got {len(raw_block)}"
        )

    scale = np.frombuffer(
        raw_block[:Q8_0_SCALE_NUM_BYTES],
        dtype=scale_dtype_for_endian(endian_info),
        count=1,
    ).astype(np.float32)[0]
    quant = np.frombuffer(
        raw_block[Q8_0_SCALE_NUM_BYTES:],
        dtype=np.int8,
        count=Q8_0_BLOCK_SIZE,
    ).copy()
    values = quant.astype(np.float32) * np.float32(scale)
    return np.float32(scale), quant, values


def read_q8_0_block(
    file_obj: Any,
    block_offset: int,
    endian_info: GGUFEndianInfo,
) -> tuple[np.float32, np.ndarray, np.ndarray]:
    file_obj.seek(block_offset)
    raw = file_obj.read(Q8_0_BLOCK_NUM_BYTES)
    return decode_q8_0_block(raw, endian_info)


def _decode_tensor_slice_impl(
    tensor_name: str,
    row_start: int,
    row_count: int,
    col_start: int,
    col_count: int,
    *,
    representation: Representation,
    gguf_path: Path | str,
    tensors_csv: Path | str,
) -> np.ndarray:
    meta = get_tensor_meta(tensor_name, tensors_csv)
    layout = matrix_layout_from_gguf_shape(meta)
    validate_slice(layout, row_start, row_count, col_start, col_count)

    endian_info = detect_gguf_endian(gguf_path)
    output_dtype = np.float32 if representation == "float" else np.int8
    output = np.empty((row_count, col_count), dtype=output_dtype)
    block_range = q8_0_block_range_for_cols(col_start, col_count)

    with gguf_path.open("rb") as file_obj:
        for out_row, row_index in enumerate(range(row_start, row_start + row_count)):
            for block_index in block_range:
                block_offset = q8_0_block_file_offset(
                    meta.offset,
                    layout,
                    row_index,
                    block_index,
                )
                _scale, quant, values = read_q8_0_block(file_obj, block_offset, endian_info)

                block_col_start = block_index * Q8_0_BLOCK_SIZE
                copy_start = max(col_start, block_col_start)
                copy_end = min(col_start + col_count, block_col_start + Q8_0_BLOCK_SIZE)
                src_start = copy_start - block_col_start
                src_end = copy_end - block_col_start
                dst_start = copy_start - col_start
                dst_end = copy_end - col_start

                if representation == "float":
                    output[out_row, dst_start:dst_end] = values[src_start:src_end]
                elif representation == "int8":
                    output[out_row, dst_start:dst_end] = quant[src_start:src_end]
                else:
                    raise ValueError(f"unsupported representation: {representation}")

    return output


def decode_tensor_slice(
    tensor_name: str,
    row_start: int,
    row_count: int,
    col_start: int,
    col_count: int,
    *,
    representation: Representation = "float",
    gguf_path: Path | str = DEFAULT_GGUF_PATH,
    tensors_csv: Path | str = DEFAULT_TENSORS_CSV,
) -> np.ndarray:
    """Decode a small Q8_0 tensor slice into a numpy array.

    Args:
        tensor_name: GGUF tensor name, e.g. "blk.0.ffn_down.weight".
        row_start/row_count: Logical matrix rows. For a GGUF shape [cols, rows],
            row 0 is the first stored row in the tensor payload.
        col_start/col_count: Logical matrix columns along GGUF ne[0].
        representation: "float" returns dequantized float32 values. "int8"
            returns the signed quant payload values for the same slice.
        gguf_path: GGUF file containing the raw Q8_0 blocks.
        tensors_csv: inspect_gguf.py tensors.csv containing absolute offsets.
    """

    return _decode_tensor_slice_impl(
        tensor_name=tensor_name,
        row_start=row_start,
        row_count=row_count,
        col_start=col_start,
        col_count=col_count,
        representation=representation,
        gguf_path=resolve_existing_path(gguf_path),
        tensors_csv=resolve_existing_path(tensors_csv),
    )


def pack_q8_0_block(
    scale: float,
    quant: np.ndarray,
    endian_info: GGUFEndianInfo,
) -> bytes:
    quant = np.asarray(quant, dtype=np.int8)
    if quant.shape != (Q8_0_BLOCK_SIZE,):
        raise ValueError(f"quant block must have shape ({Q8_0_BLOCK_SIZE},), got {quant.shape}")
    scale_bytes = np.asarray([scale], dtype=scale_dtype_for_endian(endian_info)).tobytes()
    return scale_bytes + quant.tobytes()


def quantize_rows_to_q8_0_ref(values: np.ndarray) -> bytes:
    """Small reference quantizer used only by self-test.

    It emits Q8_0 blocks in the same block layout this decoder consumes. This is
    not used by runtime loading.
    """

    matrix = np.asarray(values, dtype=np.float32)
    if matrix.ndim != 2:
        raise ValueError(f"expected 2-D matrix, got shape {matrix.shape}")
    if matrix.shape[1] % Q8_0_BLOCK_SIZE != 0:
        raise ValueError("matrix column count must be divisible by Q8_0_BLOCK_SIZE")

    endian_info = GGUFEndianInfo(endian="little", numpy_byte_order="<", version=3)
    chunks: list[bytes] = []
    for row in matrix:
        for start in range(0, matrix.shape[1], Q8_0_BLOCK_SIZE):
            block = row[start:start + Q8_0_BLOCK_SIZE]
            max_abs = float(np.max(np.abs(block)))
            scale = np.float32(0.0 if max_abs == 0.0 else max_abs / 127.0)
            inv_scale = np.float32(0.0 if scale == 0.0 else 1.0 / scale)
            quant = np.rint(block * inv_scale).clip(-128, 127).astype(np.int8)
            chunks.append(pack_q8_0_block(float(scale), quant, endian_info))
    return b"".join(chunks)


def self_test_round_trip() -> None:
    endian_info = GGUFEndianInfo(endian="little", numpy_byte_order="<", version=3)
    quant = np.arange(-16, 16, dtype=np.int8)
    raw = pack_q8_0_block(0.25, quant, endian_info)
    scale, decoded_quant, decoded_values = decode_q8_0_block(raw, endian_info)
    np.testing.assert_equal(decoded_quant, quant)
    np.testing.assert_allclose(decoded_values, quant.astype(np.float32) * scale, rtol=0, atol=0)

    values = np.linspace(-1.0, 1.0, num=2 * Q8_0_BLOCK_SIZE, dtype=np.float32).reshape(2, -1)
    raw_rows = quantize_rows_to_q8_0_ref(values)

    decoded = np.empty_like(values)
    for row in range(values.shape[0]):
        block_offset = row * Q8_0_BLOCK_NUM_BYTES
        _scale, _quant, block_values = decode_q8_0_block(
            raw_rows[block_offset:block_offset + Q8_0_BLOCK_NUM_BYTES],
            endian_info,
        )
        decoded[row] = block_values

    # Quantization is lossy; the decoded result must be within half a scale
    # quantum plus the fp16 scale rounding noise.
    max_error = float(np.max(np.abs(values - decoded)))
    if max_error > 0.01:
        raise AssertionError(f"Q8_0 round-trip error too large: {max_error}")


def compare_with_gguf_library(
    tensor_name: str,
    row_start: int,
    row_count: int,
    col_start: int,
    col_count: int,
    *,
    gguf_path: Path | str = DEFAULT_GGUF_PATH,
    tensors_csv: Path | str = DEFAULT_TENSORS_CSV,
) -> dict[str, Any]:
    """Compare this decoder against installed gguf.quants.dequantize if present."""

    try:
        import gguf  # type: ignore[import-not-found]
        from gguf.quants import dequantize  # type: ignore[import-not-found]
    except ImportError as exc:
        return {
            "available": False,
            "reason": f"gguf import failed: {exc}",
        }

    gguf_path = resolve_existing_path(gguf_path)
    tensors_csv = resolve_existing_path(tensors_csv)
    ref_slice = decode_tensor_slice(
        tensor_name,
        row_start,
        row_count,
        col_start,
        col_count,
        representation="float",
        gguf_path=gguf_path,
        tensors_csv=tensors_csv,
    )

    reader = gguf.GGUFReader(str(gguf_path))
    tensor = next((item for item in reader.tensors if item.name == tensor_name), None)
    if tensor is None:
        return {
            "available": False,
            "reason": f"tensor {tensor_name!r} not found by gguf.GGUFReader",
        }

    lib_full = dequantize(tensor.data, tensor.tensor_type)
    lib_slice = lib_full[row_start:row_start + row_count, col_start:col_start + col_count]
    diff = np.abs(ref_slice - lib_slice)
    return {
        "available": True,
        "shape": list(ref_slice.shape),
        "max_abs_diff": float(np.max(diff)) if diff.size else 0.0,
        "allclose": bool(np.allclose(ref_slice, lib_slice, rtol=0.0, atol=0.0)),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Decode a small GGUF Q8_0 tensor slice.")
    parser.add_argument("--gguf", type=Path, default=DEFAULT_GGUF_PATH)
    parser.add_argument("--tensors-csv", type=Path, default=DEFAULT_TENSORS_CSV)
    parser.add_argument("--tensor", default=DEFAULT_TENSOR_NAME)
    parser.add_argument("--row-start", type=int, default=0)
    parser.add_argument("--row-count", type=int, default=2)
    parser.add_argument("--col-start", type=int, default=0)
    parser.add_argument("--col-count", type=int, default=8)
    parser.add_argument(
        "--representation",
        choices=("float", "int8"),
        default="float",
        help="Return dequantized float32 values or raw signed int8 payload values.",
    )
    parser.add_argument(
        "--compare-gguf",
        action="store_true",
        help="Compare this decoder with gguf.quants.dequantize for the same slice.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run built-in Q8_0 block and round-trip tests before decoding.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        if args.self_test:
            self_test_round_trip()
            print("[OK] Q8_0 self-test passed")

        array = decode_tensor_slice(
            args.tensor,
            args.row_start,
            args.row_count,
            args.col_start,
            args.col_count,
            representation=args.representation,
            gguf_path=args.gguf,
            tensors_csv=args.tensors_csv,
        )
        print(f"tensor: {args.tensor}")
        print(f"slice rows: [{args.row_start}, {args.row_start + args.row_count})")
        print(f"slice cols: [{args.col_start}, {args.col_start + args.col_count})")
        print(f"representation: {args.representation}")
        print(f"shape: {array.shape}")
        print(array)

        if args.compare_gguf:
            result = compare_with_gguf_library(
                args.tensor,
                args.row_start,
                args.row_count,
                args.col_start,
                args.col_count,
                gguf_path=args.gguf,
                tensors_csv=args.tensors_csv,
            )
            print("gguf comparison:")
            print(json.dumps(result, indent=2, sort_keys=True))
    except Exception as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
