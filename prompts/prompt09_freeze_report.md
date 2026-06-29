# Prompt 09 RTL Simulation Freeze Report

Date: 2026-06-29 KST

## Scope

- RTL: `vivado_ip/rtl/gemv_q8_0_stream_core.v`
- Testbench wrapper: `vivado_ip/tb/tb_gemv_q8_0_stream_core.v`
- Testbench body: `vivado_ip/tb/tb_gemv_q8_0_stream_core.sv`
- Simulation script: `scripts/run_gemv_sim.tcl`
- Golden data: `pycharm/golden/fake_gemv`

## Result

`vivado -mode batch -source scripts/run_gemv_sim.tcl` completed with Vivado Simulator v2024.2.2.

Summary file: `logs/gemv_sim_result.txt`

- [PASS] mode=0 scaled outputs matched golden/fake_gemv
- [PASS] mode=1 block-acc outputs matched golden/fake_gemv
- [PASS] tb_gemv_q8_0_stream_core completed

## Freeze Checks

- [PASS] `scripts/run_gemv_sim.tcl` remains executable.
- [PASS] RTL/TB contain no `real`, `shortreal`, `float`, or `floating-point` tokens.
- [PASS] `.v` and `.sv` testbench files do not duplicate a module definition.
- [PASS] Golden files were consumed from `pycharm/golden/fake_gemv`; they were not regenerated.
- [PASS] RTL calculation logic was not changed for this freeze.

## Environment Note

The host does not provide `/usr/bin/gcc`, while Vivado xelab checks that path during elaboration. The simulation script builds a process-local preload shim under `logs/` so xelab uses Vivado bundled GCC at `/tools/Xilinx/Vivado/2024.2/tps/lnx64/gcc-9.3.0/bin` without modifying system files.
