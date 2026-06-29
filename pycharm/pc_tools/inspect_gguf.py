"""Inspect a GGUF file and dump metadata, tensor rows, and a short summary."""

from __future__ import annotations

import argparse
import csv
import json
import math
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO


PYCHARM_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PYCHARM_ROOT.parent
DEFAULT_GGUF = (
    PROJECT_ROOT
    / "quantized_model"
    / "original_gguf"
    / "SmolLM2-135M-Instruct-Q8_0.gguf"
)
DEFAULT_OUT_DIR = PYCHARM_ROOT / "reports" / "gguf_inspect"

DEFAULT_ALIGNMENT = 32


GGUF_VALUE_TYPES = {
    0: "UINT8",
    1: "INT8",
    2: "UINT16",
    3: "INT16",
    4: "UINT32",
    5: "INT32",
    6: "FLOAT32",
    7: "BOOL",
    8: "STRING",
    9: "ARRAY",
    10: "UINT64",
    11: "INT64",
    12: "FLOAT64",
}

GGUF_SCALAR_FORMATS = {
    0: "<B",
    1: "<b",
    2: "<H",
    3: "<h",
    4: "<I",
    5: "<i",
    6: "<f",
    10: "<Q",
    11: "<q",
    12: "<d",
}

# GGML tensor type id -> (name, block_size, bytes_per_block).
# This covers the common GGUF tensor encodings. Unknown newer ids are still
# listed with their numeric type name, but nbytes is derived from tensor spans.
GGML_TYPE_TRAITS = {
    0: ("F32", 1, 4),
    1: ("F16", 1, 2),
    2: ("Q4_0", 32, 18),
    3: ("Q4_1", 32, 20),
    6: ("Q5_0", 32, 22),
    7: ("Q5_1", 32, 24),
    8: ("Q8_0", 32, 34),
    9: ("Q8_1", 32, 36),
    10: ("Q2_K", 256, 84),
    11: ("Q3_K", 256, 110),
    12: ("Q4_K", 256, 144),
    13: ("Q5_K", 256, 176),
    14: ("Q6_K", 256, 210),
    15: ("Q8_K", 256, 292),
    16: ("IQ2_XXS", 256, None),
    17: ("IQ2_XS", 256, None),
    18: ("IQ3_XXS", 256, None),
    19: ("IQ1_S", 256, None),
    20: ("IQ4_NL", 32, None),
    21: ("IQ3_S", 256, None),
    22: ("IQ2_S", 256, None),
    23: ("IQ4_XS", 256, None),
    24: ("I8", 1, 1),
    25: ("I16", 1, 2),
    26: ("I32", 1, 4),
    27: ("I64", 1, 8),
    28: ("F64", 1, 8),
    29: ("IQ1_M", 256, None),
    30: ("BF16", 1, 2),
    31: ("Q4_0_4_4", 32, None),
    32: ("Q4_0_4_8", 32, None),
    33: ("Q4_0_8_8", 32, None),
    34: ("TQ1_0", 256, None),
    35: ("TQ2_0", 256, None),
}


@dataclass
class TensorInfo:
    tensor_name: str
    shape: list[int]
    dtype_or_quant_type: str
    offset: int | None
    nbytes: int | None


@dataclass
class InspectResult:
    metadata: dict[str, Any]
    tensors: list[TensorInfo]
    parser_name: str
    file_path: Path
    file_exists: bool
    file_size: int | None
    gguf_version: int | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


class GGUFParseError(RuntimeError):
    """Raised when the built-in GGUF parser cannot continue."""


