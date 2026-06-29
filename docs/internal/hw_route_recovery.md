# DMA GEMV Route Recovery Plan

Date: 2026-06-29 KST

This note captures the S01 timing and route policy for the DMA GEMV hardware.
It is a build plan, not a completed timing result.

## Clock Policy

- Start PL logic at 50 MHz through `processing_system7_0/FCLK_CLK0`.
- Keep the PS clock policy unchanged.
- Keep GEMV, AXI DMA, AXIS FIFOs, AXI BRAM Controller, and SmartConnect on the
  same FCLK clock unless a later build explicitly introduces clock converters.
- Use `proc_sys_reset`; do not directly fan out PS reset to all IP resets.
- Only after a routed 50 MHz design succeeds should 75 MHz and 100 MHz be tried.

## RTL Recovery

`gemv_q8_0_stream_core.v` keeps the verified stream protocol, 16 lanes, scale
application, and both output modes. The mode=0 block finalization path is now
split into stages:

1. latch `block_acc` and `scale_q`
2. multiply signed int32 accumulator by signed int32 scale
3. apply rounded shift
4. accumulate into the row accumulator

This removes the previous one-cycle multiply, round-shift, and row-accumulate
chain across all lanes while preserving behavior.

## Storage Policy

- Input vector storage is an AXI BRAM window plus a second BRAM read port for
  the GEMV core.
- Output vectors are not exposed as a wide register array.
- Weight/scale and result payload movement goes through AXI DMA streams.
- Large buffers should remain in BRAM/XPM/IP memory, not FF/LUT arrays.

## Diagnostics

If implementation fails, run or source `scripts/report_failed_impl.tcl`. It
generates:

- `reports/full_gemv_util_hier.rpt`
- `reports/full_gemv_timing_summary.rpt`
- `reports/full_gemv_route_status.rpt`
- `reports/full_gemv_congestion.rpt`
- `reports/full_gemv_qor_suggestions.rpt`

The long-running build entrypoint is
`scripts/build_zybo_gemv_dma_bitstream.tcl`. It is intentionally not executed
as part of S01-only work.

## Failure Report Checklist

For a failed S02 build, report:

- WNS and TNS
- route congestion hot spots
- top 10 hierarchical utilization entries
- five longest timing paths
- DMA block-design connectivity status
- whether any legacy AXI-Lite bulk data movement reappeared
- next concrete RTL or BD changes
