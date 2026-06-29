#include "gemv_q8_0_ref.h"

#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define GEMV_Q8_0_DEFAULT_GOLDEN_DIR "golden/layer0_q_proj"
#define GEMV_Q8_0_DEFAULT_ATOL 0.002f
#define GEMV_Q8_0_DEFAULT_RTOL 0.0f

#ifndef GEMV_Q8_0_REF_NO_MAIN
typedef struct cli_options {
    const char *golden_dir;
    float atol;
    float rtol;
    int override_in_features;
    int override_out_features;
    int override_lanes;
    size_t in_features;
    size_t out_features;
    size_t lanes;
} cli_options_t;

typedef struct file_blob {
    unsigned char *data;
    size_t size;
} file_blob_t;

typedef struct compare_i32_result {
    size_t mismatches;
    int64_t max_abs_diff;
    size_t first_index;
    int32_t first_got;
    int32_t first_ref;
} compare_i32_result_t;

typedef struct compare_f32_result {
    size_t mismatches;
    float max_abs_diff;
    size_t first_index;
    float first_got;
    float first_ref;
} compare_f32_result_t;
#endif

static void set_error(char *error, size_t error_len, const char *message) {
    if (error != NULL && error_len > 0u) {
        snprintf(error, error_len, "%s", message);
    }
}

int gemv_q8_0_host_is_little_endian(void) {
    const uint16_t value = 0x0001u;
    return *((const unsigned char *)&value) == 0x01u;
}

static float f32_from_bits(uint32_t bits) {
    float value;
    memcpy(&value, &bits, sizeof(value));
    return value;
}

static uint16_t read_u16_le_bytes(const unsigned char *data) {
    return (uint16_t)((uint16_t)data[0] | ((uint16_t)data[1] << 8));
}

static int16_t read_i16_le_bytes(const unsigned char *data) {
    return (int16_t)read_u16_le_bytes(data);
}

static uint32_t read_u32_le_bytes(const unsigned char *data) {
    return (uint32_t)data[0]
        | ((uint32_t)data[1] << 8)
        | ((uint32_t)data[2] << 16)
        | ((uint32_t)data[3] << 24);
}

#ifndef GEMV_Q8_0_REF_NO_MAIN
static int32_t read_i32_le_bytes(const unsigned char *data) {
    return (int32_t)read_u32_le_bytes(data);
}
#endif

static float read_f32_le_bytes(const unsigned char *data) {
    return f32_from_bits(read_u32_le_bytes(data));
}

int gemv_q8_0_sanity_check_endian(void) {
    const unsigned char u16_one_le[2] = {0x01u, 0x00u};
    const unsigned char i16_neg2_le[2] = {0xfeu, 0xffu};
    const unsigned char u32_one_float_le[4] = {0x00u, 0x00u, 0x80u, 0x3fu};

    if (sizeof(int8_t) != 1u || sizeof(int16_t) != 2u || sizeof(int32_t) != 4u) {
        return -1;
    }
    if (sizeof(float) != 4u) {
        return -2;
    }
    if (read_u16_le_bytes(u16_one_le) != 1u) {
        return -3;
    }
    if (read_i16_le_bytes(i16_neg2_le) != (int16_t)-2) {
        return -4;
    }
    if (read_f32_le_bytes(u32_one_float_le) != 1.0f) {
        return -5;
    }
    if (gemv_q8_0_f16_bits_to_f32(0x3c00u) != 1.0f) {
        return -6;
    }
    if (gemv_q8_0_f16_bits_to_f32(0xbc00u) != -1.0f) {
        return -7;
    }
    if (gemv_q8_0_f16_bits_to_f32(0x0000u) != 0.0f) {
        return -8;
    }
    return 0;
}

size_t gemv_q8_0_padded_rows(size_t out_features, size_t lanes) {
    if (lanes == 0u) {
        return 0u;
    }
    return ((out_features + lanes - 1u) / lanes) * lanes;
}

size_t gemv_q8_0_weight_stream_elems(const gemv_q8_0_shape_t *shape) {
    if (shape == NULL) {
        return 0u;
    }
    return shape->padded_out_features * shape->in_features;
}

size_t gemv_q8_0_scale_stream_elems(const gemv_q8_0_shape_t *shape) {
    if (shape == NULL) {
        return 0u;
    }
    return shape->padded_out_features * shape->blocks_per_row;
}

