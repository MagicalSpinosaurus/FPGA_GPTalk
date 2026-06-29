#ifndef GEMV_Q8_0_REF_H
#define GEMV_Q8_0_REF_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define GEMV_Q8_0_BLOCK_SIZE 32u
#define GEMV_Q8_0_SCALE_BYTES 2u

typedef struct gemv_q8_0_shape {
    size_t in_features;
    size_t out_features;
    size_t padded_out_features;
    size_t lanes;
    size_t blocks_per_row;
} gemv_q8_0_shape_t;

int gemv_q8_0_host_is_little_endian(void);
int gemv_q8_0_sanity_check_endian(void);

size_t gemv_q8_0_padded_rows(size_t out_features, size_t lanes);
size_t gemv_q8_0_weight_stream_elems(const gemv_q8_0_shape_t *shape);
size_t gemv_q8_0_scale_stream_elems(const gemv_q8_0_shape_t *shape);

float gemv_q8_0_f16_bits_to_f32(uint16_t half_bits);

int gemv_q8_0_validate_shape(
    const gemv_q8_0_shape_t *shape,
    char *error,
    size_t error_len
);

int gemv_q8_0_gemv_i32_from_lane_layout(
    const int16_t *input_i16,
    const int8_t *weight_lane_stream,
    const gemv_q8_0_shape_t *shape,
    int32_t *output_i32
);

int gemv_q8_0_gemv_f32_from_lane_layout(
    const int16_t *input_i16,
    const int8_t *weight_lane_stream,
    const uint16_t *scale_f16_le_stream,
    const gemv_q8_0_shape_t *shape,
    int32_t *output_i32_or_null,
    float *output_f32
);

#ifdef __cplusplus
}
#endif

#endif
