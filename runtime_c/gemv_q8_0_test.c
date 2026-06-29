#include "gemv_q8_0_ref.h"

#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef enum test_mode {
    TEST_MODE_SCALED,
    TEST_MODE_BLOCK_ACC
} test_mode_t;

typedef struct cli_options {
    const char *case_dir;
    test_mode_t mode;
    int have_case;
    int have_mode;
} cli_options_t;

typedef struct file_blob {
    unsigned char *data;
    size_t size;
} file_blob_t;

typedef struct case_geometry {
    size_t lanes;
    size_t q8_block_size;
    size_t scale_shift;
    size_t in_features;
    size_t out_features;
    size_t padded_out_features;
    size_t q8_blocks_per_row;
} case_geometry_t;

typedef struct compare_i32_result {
    size_t mismatches;
    int64_t max_abs_diff;
    size_t first_index;
    int32_t first_got;
    int32_t first_ref;
} compare_i32_result_t;

static void usage(const char *argv0) {
    printf("Usage: %s --case PATH --mode scaled|block-acc\n", argv0);
}

static int parse_cli(int argc, char **argv, cli_options_t *options) {
    memset(options, 0, sizeof(*options));
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--help") == 0) {
            usage(argv[0]);
            exit(0);
        } else if (strcmp(argv[i], "--case") == 0 && i + 1 < argc) {
            options->case_dir = argv[++i];
            options->have_case = 1;
        } else if (strcmp(argv[i], "--mode") == 0 && i + 1 < argc) {
            const char *mode = argv[++i];
            if (strcmp(mode, "scaled") == 0) {
                options->mode = TEST_MODE_SCALED;
                options->have_mode = 1;
            } else if (strcmp(mode, "block-acc") == 0) {
                options->mode = TEST_MODE_BLOCK_ACC;
                options->have_mode = 1;
            } else {
                fprintf(stderr, "[FAIL] unknown --mode: %s\n", mode);
                return -1;
            }
        } else {
            fprintf(stderr, "[FAIL] unknown or incomplete option: %s\n", argv[i]);
            return -1;
        }
    }
    if (!options->have_case || !options->have_mode) {
        usage(argv[0]);
        return -1;
    }
    return 0;
}

static int join_path(char *out, size_t out_len, const char *dir, const char *name) {
    const int n = snprintf(out, out_len, "%s/%s", dir, name);
    if (n < 0 || (size_t)n >= out_len) {
        fprintf(stderr, "[FAIL] path too long: %s/%s\n", dir, name);
        return -1;
    }
    return 0;
}

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

static int32_t read_i32_le_bytes(const unsigned char *data) {
    return (int32_t)read_u32_le_bytes(data);
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
    values = (int16_t *)malloc(count * sizeof(*values));
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
    values = (int8_t *)malloc(count * sizeof(*values));
    if (values == NULL) {
        free_blob(&blob);
        return -3;
    }
    memcpy(values, blob.data, count);
    free_blob(&blob);
    *out = values;
    return 0;
}

