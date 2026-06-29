# DMA GEMV Hardware Architecture

Date: 2026-06-29 KST

This is the active S01 hardware architecture for the Zybo Z7-20 GEMV path. It
keeps `vivado_ip/rtl/gemv_q8_0_stream_core.v` as the compute core and replaces
bulk MMIO movement with AXI DMA plus AXI-Stream.

## Scope

S01 creates RTL wrappers, Vivado block-design Tcl, and architecture documents.
It does not run synthesis, implementation, bitstream generation, XSA export,
PetaLinux, SD-card work, or board tests.

## Block Diagram

```text
Zynq PS M_AXI_GP0
  -> SmartConnect
      -> gemv_q8_0_dma_top/S_AXI       control/status only
      -> axi_dma_0/S_AXI_LITE          DMA control
      -> axi_input_bram_ctrl/S_AXI     input vector BRAM window

DDR through PS S_AXI_HP0
  <- SmartConnect
      <- axi_dma_0/M_AXI_MM2S          read packed scale/weight stream
      <- axi_dma_0/M_AXI_S2MM          write result stream

axi_dma_0/M_AXIS_MM2S
  -> mm2s_axis_fifo
  -> gemv_q8_0_dma_top/S_AXIS
  -> gemv_q8_0_stream_core

gemv_q8_0_stream_core
  -> gemv_q8_0_dma_top/M_AXIS
  -> s2mm_axis_fifo
  -> axi_dma_0/S_AXIS_S2MM

axi_input_bram_ctrl/BRAM_PORTA
  -> input_vector_bram
  -> gemv_q8_0_dma_top/INPUT_BRAM_PORT
```

## Data Movement

Input vector:

- Stored as signed int16 values in a 32-bit AXI BRAM window.
- Two samples are packed per 32-bit word.
- The GEMV top reads the BRAM through port B and selects the low or high half
  word based on the core input address.

Weight/scale stream:

- AXI DMA MM2S reads the packed stream from DDR.
- The stream order is unchanged from the verified stream core contract:
  per row group and Q8_0 block, first one signed int32 scale value per lane,
  then 32 columns of packed signed int8 weights across 16 lanes.
- AXIS Data FIFO absorbs burst/backpressure mismatch between DMA and the core.

Result stream:

- `mode=0` emits one signed int32 scaled row result per valid row.
- `mode=1` emits signed int32 block accumulators per valid row and Q8_0 block.
- AXIS Data FIFO feeds AXI DMA S2MM, which writes results into DDR.

## Active Files

- `vivado_ip/rtl/gemv_q8_0_stream_core.v`
- `vivado_ip/rtl/gemv_q8_0_ctrl_axi_lite.v`
- `vivado_ip/rtl/gemv_q8_0_dma_top.v`
- `scripts/create_zybo_gemv_dma_hw.tcl`
- `scripts/build_zybo_gemv_dma_bitstream.tcl`
- `scripts/report_failed_impl.tcl`

## Address Plan

The S01 block-design Tcl requests:

| Region | Base | Range | Purpose |
| --- | ---: | ---: | --- |
| GEMV control | `0x43C00000` | `4K` | Control/status registers |
| AXI DMA | `0x40400000` | `64K` | DMA control registers |
| Input BRAM | `0x42000000` | `64K` | Input vector memory window |

`scripts/create_zybo_gemv_dma_hw.tcl` writes the actual Vivado address map to
`logs/hw_dma_address_map.txt` when run.

## S01 Verification Intent

The S01 pass condition is architectural:

- AXI DMA is present.
- PS `S_AXI_HP0` is enabled for DDR DMA traffic.
- MM2S feeds FIFO then GEMV stream input.
- GEMV stream output feeds FIFO then S2MM.
- AXI-Lite is limited to control/status metadata.
- PL clock starts at 50 MHz.
- Stream core mode handling remains intact.

Bitstream and XSA production belong to the next stage.
