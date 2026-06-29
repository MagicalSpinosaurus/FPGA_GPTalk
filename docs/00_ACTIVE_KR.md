# Active 상태판

## 현재 단계

- 현재 단계: S01.5 완료, S02 bitstream build 대기
- Active Vivado project: `hw/vivado_project/GPTalk.xpr`
- Vivado GUI에서 열 파일: `hw/vivado_project/GPTalk.xpr`

## Active build script

- BD 생성/갱신: `scripts/create_or_update_gptalk_dma_bd.tcl`
- S02 bitstream/XSA build: `scripts/build_gptalk_dma_bitstream.tcl`
- 실패 report 수집: `scripts/report_failed_impl.tcl`

## 다음에 실행할 명령

S02에서 다음 명령을 실행한다.

```bash
nohup /tools/Xilinx/Vivado/2024.2/bin/vivado -mode batch \
  -source scripts/build_gptalk_dma_bitstream.tcl \
  > logs/gptalk_dma_build.log 2>&1 &
```

진행 확인:

```bash
tail -80 logs/gptalk_dma_build.log
```

문제 요약:

```bash
rg -n "ERROR|CRITICAL WARNING|FAIL|Timing constraints are not met|write_bitstream completed" logs/gptalk_dma_build.log
```

## 현재 bitstream/XSA

- GPTalk DMA bitstream: 아직 없음
- GPTalk DMA XSA: 아직 없음
- 예상 bitstream 위치: `hw/vivado_project/GPTalk.runs/impl_1/design_1_wrapper.bit`
- 예상 XSA 위치: `hw/vivado_project/export/GPTalk_dma.xsa`
- Vivado strategy 기록 위치: `logs/vivado_impl_strategy.txt`

## Active RTL

- `vivado_ip/rtl/gemv_q8_0_stream_core.v`
- `vivado_ip/rtl/gemv_q8_0_dma_top.v`
- `vivado_ip/rtl/gemv_q8_0_ctrl_axi_lite.v`

## Deprecated project

- `deprecated/vivado_projects/zybo_gemv_dma/zybo_gemv_dma.xpr`
- `deprecated/vivado_projects/zybo_gemv_smoke/zybo_gemv_smoke.xpr`
- `deprecated/vivado_projects/zybo_gemv_bringup/zybo_gemv_bringup.xpr`

`hw/` 아래 active `.xpr`는 `hw/vivado_project/GPTalk.xpr` 하나만 유지한다.

## 절대 사용 금지

- AXI-Lite `INPUT_DATA` 반복 write로 input vector 전송
- AXI-Lite `STREAM_DATA` 반복 write로 weight/scale stream 전송
- AXI-Lite `RESULT_DATA` 반복 read로 output vector 전송
- smoke register-only bitstream을 full GEMV bitstream으로 취급
- `gemv_q8_0_axi_lite.v`, `gemv_q8_0_axi_lite_smoke.v`를 active datapath로 복구
- mode=0 scaled output 제거
- mode=1 block_acc debug 제거
- lane 수 축소
- fake_gemv 전용 하드코딩 IP

## 마지막 PASS/FAIL 요약

- `scripts/run_gemv_sim.tcl`: PASS
- `scripts/create_or_update_gptalk_dma_bd.tcl`: PASS
- GPTalk 내부 BD validate: PASS
- GPTalk top: `design_1_wrapper`
- Address map: `logs/hw_dma_address_map.txt`
- S02 synthesis/implementation/bitstream/XSA: 아직 실행 안 함

## 사람이 볼 문서

- `README.md`
- `docs/VIVADO_GUI_KR.md`

## 내부 참고

- `docs/internal/interface_contract_dma.md`: C/RTL/DMA register 계약서
- `docs/internal/hw_dma_architecture.md`: DMA 구조 상세
- `docs/internal/hw_route_recovery.md`: timing/routing 복구 메모
- `prompts/`: Codex/agent용 단계 문서
