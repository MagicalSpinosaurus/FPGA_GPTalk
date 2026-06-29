# SUPERSEDED BY S00 - smoke AXI-Lite path is not final

This bring-up result is retained as historical evidence only. The smoke
AXI-Lite bitstream and the old AXI-Lite GEMV wrapper are deprecated by S00 and
must not be used as the active or final GEMV build path.

# Zybo Z7-20 GEMV Hardware Bring-Up Result

Date: 2026-06-29 KST

## Summary

- RTL simulation: PASS against `golden/fake_gemv`.
- Full GEMV AXI-Lite wrapper: added and synthesized, but full-core implementation was stopped during congested routing.
- Board-access fallback: PASS. A smoke AXI-Lite bitstream/XSA was generated with the GEMV register window at `0x43C00000`.
- PetaLinux/SD update: blocked in this environment because PetaLinux tools are not installed or discoverable, and the mounted SD card partitions are read-only.
- Board Linux test: not run yet; board connection/boot is needed.

## Simulation

`logs/gemv_sim_result.txt` reports:

```text
[PASS] mode=0 scaled outputs matched golden/fake_gemv
[PASS] mode=1 block-acc outputs matched golden/fake_gemv
[PASS] tb_gemv_q8_0_stream_core completed
```

RTL/TB keyword scan found no `real`, `shortreal`, `float`, or `floating-point` keywords under `vivado_ip/rtl` and `vivado_ip/tb`. The `.v` testbench is a compatibility include wrapper; the literal `tb_gemv_q8_0_stream_core` module definition is only in the `.sv` file.

## Hardware Artifacts

Generated fallback smoke artifacts:

- Bitstream: `hw/zybo_gemv_smoke/zybo_gemv_smoke.runs/impl_1/design_1_wrapper.bit`
- XSA: `hw/zybo_gemv_smoke/export/zybo_gemv_smoke.xsa`
- Address map: `logs/hw_smoke_address_map.txt`
- Vivado console log: `logs/create_zybo_gemv_smoke_hw_console.log`

Smoke implementation completed route and bitgen. Routed timing summary:

```text
WNS 3.734 ns, TNS 0.000 ns, WHS 0.038 ns, THS 0.000 ns
All user specified timing constraints are met.
```

## GEMV Base Address

`logs/hw_smoke_address_map.txt`:

```text
/processing_system7_0/Data/SEG_gemv_q8_0_axi_lite_smoke_0_reg0 OFFSET=0x43C00000 RANGE=0x00001000
```

## Register Map

The C/devmem contract is documented in `docs/deprecated/interface_contract.md`. Minimum smoke-test registers:

| Offset | Register | Expected |
| --- | --- | --- |
| `0x00` | `VERSION` | `0x00090001` |
| `0x08` | `STATUS` | bit8 `smoke_build`; after reset typically `0x00000100` |

## Full-Core Build Status

The full wrapper build used:

- RTL: `vivado_ip/rtl/gemv_q8_0_stream_core.v`
- AXI-Lite wrapper: `vivado_ip/rtl/gemv_q8_0_axi_lite.v`
- Tcl: `scripts/create_zybo_gemv_hw.tcl`
- Log: `logs/create_zybo_gemv_hw_console.log`

It reached routing, but showed severe congestion and timing risk:

```text
WNS=-10.568 ns, TNS=-17035.241 ns
Unrouted nets at route init: 73660
Route overlap count reduced to 10027 before the run was stopped
```

No full-core `.bit` was generated in `hw/zybo_gemv_bringup`.

## PetaLinux And SD Card

Blocked items:

- `petalinux-build`, `petalinux-config`, and `petalinux-package` are not in `PATH`.
- Search under `/tools`, `/opt`, and `/home/pjs` found only Vivado/Vitis settings scripts, not PetaLinux.
- SD card mounts are read-only:
  - `/run/media/pjs/bootfs`
  - `/run/media/pjs/rootfs`
- Existing SD boot files observed but not modified:
  - `/run/media/pjs/bootfs/BOOT.BIN`
  - `/run/media/pjs/bootfs/image.ub`

## Board Test Commands

After a boot image that programs `design_1_wrapper.bit` is installed, Linux smoke test:

```sh
devmem 0x43C00000 32
devmem 0x43C00008 32
devmem 0x43C00004 32 0x00000001
devmem 0x43C00008 32
```

Expected:

- `VERSION`: `0x00090001`
- `STATUS`: bit8 set; after start, bit1 also set

Next handoff step is to install PetaLinux or provide an existing FSBL/U-Boot packaging flow, then package the smoke bitstream into `BOOT.BIN` and boot the Zybo Z7-20 for the devmem readback test.
