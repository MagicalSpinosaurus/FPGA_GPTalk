# Vivado GUI 가이드

이 문서는 사람이 Vivado GUI에서 active 프로젝트를 열고 확인하는 절차만 다룬다.

## 1. Vivado 실행

터미널 또는 애플리케이션 메뉴에서 Vivado 2024.2를 실행한다.

```bash
/tools/Xilinx/Vivado/2024.2/bin/vivado
```

## 2. Open Project

Vivado 시작 화면에서:

1. `Open Project` 선택
2. 프로젝트 파일로 이동
3. `hw/vivado_project/GPTalk.xpr` 선택
4. `Open` 클릭

다른 `.xpr` 프로젝트는 열지 않는다.

## 3. Open Block Design

프로젝트가 열린 뒤:

1. 왼쪽 `Flow Navigator` 확인
2. `IP INTEGRATOR`
3. `Open Block Design`
4. `design_1` 선택

확인할 핵심 블록:

- `processing_system7_0`
- `axi_dma_0`
- `mm2s_axis_fifo`
- `s2mm_axis_fifo`
- `gemv_q8_0_dma_top_0`
- `axi_input_bram_ctrl`
- `input_vector_bram`

확인할 핵심 연결:

- `axi_dma_0/M_AXIS_MM2S` -> `mm2s_axis_fifo` -> `gemv_q8_0_dma_top_0/S_AXIS`
- `gemv_q8_0_dma_top_0/M_AXIS` -> `s2mm_axis_fifo` -> `axi_dma_0/S_AXIS_S2MM`
- DMA memory-mapped master 경로가 PS `S_AXI_HP0`로 연결

## 4. Validate Design

Block Design 화면에서:

1. 상단 toolbar의 `Validate Design` 클릭
2. 결과 창에서 error가 없는지 확인

현재 알려진 warning:

- Digilent/Zynq PS board preset의 DDR DQS 관련 critical warning
- BRAM address lower-bit 연결 warning

위 warning은 S01.5 기준 DMA 연결 실패로 취급하지 않는다. Error가 있으면 중단하고 로그를 확인한다.

## 5. Run Synthesis

왼쪽 `Flow Navigator`에서:

1. `SYNTHESIS`
2. `Run Synthesis`
3. 완료될 때까지 대기
4. 완료 후 timing/utilization summary를 확인

## 6. Run Implementation

Synthesis 완료 후:

1. `IMPLEMENTATION`
2. `Run Implementation`
3. 완료될 때까지 대기
4. timing summary에서 WNS/TNS를 확인

## 7. Generate Bitstream

Implementation 완료 후:

1. `PROGRAM AND DEBUG`
2. `Generate Bitstream`
3. 완료 후 bitstream 파일 위치를 확인

예상 위치:

```text
hw/vivado_project/GPTalk.runs/impl_1/design_1_wrapper.bit
```

## 8. Export Hardware / XSA Export

Bitstream 생성 후:

1. 메뉴 `File`
2. `Export`
3. `Export Hardware`
4. `Include bitstream` 선택
5. 출력 위치 확인

권장 XSA 위치:

```text
hw/vivado_project/export/GPTalk_dma.xsa
```

## 9. 자동화 대체 방법

GUI 대신 재현성용 Tcl을 사용할 수 있다.

BD 생성/갱신:

```bash
/tools/Xilinx/Vivado/2024.2/bin/vivado -mode batch \
  -source scripts/create_or_update_gptalk_dma_bd.tcl \
  > logs/gptalk_dma_bd_update.log 2>&1
```

S02 bitstream/XSA build:

```bash
nohup /tools/Xilinx/Vivado/2024.2/bin/vivado -mode batch \
  -source scripts/build_gptalk_dma_bitstream.tcl \
  > logs/gptalk_dma_build.log 2>&1 &
```

Tcl은 자동화와 재현성용이다. 사람이 프로젝트를 확인할 때는 `hw/vivado_project/GPTalk.xpr`를 GUI로 열면 된다.