class BinaryReader:
    def __init__(self, file_obj: BinaryIO) -> None:
        self._file = file_obj

    def tell(self) -> int:
        return self._file.tell()

    def read_exact(self, size: int) -> bytes:
        data = self._file.read(size)
        if len(data) != size:
            raise GGUFParseError(f"unexpected end of file at byte offset {self.tell()}")
        return data

    def read_u32(self) -> int:
        return struct.unpack("<I", self.read_exact(4))[0]

    def read_u64(self) -> int:
        return struct.unpack("<Q", self.read_exact(8))[0]

    def read_scalar(self, value_type: int) -> Any:
        if value_type == 7:
            return bool(struct.unpack("<?", self.read_exact(1))[0])

        fmt = GGUF_SCALAR_FORMATS.get(value_type)
        if fmt is None:
            type_name = GGUF_VALUE_TYPES.get(value_type, f"UNKNOWN_{value_type}")
            raise GGUFParseError(f"unsupported GGUF metadata scalar type: {type_name}")
        return struct.unpack(fmt, self.read_exact(struct.calcsize(fmt)))[0]

    def read_count(self, version: int) -> int:
        if version == 1:
            return self.read_u32()
        return self.read_u64()

    def read_string(self, version: int) -> str:
        size = self.read_count(version)
        raw = self.read_exact(size)
        return raw.decode("utf-8", errors="replace")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect a GGUF file and dump metadata/tensor information."
    )
    parser.add_argument(
        "--gguf",
        type=Path,
        default=DEFAULT_GGUF,
        help=f"GGUF file path. Default: {DEFAULT_GGUF}",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"Output directory. Default: {DEFAULT_OUT_DIR}",
    )
    return parser.parse_args()


def resolve_path(path: Path) -> Path:
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


def enum_name(value: Any) -> str:
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    text = str(value)
    if "." in text:
        return text.rsplit(".", 1)[-1]
    return text


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return {"hex": value.hex()}

    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]

    if hasattr(value, "tolist"):
        try:
            return json_safe(value.tolist())
        except (TypeError, ValueError):
            pass

    if hasattr(value, "item"):
        try:
            return json_safe(value.item())
        except (TypeError, ValueError):
            pass

    return str(value)


def decode_reader_part(part: Any, value_type: Any) -> Any:
    type_name = enum_name(value_type)

    if type_name == "STRING":
        if isinstance(part, bytes):
            return part.decode("utf-8", errors="replace")
        if hasattr(part, "tobytes"):
            try:
                return part.tobytes().decode("utf-8", errors="replace")
            except (AttributeError, UnicodeDecodeError):
                pass

        safe = json_safe(part)
        if isinstance(safe, list) and all(isinstance(item, int) for item in safe):
            return bytes(safe).decode("utf-8", errors="replace")
        return safe

    safe = json_safe(part)
    if isinstance(safe, list) and len(safe) == 1:
        safe = safe[0]
    if type_name == "BOOL":
        return bool(safe)
    return safe


def gguf_reader_field_value(field: Any) -> Any:
    for attr_name in ("value", "values", "contents"):
        attr = getattr(field, attr_name, None)
        if attr is None:
            continue
        if callable(attr):
            return json_safe(attr())
        return json_safe(attr)

    parts = getattr(field, "parts", None)
    data_indexes = getattr(field, "data", None)
    if parts is not None and data_indexes is not None:
        types = list(getattr(field, "types", []) or [])
        if types and enum_name(types[0]) == "ARRAY":
            item_type = types[1] if len(types) > 1 else None
            return [decode_reader_part(parts[index], item_type) for index in data_indexes]

        value_type = types[0] if types else None
        values = [decode_reader_part(parts[index], value_type) for index in data_indexes]
        if len(values) == 1:
            return values[0]
        return values

    return json_safe(field)


def gguf_reader_tensor_info(tensor: Any) -> TensorInfo:
    name = str(getattr(tensor, "name"))

    shape_value = getattr(tensor, "shape", [])
    shape = [int(item) for item in json_safe(shape_value)]

    tensor_type = getattr(tensor, "tensor_type", getattr(tensor, "type", None))
    type_name = enum_name(tensor_type)

    offset = None
    for attr_name in ("data_offset", "offset"):
        if hasattr(tensor, attr_name):
            offset = int(getattr(tensor, attr_name))
            break

    nbytes = None
    for attr_name in ("n_bytes", "nbytes"):
        if hasattr(tensor, attr_name):
            nbytes = int(getattr(tensor, attr_name))
            break

    if nbytes is None:
        nbytes = calc_tensor_nbytes(shape, tensor_type)

    return TensorInfo(
        tensor_name=name,
        shape=shape,
        dtype_or_quant_type=type_name,
        offset=offset,
        nbytes=nbytes,
    )