static int load_i32_file(const char *path, size_t count, int32_t **out) {
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
    values = (int32_t *)malloc(count * sizeof(*values));
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
        fprintf(stderr, "[FAIL] manifest malformed field %s\n", field);
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

static int load_geometry(const char *case_dir, case_geometry_t *geometry) {
    char manifest_path[4096];
    memset(geometry, 0, sizeof(*geometry));
    if (join_path(manifest_path, sizeof(manifest_path), case_dir, "manifest.json") != 0) {
        return -1;
    }
    if (parse_json_size_field(manifest_path, "lanes", &geometry->lanes) != 0 ||
        parse_json_size_field(manifest_path, "q8_block_size", &geometry->q8_block_size) != 0 ||
        parse_json_size_field(manifest_path, "scale_shift", &geometry->scale_shift) != 0 ||
        parse_json_size_field(manifest_path, "in_features", &geometry->in_features) != 0 ||
        parse_json_size_field(manifest_path, "out_features", &geometry->out_features) != 0 ||
        parse_json_size_field(manifest_path, "padded_out_features", &geometry->padded_out_features) != 0 ||
        parse_json_size_field(manifest_path, "q8_blocks_per_row", &geometry->q8_blocks_per_row) != 0) {
        return -2;
    }
    if (geometry->q8_block_size != GEMV_Q8_0_BLOCK_SIZE) {
        fprintf(stderr, "[FAIL] q8_block_size=%zu expected=%u\n",
            geometry->q8_block_size,
            (unsigned)GEMV_Q8_0_BLOCK_SIZE);
        return -3;
    }
    if (geometry->lanes == 0u ||
        geometry->in_features == 0u ||
        geometry->out_features == 0u ||
        geometry->padded_out_features < geometry->out_features ||
        (geometry->padded_out_features % geometry->lanes) != 0u ||
        (geometry->in_features % geometry->q8_block_size) != 0u ||
        geometry->q8_blocks_per_row != (geometry->in_features / geometry->q8_block_size) ||
        geometry->scale_shift >= 63u) {
        fprintf(stderr, "[FAIL] invalid geometry in %s\n", manifest_path);
        return -4;
    }
    return 0;
}

static int64_t round_shift_signed_i64(int64_t value, size_t shift) {
    if (shift == 0u) {
        return value;
    }
    const int64_t rounding = (int64_t)1 << (shift - 1u);
    if (value >= 0) {
        return (value + rounding) >> shift;
    }
    return -(((-value) + rounding) >> shift);
}

static int compute_block_acc(
    const case_geometry_t *geometry,
    const int16_t *input_i16,
    const int8_t *weight_stream,
    int32_t *block_acc
) {
    const size_t row_groups = geometry->padded_out_features / geometry->lanes;
    memset(block_acc, 0, geometry->out_features * geometry->q8_blocks_per_row * sizeof(*block_acc));

    for (size_t group = 0u; group < row_groups; group++) {
        const size_t row_group = group * geometry->lanes;
        const size_t group_base = group * geometry->q8_blocks_per_row * geometry->q8_block_size * geometry->lanes;
        for (size_t block = 0u; block < geometry->q8_blocks_per_row; block++) {
            const size_t block_base = group_base + block * geometry->q8_block_size * geometry->lanes;
            for (size_t lane = 0u; lane < geometry->lanes; lane++) {
                const size_t row = row_group + lane;
                int32_t acc = 0;
                if (row >= geometry->out_features) {
                    continue;
                }
                for (size_t k = 0u; k < geometry->q8_block_size; k++) {
                    const size_t col = block * geometry->q8_block_size + k;
                    const size_t weight_index = block_base + k * geometry->lanes + lane;
                    acc += (int32_t)input_i16[col] * (int32_t)weight_stream[weight_index];
                }
                block_acc[row * geometry->q8_blocks_per_row + block] = acc;
            }
        }
    }
    return 0;
}

static int compute_scaled(
    const case_geometry_t *geometry,
    const int32_t *block_acc,
    const int32_t *scale_q_stream,
    int32_t *scaled
) {
    const size_t row_groups = geometry->padded_out_features / geometry->lanes;
    const int64_t min_i32 = (int64_t)(-2147483647 - 1);
    const int64_t max_i32 = (int64_t)2147483647;

    memset(scaled, 0, geometry->out_features * sizeof(*scaled));
    for (size_t group = 0u; group < row_groups; group++) {
        const size_t row_group = group * geometry->lanes;
        const size_t scale_group_base = group * geometry->q8_blocks_per_row * geometry->lanes;
        for (size_t lane = 0u; lane < geometry->lanes; lane++) {
            const size_t row = row_group + lane;
            int64_t sum = 0;
            int64_t rounded;
            if (row >= geometry->out_features) {
                continue;
            }
            for (size_t block = 0u; block < geometry->q8_blocks_per_row; block++) {
                const size_t scale_index = scale_group_base + block * geometry->lanes + lane;
                const size_t acc_index = row * geometry->q8_blocks_per_row + block;
                sum += (int64_t)block_acc[acc_index] * (int64_t)scale_q_stream[scale_index];
            }
            rounded = round_shift_signed_i64(sum, geometry->scale_shift);
            if (rounded < min_i32 || rounded > max_i32) {
                fprintf(stderr, "[FAIL] scaled output overflow row=%zu value=%lld\n",
                    row,
                    (long long)rounded);
                return -1;
            }
            scaled[row] = (int32_t)rounded;
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
        const int64_t diff = (int64_t)got[i] - (int64_t)ref[i];
        const int64_t abs_diff = diff < 0 ? -diff : diff;
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

static int print_fixed_float_report(
    const char *case_dir,
    const int32_t *block_acc,
    const int32_t *scale_q_stream,
    const case_geometry_t *geometry
) {
    char float_path[4096];
    file_blob_t blob;
    double max_abs = 0.0;
    double sum_abs = 0.0;

    if (join_path(float_path, sizeof(float_path), case_dir, "output_ref_float.bin") != 0) {
        return -1;
    }
    if (read_file_blob(float_path, &blob) != 0) {
        return -1;
    }
    if (blob.size != geometry->out_features * 4u) {
        fprintf(stderr, "[FAIL] %s size=%zu expected=%zu\n",
            float_path,
            blob.size,
            geometry->out_features * 4u);
        free_blob(&blob);
        return -1;
    }
    for (size_t i = 0u; i < geometry->out_features; i++) {
        const uint32_t bits = read_u32_le_bytes(&blob.data[i * 4u]);
        const size_t group = i / geometry->lanes;
        const size_t lane = i % geometry->lanes;
        const size_t scale_group_base = group * geometry->q8_blocks_per_row * geometry->lanes;
        int64_t fixed_acc = 0;
        float ref_float;
        float fixed_float;
        double abs_err;

        for (size_t block = 0u; block < geometry->q8_blocks_per_row; block++) {
            const size_t acc_index = i * geometry->q8_blocks_per_row + block;
            const size_t scale_index = scale_group_base + block * geometry->lanes + lane;
            fixed_acc += (int64_t)block_acc[acc_index] * (int64_t)scale_q_stream[scale_index];
        }
        memcpy(&ref_float, &bits, sizeof(ref_float));
        fixed_float = (float)ldexp((double)fixed_acc, -(int)geometry->scale_shift);
        abs_err = fabs((double)fixed_float - (double)ref_float);
        if (abs_err > max_abs) {
            max_abs = abs_err;
        }
        sum_abs += abs_err;
    }
    free_blob(&blob);
    printf("fixed-vs-float reference: max_abs_error=%.9g mean_abs_error=%.9g\n",
        max_abs,
        geometry->out_features == 0u ? 0.0 : sum_abs / (double)geometry->out_features);
    return 0;
}

int main(int argc, char **argv) {
    cli_options_t options;
    case_geometry_t geometry;
    char input_path[4096];
    char weight_path[4096];
    char scale_path[4096];
    char block_ref_path[4096];
    char scaled_ref_path[4096];
    int16_t *input_i16 = NULL;
    int8_t *weight_stream = NULL;
    int32_t *scale_q_stream = NULL;
    int32_t *block_acc = NULL;
    int32_t *scaled = NULL;
    int32_t *ref = NULL;
    compare_i32_result_t cmp;
    int rc = 1;

    if (parse_cli(argc, argv, &options) != 0) {
        return 2;
    }
    if (gemv_q8_0_sanity_check_endian() != 0) {
        fprintf(stderr, "[FAIL] endian/primitive-size sanity check failed\n");
        return 2;
    }
    if (load_geometry(options.case_dir, &geometry) != 0) {
        return 2;
    }
    if (join_path(input_path, sizeof(input_path), options.case_dir, "input_i16.bin") != 0 ||
        join_path(weight_path, sizeof(weight_path), options.case_dir, "weight_q8_fpga_layout.bin") != 0 ||
        join_path(scale_path, sizeof(scale_path), options.case_dir, "scale_q_i32.bin") != 0 ||
        join_path(block_ref_path, sizeof(block_ref_path), options.case_dir, "output_block_acc_ref_i32.bin") != 0 ||
        join_path(scaled_ref_path, sizeof(scaled_ref_path), options.case_dir, "output_scaled_ref_i32.bin") != 0) {
        return 2;
    }

    printf("GEMV Q8_0 fixed-scale C test\n");
    printf("case dir: %s\n", options.case_dir);
    printf("mode: %s\n", options.mode == TEST_MODE_SCALED ? "scaled" : "block-acc");
    printf("shape: out=%zu padded_out=%zu in=%zu lanes=%zu blocks_per_row=%zu scale_shift=%zu\n",
        geometry.out_features,
        geometry.padded_out_features,
        geometry.in_features,
        geometry.lanes,
        geometry.q8_blocks_per_row,
        geometry.scale_shift);

    if (load_i16_file(input_path, geometry.in_features, &input_i16) != 0 ||
        load_i8_file(weight_path, geometry.padded_out_features * geometry.in_features, &weight_stream) != 0 ||
        load_i32_file(scale_path, geometry.padded_out_features * geometry.q8_blocks_per_row, &scale_q_stream) != 0) {
        goto cleanup;
    }

    block_acc = (int32_t *)calloc(geometry.out_features * geometry.q8_blocks_per_row, sizeof(*block_acc));
    scaled = (int32_t *)calloc(geometry.out_features, sizeof(*scaled));
    if (block_acc == NULL || scaled == NULL) {
        fprintf(stderr, "[FAIL] output allocation failed\n");
        goto cleanup;
    }
    if (compute_block_acc(&geometry, input_i16, weight_stream, block_acc) != 0 ||
        compute_scaled(&geometry, block_acc, scale_q_stream, scaled) != 0) {
        goto cleanup;
    }

    if (options.mode == TEST_MODE_BLOCK_ACC) {
        const size_t count = geometry.out_features * geometry.q8_blocks_per_row;
        if (load_i32_file(block_ref_path, count, &ref) != 0) {
            goto cleanup;
        }
        cmp = compare_i32(block_acc, ref, count);
        printf("compare block_acc_i32: mismatches=%zu max_abs_diff=%lld\n",
            cmp.mismatches,
            (long long)cmp.max_abs_diff);
    } else {
        const size_t count = geometry.out_features;
        if (load_i32_file(scaled_ref_path, count, &ref) != 0) {
            goto cleanup;
        }
        cmp = compare_i32(scaled, ref, count);
        printf("compare scaled_output_i32: mismatches=%zu max_abs_diff=%lld\n",
            cmp.mismatches,
            (long long)cmp.max_abs_diff);
        if (print_fixed_float_report(options.case_dir, block_acc, scale_q_stream, &geometry) != 0) {
            goto cleanup;
        }
    }
    if (cmp.mismatches != 0u) {
        printf("  first mismatch index=%zu got=%d ref=%d\n",
            cmp.first_index,
            cmp.first_got,
            cmp.first_ref);
        printf("[FAIL] C reference differs from Python golden\n");
        rc = 1;
    } else {
        printf("[PASS] mismatch 0, max error %lld\n", (long long)cmp.max_abs_diff);
        rc = 0;
    }

cleanup:
    free(input_i16);
    free(weight_stream);
    free(scale_q_stream);
    free(block_acc);
    free(scaled);
    free(ref);
    return rc;
}