float gemv_q8_0_f16_bits_to_f32(uint16_t half_bits) {
    const uint32_t sign = ((uint32_t)half_bits & 0x8000u) << 16;
    uint32_t exp = ((uint32_t)half_bits >> 10) & 0x1fu;
    uint32_t frac = (uint32_t)half_bits & 0x03ffu;
    uint32_t bits;

    if (exp == 0u) {
        if (frac == 0u) {
            bits = sign;
        } else {
            int exp32 = 127 - 14;
            while ((frac & 0x0400u) == 0u) {
                frac <<= 1;
                exp32--;
            }
            frac &= 0x03ffu;
            bits = sign | ((uint32_t)exp32 << 23) | (frac << 13);
        }
    } else if (exp == 0x1fu) {
        bits = sign | 0x7f800000u | (frac << 13);
    } else {
        exp = exp + (127u - 15u);
        bits = sign | (exp << 23) | (frac << 13);
    }

    return f32_from_bits(bits);
}

int gemv_q8_0_validate_shape(
    const gemv_q8_0_shape_t *shape,
    char *error,
    size_t error_len
) {
    if (shape == NULL) {
        set_error(error, error_len, "shape is NULL");
        return -1;
    }
    if (shape->lanes == 0u) {
        set_error(error, error_len, "lanes must be non-zero");
        return -2;
    }
    if (shape->in_features == 0u || shape->out_features == 0u) {
        set_error(error, error_len, "in_features and out_features must be non-zero");
        return -3;
    }
    if ((shape->in_features % GEMV_Q8_0_BLOCK_SIZE) != 0u) {
        set_error(error, error_len, "in_features must be divisible by Q8_0 block size");
        return -4;
    }
    if (shape->blocks_per_row != (shape->in_features / GEMV_Q8_0_BLOCK_SIZE)) {
        set_error(error, error_len, "blocks_per_row does not match in_features / 32");
        return -5;
    }
    if (shape->padded_out_features < shape->out_features) {
        set_error(error, error_len, "padded_out_features is smaller than out_features");
        return -6;
    }
    if ((shape->padded_out_features % shape->lanes) != 0u) {
        set_error(error, error_len, "padded_out_features must be divisible by lanes");
        return -7;
    }
    return 0;
}

int gemv_q8_0_gemv_i32_from_lane_layout(
    const int16_t *input_i16,
    const int8_t *weight_lane_stream,
    const gemv_q8_0_shape_t *shape,
    int32_t *output_i32
) {
    char error[128];
    size_t row_group;

    if (input_i16 == NULL || weight_lane_stream == NULL || output_i32 == NULL) {
        return -1;
    }
    if (gemv_q8_0_validate_shape(shape, error, sizeof(error)) != 0) {
        return -2;
    }

    for (size_t row = 0u; row < shape->out_features; row++) {
        output_i32[row] = 0;
    }

    for (row_group = 0u; row_group < shape->padded_out_features; row_group += shape->lanes) {
        const size_t group_index = row_group / shape->lanes;
        const size_t group_base = group_index * shape->in_features * shape->lanes;

        for (size_t col = 0u; col < shape->in_features; col++) {
            const int32_t x = (int32_t)input_i16[col];
            const size_t lane_base = group_base + col * shape->lanes;

            for (size_t lane = 0u; lane < shape->lanes; lane++) {
                const size_t row = row_group + lane;
                if (row < shape->out_features) {
                    const int32_t w = (int32_t)weight_lane_stream[lane_base + lane];
                    output_i32[row] += x * w;
                }
            }
        }
    }

    return 0;
}