def inspect_with_gguf_library(gguf_path: Path) -> InspectResult:
    try:
        import gguf  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(f"gguf Python library is not installed: {exc}") from exc

    reader_cls = getattr(gguf, "GGUFReader", None)
    if reader_cls is None:
        raise RuntimeError("gguf Python library does not expose GGUFReader")

    reader = reader_cls(str(gguf_path))

    fields = getattr(reader, "fields", None)
    if fields is None:
        raise RuntimeError("GGUFReader object does not expose fields")

    metadata = {
        str(key): gguf_reader_field_value(field)
        for key, field in dict(fields).items()
    }

    raw_tensors = getattr(reader, "tensors", None)
    if raw_tensors is None:
        raise RuntimeError("GGUFReader object does not expose tensors")
    tensors = [gguf_reader_tensor_info(tensor) for tensor in raw_tensors]

    version = None
    for attr_name in ("version", "gguf_version"):
        if hasattr(reader, attr_name):
            version = int(getattr(reader, attr_name))
            break
    if version is None and isinstance(metadata.get("GGUF.version"), int):
        version = int(metadata["GGUF.version"])

    return InspectResult(
        metadata=metadata,
        tensors=tensors,
        parser_name="gguf Python library",
        file_path=gguf_path,
        file_exists=True,
        file_size=gguf_path.stat().st_size,
        gguf_version=version,
    )


def read_metadata_value(reader: BinaryReader, version: int, value_type: int) -> Any:
    if value_type == 8:
        return reader.read_string(version)

    if value_type == 9:
        item_type = reader.read_u32()
        item_count = reader.read_count(version)
        return [read_metadata_value(reader, version, item_type) for _ in range(item_count)]

    return reader.read_scalar(value_type)


def product(items: list[int]) -> int:
    result = 1
    for item in items:
        result *= item
    return result


def calc_tensor_nbytes(shape: list[int], tensor_type: Any) -> int | None:
    try:
        tensor_type_id = int(tensor_type)
    except (TypeError, ValueError):
        tensor_type_name = enum_name(tensor_type)
        tensor_type_id = next(
            (key for key, traits in GGML_TYPE_TRAITS.items() if traits[0] == tensor_type_name),
            None,
        )
        if tensor_type_id is None:
            return None

    traits = GGML_TYPE_TRAITS.get(tensor_type_id)
    if traits is None:
        return None

    _name, block_size, bytes_per_block = traits
    if bytes_per_block is None:
        return None

    return math.ceil(product(shape) / block_size) * bytes_per_block


