# Deprecated AXI-Lite Data Path

Date: 2026-06-29 KST

## Status

The old AXI-Lite data-path build has been removed from the active RTL and script
paths and moved under `deprecated/old_axi_lite_bringup/`.

This path was deprecated because bulk input, weight/scale stream, and output
movement through AXI-Lite registers is not the final architecture. AXI-Lite is
allowed only for control/status in the final path; bulk data must move through a
DMA/AXI-Stream design created in a later stage.

## Moved Files

- `vivado_ip/rtl/gemv_q8_0_axi_lite.v` -> `deprecated/old_axi_lite_bringup/vivado_ip/rtl/gemv_q8_0_axi_lite.v`
- `vivado_ip/rtl/gemv_q8_0_axi_lite_smoke.v` -> `deprecated/old_axi_lite_bringup/vivado_ip/rtl/gemv_q8_0_axi_lite_smoke.v`
- `scripts/create_zybo_gemv_hw.tcl` -> `deprecated/old_axi_lite_bringup/scripts/create_zybo_gemv_hw.tcl`
- `scripts/create_zybo_gemv_smoke_hw.tcl` -> `deprecated/old_axi_lite_bringup/scripts/create_zybo_gemv_smoke_hw.tcl`

## Preserved Active Files

- `vivado_ip/rtl/gemv_q8_0_stream_core.v`
- `vivado_ip/tb/tb_gemv_q8_0_stream_core.sv`
- `vivado_ip/tb/tb_gemv_q8_0_stream_core.v`
- `scripts/run_gemv_sim.tcl`
- `golden/fake_gemv/`
- `fpga_layout/q8_0_lane16/`
- `runtime_c/`
- `logs/gemv_sim_result.txt`

## Deprecated Register Data Path

The following old register data path is deprecated and must not be used as the
final build path:

- `INPUT_DATA` repeated AXI-Lite writes for input vectors
- `STREAM_DATA` repeated AXI-Lite writes for weight/scale stream words
- `RESULT_DATA` repeated AXI-Lite reads for output vectors

The old wrappers also contain related register names such as `REG_INPUT_DATA`,
`REG_STREAM_DATA`, and `REG_RESULT_DATA`; those names are now confined to the
deprecated bring-up tree and deprecated documentation.

## Active Build TODO

`scripts/create_zybo_gemv_dma_hw.tcl` does not exist yet. This is expected after
S00 and is a TODO for S01, not part of this stage.