int gemv_q8_0_gemv_f32_from_lane_layout(
    const int16_t *input_i16,
    const int8_t *weight_lane_stream,
    const uint16_t *scale_f16_le_stream,
    const gemv_q8_0_shape_t *shape,
    int32_t *output_i32_or_null,
    float *output_f32
) {
    char error[128];

    if (input_i16 == NULL || weight_lane_stream == NULL || scale_f16_le_stream == NULL || output_f32 == NULL) {
        return -1;
    }
    if (gemv_q8_0_validate_shape(shape, error, sizeof(error)) != 0) {
        return -2;
    }

    for (size_t row = 0u; row < shape->out_features; row++) {
        output_f32[row] = 0.0f;
        if (output_i32_or_null != NULL) {
            output_i32_or_null[row] = 0;
        }
    }

    for (size_t row_group = 0u; row_group < shape->padded_out_features; row_group += shape->lanes) {
        const size_t group_index = row_group / shape->lanes;
        const size_t weight_group_base = group_index * shape->in_features * shape->lanes;
        const size_t scale_group_base = group_index * shape->blocks_per_row * shape->lanes;

        for (size_t lane = 0u; lane < shape->lanes; lane++) {
            const size_t row = row_group + lane;
            if (row >= shape->out_features) {
                continue;
            }

            for (size_t block = 0u; block < shape->blocks_per_row; block++) {
                int32_t block_acc = 0;
                const size_t col_start = block * GEMV_Q8_0_BLOCK_SIZE;

                for (size_t k = 0u; k < GEMV_Q8_0_BLOCK_SIZE; k++) {
                    const size_t col = col_start + k;
                    const size_t weight_index = weight_group_base + col * shape->lanes + lane;
                    block_acc += (int32_t)input_i16[col] * (int32_t)weight_lane_stream[weight_index];
                }

                if (output_i32_or_null != NULL) {
                    output_i32_or_null[row] += block_acc;
                }
                {
                    const size_t scale_index = scale_group_base + block * shape->lanes + lane;
                    const float scale = gemv_q8_0_f16_bits_to_f32(scale_f16_le_stream[scale_index]);
                    output_f32[row] += (float)block_acc * scale;
                }
            }
        }
    }

    return 0;
}

#ifndef GEMV_Q8_0_REF_NO_MAIN
static void free_blob(file_blob_t *blob) {
    if (blob != NULL) {
        free(blob->data);
        blob->data = NULL;
        blob->size = 0u;
    }
}

static int read_file_blob(const char *path, file_blob_t *blob) {
    FILE *file;
    long file_size;
    size_t got;

    blob->data = NULL;
    blob->size = 0u;

    file = fopen(path, "rb");
    if (file == NULL) {
        fprintf(stderr, "[FAIL] open %s: %s\n", path, strerror(errno));
        return -1;
    }
    if (fseek(file, 0L, SEEK_END) != 0) {
        fprintf(stderr, "[FAIL] fseek %s\n", path);
        fclose(file);
        return -2;
    }
    file_size = ftell(file);
    if (file_size < 0L) {
        fprintf(stderr, "[FAIL] ftell %s\n", path);
        fclose(file);
        return -3;
    }
    if (fseek(file, 0L, SEEK_SET) != 0) {
        fprintf(stderr, "[FAIL] rewind %s\n", path);
        fclose(file);
        return -4;
    }

    blob->data = (unsigned char *)malloc((size_t)file_size == 0u ? 1u : (size_t)file_size);
    if (blob->data == NULL) {
        fprintf(stderr, "[FAIL] malloc %ld bytes for %s\n", file_size, path);
        fclose(file);
        return -5;
    }
    blob->size = (size_t)file_size;
    got = fread(blob->data, 1u, blob->size, file);
    fclose(file);
    if (got != blob->size) {
        fprintf(stderr, "[FAIL] fread %s: got %zu expected %zu\n", path, got, blob->size);
        free_blob(blob);
        return -6;
    }
    return 0;
}

static int join_path(char *out, size_t out_len, const char *dir, const char *name) {
    int n;
    if (dir == NULL || name == NULL || out == NULL || out_len == 0u) {
        return -1;
    }
    n = snprintf(out, out_len, "%s/%s", dir, name);
    if (n < 0 || (size_t)n >= out_len) {
        return -2;
    }
    return 0;
}

static int load_i16_file(const char *path, size_t count, int16_t **out) {
    file_blob_t blob;
    int16_t *values;
    if (read_file_blob(path, &blob) != 0) {
        return -1;
    }
    if (blob.size != count * 2u) {
        fprintf(stderr, "[FAIL] %s size=%zu expected=%zu\n", path, blob.size, count * 2u);
        free_blob(&blob);
        return -2;
    }
    values = (int16_t *)malloc(count * sizeof(int16_t));
    if (values == NULL) {
        free_blob(&blob);
        return -3;
    }
    for (size_t i = 0u; i < count; i++) {
        values[i] = read_i16_le_bytes(&blob.data[i * 2u]);
    }
    free_blob(&blob);
    *out = values;
    return 0;
}