def align_offset(offset: int, alignment: int) -> int:
    if alignment <= 0:
        return offset
    return ((offset + alignment - 1) // alignment) * alignment


def tensor_type_name(tensor_type: int) -> str:
    traits = GGML_TYPE_TRAITS.get(tensor_type)
    if traits is None:
        return f"TYPE_{tensor_type}"
    return traits[0]


def fill_unknown_tensor_nbytes(
    tensors: list[TensorInfo],
    file_size: int,
) -> None:
    indexed_offsets = [
        (index, tensor.offset)
        for index, tensor in enumerate(tensors)
        if tensor.offset is not None
    ]
    indexed_offsets.sort(key=lambda item: item[1])

    for position, (index, offset) in enumerate(indexed_offsets):
        tensor = tensors[index]
        if tensor.nbytes is not None:
            continue

        if position + 1 < len(indexed_offsets):
            next_offset = indexed_offsets[position + 1][1]
            tensor.nbytes = max(0, next_offset - offset)
        else:
            tensor.nbytes = max(0, file_size - offset)


def inspect_with_builtin_parser(gguf_path: Path) -> InspectResult:
    file_size = gguf_path.stat().st_size

    with gguf_path.open("rb") as file_obj:
        reader = BinaryReader(file_obj)
        magic = reader.read_exact(4)
        if magic != b"GGUF":
            raise GGUFParseError(f"not a GGUF file: magic bytes are {magic!r}")

        version = reader.read_u32()
        if version not in (1, 2, 3):
            raise GGUFParseError(f"unsupported GGUF version: {version}")

        tensor_count = reader.read_count(version)
        metadata_count = reader.read_count(version)

        metadata: dict[str, Any] = {}
        for _ in range(metadata_count):
            key = reader.read_string(version)
            value_type = reader.read_u32()
            metadata[key] = read_metadata_value(reader, version, value_type)

        tensors: list[TensorInfo] = []
        raw_tensor_offsets: list[int] = []
        for _ in range(tensor_count):
            name = reader.read_string(version)
            dim_count = reader.read_u32()
            shape = [reader.read_u64() for _ in range(dim_count)]
            tensor_type = reader.read_u32()
            raw_offset = reader.read_u64()
            raw_tensor_offsets.append(raw_offset)
            tensors.append(
                TensorInfo(
                    tensor_name=name,
                    shape=shape,
                    dtype_or_quant_type=tensor_type_name(tensor_type),
                    offset=None,
                    nbytes=calc_tensor_nbytes(shape, tensor_type),
                )
            )

        alignment = metadata.get("general.alignment", DEFAULT_ALIGNMENT)
        if not isinstance(alignment, int):
            alignment = DEFAULT_ALIGNMENT
        data_start = align_offset(reader.tell(), alignment)

    for tensor, raw_offset in zip(tensors, raw_tensor_offsets):
        tensor.offset = data_start + raw_offset

    fill_unknown_tensor_nbytes(tensors, file_size)

    return InspectResult(
        metadata=metadata,
        tensors=tensors,
        parser_name="built-in GGUF parser",
        file_path=gguf_path,
        file_exists=True,
        file_size=file_size,
        gguf_version=version,
    )


def inspect_gguf(gguf_path: Path) -> InspectResult:
    if not gguf_path.exists():
        return InspectResult(
            metadata={},
            tensors=[],
            parser_name="none",
            file_path=gguf_path,
            file_exists=False,
            file_size=None,
            error=f"GGUF file does not exist: {gguf_path}",
        )

    if not gguf_path.is_file():
        return InspectResult(
            metadata={},
            tensors=[],
            parser_name="none",
            file_path=gguf_path,
            file_exists=True,
            file_size=None,
            error=f"GGUF path is not a regular file: {gguf_path}",
        )

    library_error = None
    try:
        return inspect_with_gguf_library(gguf_path)
    except Exception as exc:  # pragma: no cover - depends on installed gguf API
        library_error = f"{type(exc).__name__}: {exc}"

    try:
        result = inspect_with_builtin_parser(gguf_path)
        result.warnings.append(
            "gguf Python library path failed; used built-in parser instead."
        )
        result.warnings.append(f"gguf library error: {library_error}")
        return result
    except Exception as exc:
        return InspectResult(
            metadata={},
            tensors=[],
            parser_name="none",
            file_path=gguf_path,
            file_exists=True,
            file_size=gguf_path.stat().st_size,
            error=(
                f"gguf library error: {library_error}\n"
                f"built-in parser error: {type(exc).__name__}: {exc}"
            ),
        )


def get_first(metadata: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in metadata:
            return metadata[key]
    return None


def get_first_suffix(metadata: dict[str, Any], suffixes: tuple[str, ...]) -> Any:
    for suffix in suffixes:
        for key, value in metadata.items():
            if key.endswith(suffix):
                return value
    return None


def get_summary_value(metadata: dict[str, Any], *keys_or_suffixes: str) -> Any:
    exact = tuple(item for item in keys_or_suffixes if not item.startswith("*"))
    suffixes = tuple(item[1:] for item in keys_or_suffixes if item.startswith("*"))
    value = get_first(metadata, exact)
    if value is not None:
        return value
    return get_first_suffix(metadata, suffixes)


def get_vocab_size(metadata: dict[str, Any]) -> Any:
    value = get_summary_value(metadata, "llama.vocab_size", "general.vocab_size")
    if value is not None:
        return value

    tokens = metadata.get("tokenizer.ggml.tokens")
    if isinstance(tokens, list):
        return len(tokens)
    return None


def display_value(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, list):
        return f"{len(value)} entries"
    return str(value)


def format_bytes(size: int | None) -> str:
    if size is None:
        return "unknown"

    units = ("B", "KiB", "MiB", "GiB")
    value = float(size)
    unit = units[0]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            break
        value /= 1024
    return f"{size:,} bytes ({value:.2f} {unit})"


def build_summary(result: InspectResult) -> str:
    metadata = result.metadata
    tensor_types = sorted(
        {tensor.dtype_or_quant_type for tensor in result.tensors},
        key=lambda item: (item.startswith("TYPE_"), item),
    )

    model_name = get_summary_value(
        metadata,
        "general.name",
        "general.basename",
        "general.architecture",
    )
    hidden_size = get_summary_value(metadata, "llama.embedding_length", "*.embedding_length")
    layer_count = get_summary_value(metadata, "llama.block_count", "*.block_count")
    attention_heads = get_summary_value(
        metadata,
        "llama.attention.head_count",
        "*.attention.head_count",
    )
    kv_heads = get_summary_value(
        metadata,
        "llama.attention.head_count_kv",
        "*.attention.head_count_kv",
    )
    intermediate_size = get_summary_value(
        metadata,
        "llama.feed_forward_length",
        "*.feed_forward_length",
    )
    context_length = get_summary_value(
        metadata,
        "llama.context_length",
        "*.context_length",
    )

    lines = [
        "GGUF inspection summary",
        f"GGUF file: {result.file_path}",
        f"File exists: {result.file_exists}",
        f"Parser: {result.parser_name}",
        f"GGUF version: {display_value(result.gguf_version)}",
        "",
        f"Model name: {display_value(model_name)}",
        f"Vocab size: {display_value(get_vocab_size(metadata))}",
        f"Hidden size: {display_value(hidden_size)}",
        f"Layer count: {display_value(layer_count)}",
        f"Attention head count: {display_value(attention_heads)}",
        f"KV head count: {display_value(kv_heads)}",
        f"Intermediate size: {display_value(intermediate_size)}",
        f"Context length: {display_value(context_length)}",
        f"Quantization/tensor types: {', '.join(tensor_types) if tensor_types else 'unknown'}",
        f"Total tensor count: {len(result.tensors)}",
        f"Total file size: {format_bytes(result.file_size)}",
    ]

    if result.warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in result.warnings)

    if result.error:
        lines.extend(["", "Error:"])
        lines.extend(result.error.splitlines())

    return "\n".join(lines) + "\n"


def write_metadata_json(result: InspectResult, out_dir: Path) -> None:
    if result.metadata:
        payload: dict[str, Any] = result.metadata
    else:
        payload = {
            "file": str(result.file_path),
            "file_exists": result.file_exists,
            "file_size": result.file_size,
            "error": result.error,
        }

    with (out_dir / "metadata.json").open("w", encoding="utf-8") as file_obj:
        json.dump(json_safe(payload), file_obj, ensure_ascii=False, indent=2)
        file_obj.write("\n")


def write_tensors_csv(result: InspectResult, out_dir: Path) -> None:
    fieldnames = [
        "tensor_name",
        "shape",
        "dtype_or_quant_type",
        "offset",
        "nbytes",
    ]
    with (out_dir / "tensors.csv").open("w", encoding="utf-8", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
        writer.writeheader()
        for tensor in result.tensors:
            writer.writerow(
                {
                    "tensor_name": tensor.tensor_name,
                    "shape": json.dumps(tensor.shape, separators=(",", ":")),
                    "dtype_or_quant_type": tensor.dtype_or_quant_type,
                    "offset": "" if tensor.offset is None else tensor.offset,
                    "nbytes": "" if tensor.nbytes is None else tensor.nbytes,
                }
            )


def write_summary_txt(result: InspectResult, out_dir: Path) -> None:
    (out_dir / "summary.txt").write_text(build_summary(result), encoding="utf-8")


def write_outputs(result: InspectResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_metadata_json(result, out_dir)
    write_tensors_csv(result, out_dir)
    write_summary_txt(result, out_dir)


def main() -> int:
    args = parse_args()
    gguf_path = resolve_path(args.gguf)
    out_dir = resolve_path(args.out)

    result = inspect_gguf(gguf_path)
    write_outputs(result, out_dir)

    print(f"metadata: {out_dir / 'metadata.json'}")
    print(f"tensors:  {out_dir / 'tensors.csv'}")
    print(f"summary:  {out_dir / 'summary.txt'}")

    if result.error:
        print(f"[FAIL] {result.error}", file=sys.stderr)
        return 1

    if result.warnings:
        for warning in result.warnings:
            print(f"[WARN] {warning}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
