# DMA GEMV Interface Contract

Date: 2026-06-29 KST

This is the active hardware/software contract for the DMA GEMV path.

## Address Regions

| Region | Requested base | Range | Access |
| --- | ---: | ---: | --- |
| GEMV control | `0x43C00000` | `4K` | 32-bit AXI-Lite |
| AXI DMA | `0x40400000` | `64K` | Xilinx AXI DMA register map |
| Input BRAM | `0x42000000` | `64K` | 32-bit memory window |

Vivado is the source of truth after address assignment. The generated address
report is `logs/hw_dma_address_map.txt`.

## GEMV Control Register Map

Offsets are relative to the GEMV control base.

| Offset | Name | Access | Description |
| ---: | --- | --- | --- |
| `0x00` | `VERSION` | RO | DMA GEMV wrapper version, currently `0x000A0001` |
| `0x04` | `CONTROL` | WO | bit0 start pulse, bit1 clear sticky status |
| `0x08` | `STATUS` | RO | bit0 busy, bit1 done sticky, bit2 core error, bit3 stream input ready, bit4 result output valid, bit5 result backpressure, bit6 start while busy, bit7 mode |
| `0x0C` | `ERROR_CODE` | RO | core error code |
| `0x10` | `MODE` | RW | `0` scaled row output, `1` block accumulator debug output |
| `0x14` | `SCALE_SHIFT` | RW | fixed-scale right shift, default `20` |
| `0x18` | `IN_FEATURES` | RW | input feature count, multiple of 32 |
| `0x1C` | `OUT_FEATURES` | RW | valid output row count |
| `0x20` | `INPUT_BRAM_BASE` | RW | software-visible input BRAM base metadata |
| `0x24` | `WEIGHT_STREAM_LENGTH` | RW | expected MM2S byte count metadata |
| `0x28` | `RESULT_LENGTH` | RW | expected S2MM byte count metadata |
| `0x2C` | `START` | WO | bit0 alternate start pulse |
| `0x30` | `DONE` | RW | bit0 done sticky, bit1 raw done pulse; write bit0 to clear sticky |
| `0x34` | `DEBUG_ROW` | RO | current/debug row |
| `0x38` | `DEBUG_BLOCK` | RO | current/debug Q8_0 block |
| `0x3C` | `DEBUG_LANE` | RO | current/debug lane |

AXI-Lite carries no bulk tensor payload in this contract.

## Software Sequence

1. Write the signed int16 input vector into the input BRAM memory window.
2. Prepare a packed scale/weight stream in a DMA-coherent DDR buffer.
3. Prepare a DMA-coherent DDR result buffer.
4. Program GEMV `MODE`, `SCALE_SHIFT`, `IN_FEATURES`, `OUT_FEATURES`,
   `WEIGHT_STREAM_LENGTH`, and `RESULT_LENGTH`.
5. Program AXI DMA S2MM with the result buffer address and byte count.
6. Program AXI DMA MM2S with the packed stream address and byte count.
7. Write GEMV `START`.
8. Poll DMA completion and GEMV `DONE`.
9. Compare the DDR result buffer against the relevant golden output.

## Input BRAM Packing

The input BRAM is 32-bit wide. Software packs two signed int16 input samples per
word:

```text
word[n][15:0]  = input[2*n]
word[n][31:16] = input[2*n + 1]
```

The GEMV top presents byte addresses to BRAM port B and selects the correct
half word internally.

## Stream Contract

The AXI stream into GEMV is one 32-bit word wide with `TKEEP=0xF`. The stream
order remains the proven core order:

```text
for each row group of 16 rows:
  for each Q8_0 block:
    16 signed int32 scale words, lane order 0..15
    for each of 32 input columns:
      four signed int8 weights packed little-endian per 32-bit word
```

`TLAST` marks the final word of the entire GEMV stream. The core raises an error
if the final marker appears early or late.

The output stream is one signed int32 value per 32-bit word with `TKEEP=0xF`.
`TLAST` marks the final result word for the configured run.