static int load_i8_file(const char *path, size_t count, int8_t **out) {
    file_blob_t blob;
    int8_t *values;
    if (read_file_blob(path, &blob) != 0) {
        return -1;
    }
    if (blob.size != count) {
        fprintf(stderr, "[FAIL] %s size=%zu expected=%zu\n", path, blob.size, count);
        free_blob(&blob);
        return -2;
    }
    values = (int8_t *)malloc(count * sizeof(int8_t));
    if (values == NULL) {
        free_blob(&blob);
        return -3;
    }
    memcpy(values, blob.data, count);
    free_blob(&blob);
    *out = values;
    return 0;
}

static int load_u16_le_file(const char *path, size_t count, uint16_t **out) {
    file_blob_t blob;
    uint16_t *values;
    if (read_file_blob(path, &blob) != 0) {
        return -1;
    }
    if (blob.size != count * 2u) {
        fprintf(stderr, "[FAIL] %s size=%zu expected=%zu\n", path, blob.size, count * 2u);
        free_blob(&blob);
        return -2;
    }
    values = (uint16_t *)malloc(count * sizeof(uint16_t));
    if (values == NULL) {
        free_blob(&blob);
        return -3;
    }
    for (size_t i = 0u; i < count; i++) {
        values[i] = read_u16_le_bytes(&blob.data[i * 2u]);
    }
    free_blob(&blob);
    *out = values;
    return 0;
}

static int load_i32_le_file(const char *path, size_t count, int32_t **out) {
    file_blob_t blob;
    int32_t *values;
    if (read_file_blob(path, &blob) != 0) {
        return -1;
    }
    if (blob.size != count * 4u) {
        fprintf(stderr, "[FAIL] %s size=%zu expected=%zu\n", path, blob.size, count * 4u);
        free_blob(&blob);
        return -2;
    }
    values = (int32_t *)malloc(count * sizeof(int32_t));
    if (values == NULL) {
        free_blob(&blob);
        return -3;
    }
    for (size_t i = 0u; i < count; i++) {
        values[i] = read_i32_le_bytes(&blob.data[i * 4u]);
    }
    free_blob(&blob);
    *out = values;
    return 0;
}

static int load_f32_le_file(const char *path, size_t count, float **out) {
    file_blob_t blob;
    float *values;
    if (read_file_blob(path, &blob) != 0) {
        return -1;
    }
    if (blob.size != count * 4u) {
        fprintf(stderr, "[FAIL] %s size=%zu expected=%zu\n", path, blob.size, count * 4u);
        free_blob(&blob);
        return -2;
    }
    values = (float *)malloc(count * sizeof(float));
    if (values == NULL) {
        free_blob(&blob);
        return -3;
    }
    for (size_t i = 0u; i < count; i++) {
        values[i] = read_f32_le_bytes(&blob.data[i * 4u]);
    }
    free_blob(&blob);
    *out = values;
    return 0;
}

static int parse_json_size_field(const char *manifest_path, const char *field, size_t *value) {
    file_blob_t blob;
    char needle[128];
    char *text;
    char *pos;
    char *colon;
    char *endptr;
    unsigned long long parsed;

    if (snprintf(needle, sizeof(needle), "\"%s\"", field) >= (int)sizeof(needle)) {
        return -1;
    }
    if (read_file_blob(manifest_path, &blob) != 0) {
        return -2;
    }
    text = (char *)malloc(blob.size + 1u);
    if (text == NULL) {
        free_blob(&blob);
        return -3;
    }
    memcpy(text, blob.data, blob.size);
    text[blob.size] = '\0';
    free_blob(&blob);

    pos = strstr(text, needle);
    if (pos == NULL) {
        fprintf(stderr, "[FAIL] manifest missing field %s\n", field);
        free(text);
        return -4;
    }
    colon = strchr(pos + strlen(needle), ':');
    if (colon == NULL) {
        free(text);
        return -5;
    }
    errno = 0;
    parsed = strtoull(colon + 1, &endptr, 10);
    if (errno != 0 || endptr == colon + 1) {
        fprintf(stderr, "[FAIL] manifest field %s is not an integer\n", field);
        free(text);
        return -6;
    }
    free(text);
    *value = (size_t)parsed;
    return 0;
}

