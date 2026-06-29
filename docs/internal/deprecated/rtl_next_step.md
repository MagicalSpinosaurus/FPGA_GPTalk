# RTL Next Step: Q8_0 Block Accumulator GEMV

## 목표

Prompt 09 이전 단계의 RTL 목표는 Q8_0 weight를 받아 최종 float output을 직접 만드는 것이 아니라, block 단위 정수 누산 결과인 `block_acc_i32[row][block]`를 정확히 출력하는 GEMV v1을 만드는 것이다. Q8_0 scale 적용과 최종 float 합산은 CPU runtime이 담당한다.

## Q8_0 block 구조

GGUF Q8_0 weight block은 32개 weight마다 하나의 scale을 갖는다.

```text
block:
  scale: fp16, 2 bytes
  qs:    int8[32], 32 bytes
```

논리 행렬 관점에서는 한 output row가 여러 Q8_0 block으로 나뉜다.

```text
blocks_per_row = in_features / 32
weight[row][block][k] : int8, k=0..31
scale[row][block]     : fp16
```

## 단일 int32 acc가 실제 출력이 아닌 이유

Q8_0의 scale은 row당 하나가 아니라 `row, block`마다 하나다. 따라서 row 전체에 대해 다음 값만 계산하면:

```text
sum_block sum_k(input[block*32+k] * weight[row][block][k])
```

block별 scale 정보를 잃는다. 실제 float 출력은 다음처럼 block 누산값에 각 block scale을 곱한 뒤 합산해야 한다.

```text
block_acc_i32[row][block] =
  sum_k(input_i16[block*32+k] * weight_i8[row][block][k])

output_float[row] =
  sum_block(float(block_acc_i32[row][block]) * fp16_to_float(scale[row][block]))
```

즉 `output_ref_i32` 같은 row당 단일 int32 값은 unscaled debug/reference 값일 수는 있지만 Q8_0의 실제 linear output은 아니다.

## v1 RTL 출력형

FPGA v1 GEMV 출력은 다음 형태로 고정한다.

```text
block_acc_i32[row][block]
```

RTL은 int8 weight와 int16 input을 곱해 32개 column 단위로 int32 누산한다. scale stream은 RTL v1에서 곱하지 않는다. 이 정책은 float/fp16 처리와 scale ordering 문제를 CPU runtime으로 밀어 RTL의 첫 검증 범위를 좁힌다.

## CPU runtime scale 적용 흐름

CPU runtime은 FPGA output stream과 scale stream을 같은 row/block 순서로 소비한다.

```text
for row in rows:
    acc_f32 = 0.0
    for block in blocks_per_row:
        acc_f32 += float(block_acc_i32[row][block]) * f16_to_f32(scale[row][block])
    output[row] = acc_f32
```

현재 golden에는 `block_acc_i32.bin`과 `scale.bin`이 모두 들어 있다. C reference는 이 둘을 이용해 `output_ref_float.bin`과 비교한다.

## 검증 순서

1. 1-lane RTL GEMV
   - 한 row씩 처리한다.
   - `fake_gemv`로 row padding과 block 순서부터 검증한다.
   - `block_acc_i32.bin`과 cycle/order가 맞는지 확인한다.

2. 4-lane RTL GEMV
   - row group과 lane ordering을 검증한다.
   - lane별 accumulator reset, valid 유지, 마지막 row group padding을 확인한다.
   - 기존 layout writer를 `--lanes 4`로 재생성할 golden case가 필요하다.

3. 16-lane RTL GEMV
   - 현재 `q8_0_lane16` layout과 맞춘다.
   - `layer0_q_proj`, `layer0_gate_proj`, `lm_head_slice` 순서로 크기를 늘린다.
   - 최종 RTL testbench는 `block_acc_i32` exact match를 성공 기준으로 둔다.

## 필요한 golden 추가 생성 계획

현재 golden은 lane16 중심이다. RTL bring-up을 위해 다음 산출물을 추가로 만든다.

- `golden_lane1/` 또는 `golden/lane1/...`: 1-lane stream order용 `fake_gemv`, `layer0_q_proj`
- `golden_lane4/` 또는 `golden/lane4/...`: 4-lane row group 검증용 `fake_gemv`, `layer0_q_proj`
- 각 lane 설정별 manifest에 `lanes`, `q8_0_blocks_per_row`, `block_acc_i32` shape, scale order를 명시
- RTL testbench 입력용 짧은 case: `out_features <= 5`, `in_features = 64`의 synthetic Q8_0 case 유지
- 실제 tensor smoke case: `layer0_q_proj` 전체 row 또는 작은 row slice

기존 `golden/`과 C reference는 삭제하지 않고 유지한다. 추가 golden은 lane별 디렉터리로 분리해 기존 lane16 산출물과 혼동하지 않게 한다.
