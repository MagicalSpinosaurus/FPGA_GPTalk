# RTL Simulation Howto: Q8_0 GEMV

Prompt 09 시뮬레이션은 보드 실행이 아니라 PC Vivado/XSim에서 RTL GEMV가 Python/C golden과 bit-exact로 맞는지 확인하는 단계다.

## 산출물 확인

저장소 루트에서 다음 파일이 있어야 한다.

```bash
cd /home/user22/Desktop/smollm2-zybo
ls vivado_ip/rtl/gemv_q8_0_stream_core.v
ls vivado_ip/tb/tb_gemv_q8_0_stream_core.v
ls scripts/run_gemv_sim.tcl
ls docs/rtl_sim_howto.md
```

`vivado_ip/tb/tb_gemv_q8_0_stream_core.v`는 실행 카드 호환용 wrapper이고, 실제 testbench 본문은 `vivado_ip/tb/tb_gemv_q8_0_stream_core.sv`에 있다. Vivado에서는 wrapper를 SystemVerilog file type으로 컴파일한다.

## 입력 Golden

기본 시뮬레이션은 다음 case를 사용한다.

```text
pycharm/golden/fake_gemv/
```

필수 파일:

```text
input_i16.bin
weight_q8_fpga_layout.bin
scale_q_i32.bin
output_scaled_ref_i32.bin
output_block_acc_ref_i32.bin
manifest.json
```

## 실행

Vivado가 PATH에 잡힌 터미널에서 실행한다.

```bash
cd /home/user22/Desktop/smollm2-zybo
vivado -version
vivado -mode batch -source scripts/run_gemv_sim.tcl
cat logs/gemv_sim_result.txt 2>/dev/null || true
```

현재 터미널에서 `vivado: command not found`가 나오면 Vivado 설치 경로의 `settings64.sh`를 먼저 source해야 한다. 예:

```bash
source /tools/Xilinx/Vivado/2024.2/settings64.sh
```

설치 경로는 PC마다 다를 수 있다.

## 통과 기준

testbench는 `fake_gemv`를 두 번 실행한다.

- `mode=0`: `output_scaled_ref_i32.bin`과 scaled output bit-exact 비교
- `mode=1`: `output_block_acc_ref_i32.bin`과 block_acc debug output bit-exact 비교

성공 시 로그에 다음 문구가 있어야 한다.

```text
[PASS] mode=0 scaled outputs matched golden/fake_gemv
[PASS] mode=1 block-acc outputs matched golden/fake_gemv
[PASS] tb_gemv_q8_0_stream_core completed
status: PASS
```

## Fixed-Point 정책

RTL은 `real`, `shortreal`, FP IP를 사용하지 않는다. GGUF Q8_0 fp16 scale은 PC 변환 단계에서 `scale_q_i32`로 변환되어 stream으로 들어온다.

계산 흐름:

```text
block_acc_i32[row][block] = sum(input_i16[col] * weight_i8[row][col])
scaled_block = round_away_from_zero((block_acc_i32 * scale_q_i32) / 2^scale_shift)
row_acc += scaled_block
output_i32 = saturate_i32(row_acc)
```

기본 `scale_shift`는 20이고, RTL parameter와 입력 register를 모두 지원한다.

## 실패 분석

- `mode=1 block-acc`부터 틀리면 weight packing, signed extension, endian, column/lane 순서를 먼저 확인한다.
- `mode=1`은 맞고 `mode=0`만 틀리면 `scale_q_i32` stream alignment, `scale_shift`, rounding 정책을 확인한다.
- `tlast` error가 나면 scale header 16 word와 weight payload `32 * 16 / 4 = 128` word의 packet 길이를 확인한다.
- output X/Z가 나오면 reset default assignment, BRAM read latency 모델, valid/ready stall 처리를 확인한다.

디버그 신호:

```text
debug_row
debug_block
debug_lane
busy
done
error
error_code
```