static int parse_size_arg(const char *text, size_t *value) {
    char *endptr;
    unsigned long long parsed;
    if (text == NULL || value == NULL) {
        return -1;
    }
    errno = 0;
    parsed = strtoull(text, &endptr, 10);
    if (errno != 0 || endptr == text || *endptr != '\0') {
        return -2;
    }
    *value = (size_t)parsed;
    return 0;
}

static int parse_float_arg(const char *text, float *value) {
    char *endptr;
    float parsed;
    if (text == NULL || value == NULL) {
        return -1;
    }
    errno = 0;
    parsed = strtof(text, &endptr);
    if (errno != 0 || endptr == text || *endptr != '\0') {
        return -2;
    }
    *value = parsed;
    return 0;
}

static void usage(const char *argv0) {
    printf("Usage: %s [options]\n", argv0);
    printf("\n");
    printf("Options:\n");
    printf("  --golden-dir PATH    Default: %s\n", GEMV_Q8_0_DEFAULT_GOLDEN_DIR);
    printf("  --in-features N      Override manifest in_features\n");
    printf("  --out-features N     Override manifest out_features\n");
    printf("  --lanes N            Override manifest lanes\n");
    printf("  --atol FLOAT         Float absolute tolerance. Default: %.6g\n", GEMV_Q8_0_DEFAULT_ATOL);
    printf("  --rtol FLOAT         Float relative tolerance. Default: %.6g\n", GEMV_Q8_0_DEFAULT_RTOL);
    printf("  --help               Show this help\n");
}

static int parse_cli(int argc, char **argv, cli_options_t *options) {
    options->golden_dir = GEMV_Q8_0_DEFAULT_GOLDEN_DIR;
    options->atol = GEMV_Q8_0_DEFAULT_ATOL;
    options->rtol = GEMV_Q8_0_DEFAULT_RTOL;
    options->override_in_features = 0;
    options->override_out_features = 0;
    options->override_lanes = 0;
    options->in_features = 0u;
    options->out_features = 0u;
    options->lanes = 0u;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--help") == 0) {
            usage(argv[0]);
            exit(0);
        } else if (strcmp(argv[i], "--golden-dir") == 0 && i + 1 < argc) {
            options->golden_dir = argv[++i];
        } else if (strcmp(argv[i], "--in-features") == 0 && i + 1 < argc) {
            if (parse_size_arg(argv[++i], &options->in_features) != 0) {
                fprintf(stderr, "[FAIL] invalid --in-features\n");
                return -1;
            }
            options->override_in_features = 1;
        } else if (strcmp(argv[i], "--out-features") == 0 && i + 1 < argc) {
            if (parse_size_arg(argv[++i], &options->out_features) != 0) {
                fprintf(stderr, "[FAIL] invalid --out-features\n");
                return -1;
            }
            options->override_out_features = 1;
        } else if (strcmp(argv[i], "--lanes") == 0 && i + 1 < argc) {
            if (parse_size_arg(argv[++i], &options->lanes) != 0) {
                fprintf(stderr, "[FAIL] invalid --lanes\n");
                return -1;
            }
            options->override_lanes = 1;
        } else if (strcmp(argv[i], "--atol") == 0 && i + 1 < argc) {
            if (parse_float_arg(argv[++i], &options->atol) != 0) {
                fprintf(stderr, "[FAIL] invalid --atol\n");
                return -1;
            }
        } else if (strcmp(argv[i], "--rtol") == 0 && i + 1 < argc) {
            if (parse_float_arg(argv[++i], &options->rtol) != 0) {
                fprintf(stderr, "[FAIL] invalid --rtol\n");
                return -1;
            }
        } else {
            fprintf(stderr, "[FAIL] unknown or incomplete option: %s\n", argv[i]);
            usage(argv[0]);
            return -1;
        }
    }
    return 0;
}

static compare_i32_result_t compare_i32(const int32_t *got, const int32_t *ref, size_t count) {
    compare_i32_result_t result;
    result.mismatches = 0u;
    result.max_abs_diff = 0;
    result.first_index = 0u;
    result.first_got = 0;
    result.first_ref = 0;

    for (size_t i = 0u; i < count; i++) {
        int64_t diff = (int64_t)got[i] - (int64_t)ref[i];
        int64_t abs_diff = diff < 0 ? -diff : diff;
        if (abs_diff > result.max_abs_diff) {
            result.max_abs_diff = abs_diff;
        }
        if (got[i] != ref[i]) {
            if (result.mismatches == 0u) {
                result.first_index = i;
                result.first_got = got[i];
                result.first_ref = ref[i];
            }
            result.mismatches++;
        }
    }
    return result;
}

