# DEPRECATED - AXI-Lite data path is not the final interface

This document is kept only as a historical reference for the old AXI-Lite
bring-up path. The AXI-Lite `INPUT_DATA`/`STREAM_DATA`/`RESULT_DATA` data path
is not the final GEMV interface and must not be used as the active build path.
The next active hardware path must use DMA/AXI-Stream for bulk input, weight,
scale, and result movement.

# Q8_0 GEMV PS/PL Interface Contract

Date: 2026-06-29 KST

## Address Map

The generated Zybo Z7-20 smoke hardware maps the GEMV AXI-Lite register window at:

- Base: `0x43C00000`
- Range: `0x00001000`
- Access: 32-bit little-endian MMIO, word-aligned
- Source: `logs/hw_smoke_address_map.txt`

The full datapath wrapper is `vivado_ip/rtl/gemv_q8_0_axi_lite.v`. The current board-access fallback bitstream uses `vivado_ip/rtl/gemv_q8_0_axi_lite_smoke.v`; it keeps the same register window for Linux bring-up but does not execute fake_gemv.

## Registers

| Offset | Name | Access | Description |
| --- | --- | --- | --- |
| `0x00` | `VERSION` | RO | `0x00090001` |
| `0x04` | `CONTROL` | RW/W1P | bit0 start pulse, bit1 mode, bit2 clear sticky/result state |
| `0x08` | `STATUS` | RO | bit0 busy, bit1 done_sticky, bit2 core_error, bit3 stream_ready, bit4 result_count_nonzero, bit5 result_overflow, bit6 stream_write_error, bit7 input_addr_error, bit8 smoke_build |
| `0x0C` | `ERROR_CODE` | RO | Core error code, zero in smoke build |
| `0x10` | `SCALE_SHIFT` | RW | Default `20` |
| `0x14` | `IN_FEATURES` | RW | Default `32` for fake_gemv |
| `0x18` | `OUT_FEATURES` | RW | Default `3` for fake_gemv |
| `0x1C` | `DEBUG_ROW` | RO | Core debug row |
| `0x20` | `DEBUG_BLOCK` | RO | Core debug block |
| `0x24` | `DEBUG_LANE` | RO | Core debug lane |
| `0x28` | `INPUT_ADDR` | RW | Input vector address index |
| `0x2C` | `INPUT_DATA` | RW | Full wrapper: signed low 16-bit input sample at `INPUT_ADDR`; smoke: readback register |
| `0x30` | `STREAM_DATA` | WO/RB | Full wrapper: push one Q8_0 stream word when `STATUS[3]` is set; smoke: readback register |
| `0x34` | `STREAM_LAST` | RW | bit0 used as stream `tlast` on next `STREAM_DATA` write |
| `0x38` | `RESULT_COUNT` | RO | Captured result count |
| `0x3C` | `RESULT_ADDR` | RW | Result read index |
| `0x40` | `RESULT_DATA` | RO | Result data at `RESULT_ADDR` |
| `0x44` | `RESULT_ROW` | RO | Result row at `RESULT_ADDR` |
| `0x48` | `RESULT_BLOCK` | RO | Result block at `RESULT_ADDR` |
| `0x4C` | `RESULT_LANE` | RO | Result lane at `RESULT_ADDR` |
| `0x50` | `RESULT_LAST` | RO | Result last flag at `RESULT_ADDR` |

## Smoke Read Test

On Linux after loading the generated hardware:

```sh
devmem 0x43C00000 32
devmem 0x43C00008 32
devmem 0x43C00004 32 0x00000001
devmem 0x43C00008 32
```

Expected smoke values:

- `VERSION`: `0x00090001`
- `STATUS` after reset: bit8 set, typically `0x00000100`
- `STATUS` after start write: bit8 and bit1 set, typically `0x00000102`

## Full Datapath Software Sequence

For `gemv_q8_0_axi_lite.v`, software should load input samples through `INPUT_ADDR`/`INPUT_DATA`, program `SCALE_SHIFT`, `IN_FEATURES`, `OUT_FEATURES`, set `CONTROL.mode`, pulse `CONTROL.start`, poll `STATUS.stream_ready`, write Q8_0 scale/weight words to `STREAM_DATA`, then poll `STATUS.done_sticky` and read results through `RESULT_ADDR`/`RESULT_*`.
