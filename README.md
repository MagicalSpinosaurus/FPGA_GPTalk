# SmolLM2 Zybo Z7-20

SmolLM2-135M-Instruct Q8_0 모델의 GEMV 연산을 Zybo Z7-20 / Zynq-7020 FPGA로 offload하기 위한 프로젝트입니다. 현재 하드웨어 경로는 AXI DMA + AXI-Stream GEMV + PS DDR/BRAM 구성을 기준으로 합니다.

## Active Vivado Project

- Active project: `hw/vivado_project/GPTalk.xpr`
- 사용자는 Vivado GUI에서 이 파일만 열면 됩니다.
- 새 Vivado `.xpr` 프로젝트를 만들지 않습니다.

## Vivado GUI

Vivado에서 확인할 때:

1. Vivado 실행
2. Open Project
3. `hw/vivado_project/GPTalk.xpr` 열기
4. Flow Navigator에서 Open Block Design

상세 GUI 절차는 `docs/VIVADO_GUI_KR.md`를 봅니다.

## Final Demo Command

최종 목표 데모 명령은 다음입니다.

```bash
./smollm2_chat \
  --model /mnt/sd/SmolLM2-135M-Instruct-Q8_0.gguf \
  --backend fpga \
  --require-fpga \
  --max-new-tokens 16
```

최종 성공 조건은 모든 Q8_0 GEMV가 FPGA backend를 타고 CPU fallback이 0인 것입니다.

```text
total_gemv_calls: N
fpga_gemv_calls: N
cpu_gemv_fallbacks: 0
```

## 주요 폴더

```text
hw/                 Vivado active project
vivado_ip/rtl/      GEMV RTL
vivado_ip/tb/       RTL testbench
scripts/            자동화 Tcl 및 검증 스크립트
runtime_c/          C reference/runtime
pycharm/            Python 도구, golden data, 모델 분석 산출물
fpga_layout/        Q8_0 lane16 layout 산출물
docs/               사람이 읽는 현재 문서
docs/internal/      내부 설계 메모와 과거 문서
prompts/            Codex/agent용 단계 문서
logs/               실행 로그
reports/            Vivado report 출력 위치
deprecated/         active가 아닌 과거 프로젝트/RTL/Tcl 보관
```

## 사람이 읽을 문서

- `docs/00_ACTIVE_KR.md`: 현재 상태판과 다음 명령
- `docs/VIVADO_GUI_KR.md`: Vivado GUI 작업 순서

Tcl과 agent prompt는 재현성과 자동화용입니다. 사용자가 일반적으로 읽을 주 문서는 위 두 개입니다.