static compare_f32_result_t compare_f32(
    const float *got,
    const float *ref,
    size_t count,
    float atol,
    float rtol
) {
    compare_f32_result_t result;
    result.mismatches = 0u;
    result.max_abs_diff = 0.0f;
    result.first_index = 0u;
    result.first_got = 0.0f;
    result.first_ref = 0.0f;

    for (size_t i = 0u; i < count; i++) {
        const float diff = fabsf(got[i] - ref[i]);
        const float limit = atol + rtol * fabsf(ref[i]);
        if (diff > result.max_abs_diff) {
            result.max_abs_diff = diff;
        }
        if (!(diff <= limit)) {
            if (result.mismatches == 0u) {
                result.first_index = i;
                result.first_got = got[i];
                result.first_ref = ref[i];
            }
            result.mismatches++;
        }
    }
    return result;
}

int main(int argc, char **argv) {
    cli_options_t options;
    gemv_q8_0_shape_t shape;
    char manifest_path[4096];
    char input_path[4096];
    char weight_path[4096];
    char scale_path[4096];
    char ref_i32_path[4096];
    char ref_f32_path[4096];
    char error[160];
    int16_t *input_i16 = NULL;
    int8_t *weight_stream = NULL;
    uint16_t *scale_stream = NULL;
    int32_t *ref_i32 = NULL;
    float *ref_f32 = NULL;
    int32_t *out_i32_plain = NULL;
    int32_t *out_i32_scaled_path = NULL;
    float *out_f32 = NULL;
    compare_i32_result_t cmp_i32_plain;
    compare_i32_result_t cmp_i32_scaled_path;
    compare_f32_result_t cmp_f32;
    int rc = 1;

    if (parse_cli(argc, argv, &options) != 0) {
        return 2;
    }
    if (gemv_q8_0_sanity_check_endian() != 0) {
        fprintf(stderr, "[FAIL] endian/primitive-size sanity check failed\n");
        return 2;
    }

    if (join_path(manifest_path, sizeof(manifest_path), options.golden_dir, "manifest.json") != 0 ||
        join_path(input_path, sizeof(input_path), options.golden_dir, "input_i16.bin") != 0 ||
        join_path(weight_path, sizeof(weight_path), options.golden_dir, "weight_q8_fpga_layout.bin") != 0 ||
        join_path(scale_path, sizeof(scale_path), options.golden_dir, "scale.bin") != 0 ||
        join_path(ref_i32_path, sizeof(ref_i32_path), options.golden_dir, "output_ref_i32.bin") != 0 ||
        join_path(ref_f32_path, sizeof(ref_f32_path), options.golden_dir, "output_ref_float.bin") != 0) {
        fprintf(stderr, "[FAIL] path is too long\n");
        return 2;
    }

    if (!options.override_in_features &&
        parse_json_size_field(manifest_path, "in_features", &options.in_features) != 0) {
        return 2;
    }
    if (!options.override_out_features &&
        parse_json_size_field(manifest_path, "out_features", &options.out_features) != 0) {
        return 2;
    }
    if (!options.override_lanes &&
        parse_json_size_field(manifest_path, "lanes", &options.lanes) != 0) {
        return 2;
    }

    shape.in_features = options.in_features;
    shape.out_features = options.out_features;
    shape.lanes = options.lanes;
    shape.padded_out_features = gemv_q8_0_padded_rows(shape.out_features, shape.lanes);
    shape.blocks_per_row = shape.in_features / GEMV_Q8_0_BLOCK_SIZE;

    if (gemv_q8_0_validate_shape(&shape, error, sizeof(error)) != 0) {
        fprintf(stderr, "[FAIL] invalid shape: %s\n", error);
        return 2;
    }

    printf("GEMV Q8_0 C reference\n");
    printf("golden dir: %s\n", options.golden_dir);
    printf("host endian: %s\n", gemv_q8_0_host_is_little_endian() ? "little" : "big");
    printf("shape: out=%zu padded_out=%zu in=%zu lanes=%zu blocks_per_row=%zu\n",
        shape.out_features,
        shape.padded_out_features,
        shape.in_features,
        shape.lanes,
        shape.blocks_per_row);
    printf("tolerance: atol=%g rtol=%g\n", options.atol, options.rtol);

    if (load_i16_file(input_path, shape.in_features, &input_i16) != 0 ||
        load_i8_file(weight_path, gemv_q8_0_weight_stream_elems(&shape), &weight_stream) != 0 ||
        load_u16_le_file(scale_path, gemv_q8_0_scale_stream_elems(&shape), &scale_stream) != 0 ||
        load_i32_le_file(ref_i32_path, shape.out_features, &ref_i32) != 0 ||
        load_f32_le_file(ref_f32_path, shape.out_features, &ref_f32) != 0) {
        goto cleanup;
    }

    out_i32_plain = (int32_t *)calloc(shape.out_features, sizeof(int32_t));
    out_i32_scaled_path = (int32_t *)calloc(shape.out_features, sizeof(int32_t));
    out_f32 = (float *)calloc(shape.out_features, sizeof(float));
    if (out_i32_plain == NULL || out_i32_scaled_path == NULL || out_f32 == NULL) {
        fprintf(stderr, "[FAIL] output allocation failed\n");
        goto cleanup;
    }

    if (gemv_q8_0_gemv_i32_from_lane_layout(input_i16, weight_stream, &shape, out_i32_plain) != 0) {
        fprintf(stderr, "[FAIL] int32 GEMV path failed\n");
        goto cleanup;
    }
    if (gemv_q8_0_gemv_f32_from_lane_layout(
            input_i16,
            weight_stream,
            scale_stream,
            &shape,
            out_i32_scaled_path,
            out_f32) != 0) {
        fprintf(stderr, "[FAIL] scaled GEMV path failed\n");
        goto cleanup;
    }

    cmp_i32_plain = compare_i32(out_i32_plain, ref_i32, shape.out_features);
    cmp_i32_scaled_path = compare_i32(out_i32_scaled_path, ref_i32, shape.out_features);
    cmp_f32 = compare_f32(out_f32, ref_f32, shape.out_features, options.atol, options.rtol);

    printf("compare int32 acc path: mismatches=%zu max_abs_diff=%lld\n",
        cmp_i32_plain.mismatches,
        (long long)cmp_i32_plain.max_abs_diff);
    if (cmp_i32_plain.mismatches != 0u) {
        printf("  first mismatch index=%zu got=%d ref=%d\n",
            cmp_i32_plain.first_index,
            cmp_i32_plain.first_got,
            cmp_i32_plain.first_ref);
    }

    printf("compare scaled-path int32 acc: mismatches=%zu max_abs_diff=%lld\n",
        cmp_i32_scaled_path.mismatches,
        (long long)cmp_i32_scaled_path.max_abs_diff);
    if (cmp_i32_scaled_path.mismatches != 0u) {
        printf("  first mismatch index=%zu got=%d ref=%d\n",
            cmp_i32_scaled_path.first_index,
            cmp_i32_scaled_path.first_got,
            cmp_i32_scaled_path.first_ref);
    }

    printf("compare float dequant path: mismatches=%zu max_abs_diff=%g\n",
        cmp_f32.mismatches,
        cmp_f32.max_abs_diff);
    if (cmp_f32.mismatches != 0u) {
        printf("  first mismatch index=%zu got=%g ref=%g allowed=%g\n",
            cmp_f32.first_index,
            cmp_f32.first_got,
            cmp_f32.first_ref,
            options.atol + options.rtol * fabsf(cmp_f32.first_ref));
    }

    if (cmp_i32_plain.mismatches == 0u &&
        cmp_i32_scaled_path.mismatches == 0u &&
        cmp_f32.mismatches == 0u) {
        printf("[OK] C reference matches golden outputs\n");
        rc = 0;
    } else {
        printf("[FAIL] C reference differs from golden outputs\n");
        rc = 1;
    }

cleanup:
    free(input_i16);
    free(weight_stream);
    free(scale_stream);
    free(ref_i32);
    free(ref_f32);
    free(out_i32_plain);
    free(out_i32_scaled_path);
    free(out_f32);
    return rc;
}
#endif
