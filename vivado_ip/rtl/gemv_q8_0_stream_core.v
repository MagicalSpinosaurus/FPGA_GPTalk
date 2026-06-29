`timescale 1ns / 1ps

// Q8_0 fixed-scale GEMV stream core.
//
// Input packet order per row group and Q8_0 block:
//   1. LANES signed int32 scale_q words, one per lane.
//   2. Q8_BLOCK_SIZE * LANES signed int8 weights.
//      Four consecutive weight bytes are packed little-endian into one
//      32-bit stream word: byte0=tdata[7:0], byte1=tdata[15:8], ...
//
// Output mode:
//   mode=0: emit one signed int32 row output per valid row.
//           block_acc_i32 * scale_q is rounded away from zero by default,
//           shifted by scale_shift, accumulated in ROW_ACC_WIDTH, then
//           saturated to signed int32 on output.
//   mode=1: emit signed int32 block_acc_i32 per valid row and block.
//
// No FP datapath is used. GGUF Q8_0 fp16 scales must be converted to
// signed int32 scale_q by the PC layout stage.

module gemv_q8_0_stream_core #(
    parameter integer LANES = 16,
    parameter integer Q8_BLOCK_SIZE = 32,
    parameter integer TDATA_WIDTH = 32,
    parameter integer INPUT_ADDR_WIDTH = 16,
    parameter integer FEATURE_WIDTH = 32,
    parameter integer SCALE_SHIFT_WIDTH = 6,
    parameter integer SCALE_SHIFT_DEFAULT = 20,
    parameter integer ROW_ACC_WIDTH = 64,
    parameter integer ROUND_ENABLE = 1
) (
    input wire clk,
    input wire reset_p,

    input wire start,
    input wire mode,
    input wire [SCALE_SHIFT_WIDTH-1:0] scale_shift,
    input wire [FEATURE_WIDTH-1:0] in_features,
    input wire [FEATURE_WIDTH-1:0] out_features,

    output reg input_rd_en,
    output reg [INPUT_ADDR_WIDTH-1:0] input_rd_addr,
    input wire signed [15:0] input_rd_data,

    input wire [TDATA_WIDTH-1:0] s_axis_tdata,
    input wire s_axis_tvalid,
    output reg s_axis_tready,
    input wire s_axis_tlast,

    output reg signed [31:0] m_axis_tdata,
    output reg m_axis_tvalid,
    input wire m_axis_tready,
    output reg m_axis_tlast,
    output reg [FEATURE_WIDTH-1:0] m_axis_row,
    output reg [FEATURE_WIDTH-1:0] m_axis_block,
    output reg [15:0] m_axis_lane,

    output reg busy,
    output reg done,
    output reg error,
    output reg [7:0] error_code,

    output reg [FEATURE_WIDTH-1:0] debug_row,
    output reg [FEATURE_WIDTH-1:0] debug_block,
    output reg [15:0] debug_lane
);

    localparam integer WEIGHTS_PER_WORD = TDATA_WIDTH / 8;
    localparam [FEATURE_WIDTH-1:0] FEATURE_ONE = {{(FEATURE_WIDTH-1){1'b0}}, 1'b1};
    localparam [FEATURE_WIDTH-1:0] LANES_FEATURE = LANES;
    localparam [15:0] WEIGHTS_PER_WORD_U16 = WEIGHTS_PER_WORD;

    localparam [3:0] ST_IDLE        = 4'd0;
    localparam [3:0] ST_SCALE       = 4'd1;
    localparam [3:0] ST_WEIGHT_RECV = 4'd2;
    localparam [3:0] ST_WEIGHT_APPLY= 4'd3;
    localparam [3:0] ST_INPUT_WAIT  = 4'd8;
    localparam [3:0] ST_BLOCK_DONE  = 4'd4;
    localparam [3:0] ST_AFTER_SCALE = 4'd5;
    localparam [3:0] ST_EMIT_ROW    = 4'd6;
    localparam [3:0] ST_EMIT_BLOCK  = 4'd7;
    localparam [3:0] ST_SCALE_MUL   = 4'd9;
    localparam [3:0] ST_SCALE_SHIFT = 4'd10;
    localparam [3:0] ST_SCALE_ACCUM = 4'd11;

    localparam [7:0] ERR_NONE       = 8'd0;
    localparam [7:0] ERR_CONFIG     = 8'd1;
    localparam [7:0] ERR_TLAST      = 8'd2;
    localparam [7:0] ERR_BUSY_START = 8'd3;

    reg [3:0] state;
    reg mode_reg;
    reg [SCALE_SHIFT_WIDTH-1:0] scale_shift_reg;
    reg [FEATURE_WIDTH-1:0] in_features_reg;
    reg [FEATURE_WIDTH-1:0] out_features_reg;
    reg [FEATURE_WIDTH-1:0] blocks_per_row_reg;
    reg [FEATURE_WIDTH-1:0] row_group_base;
    reg [FEATURE_WIDTH-1:0] block_index;

    reg [15:0] scale_lane;
    reg [15:0] weight_col;
    reg [15:0] weight_lane_base;
    reg [15:0] apply_lane_base;
    reg [15:0] emit_lane;
    reg [TDATA_WIDTH-1:0] weight_word_reg;

    reg signed [31:0] scale_q [0:LANES-1];
    reg signed [31:0] block_acc [0:LANES-1];
    reg signed [ROW_ACC_WIDTH-1:0] row_acc [0:LANES-1];
    reg signed [31:0] scale_q_stage [0:LANES-1];
    reg signed [31:0] block_acc_stage [0:LANES-1];
    reg signed [63:0] scaled_product [0:LANES-1];
    reg signed [63:0] scaled_shifted [0:LANES-1];

    integer i;
    integer b;

    wire last_group = (row_group_base + LANES_FEATURE >= out_features_reg);
    wire last_block = (block_index + FEATURE_ONE >= blocks_per_row_reg);
    wire last_weight_word_in_block =
        (weight_col == (Q8_BLOCK_SIZE - 1)) &&
        (weight_lane_base + WEIGHTS_PER_WORD >= LANES);
    wire final_input_word = last_group && last_block && last_weight_word_in_block;

    function signed [7:0] get_weight_byte;
        input [TDATA_WIDTH-1:0] word;
        input integer byte_index;
        begin
            case (byte_index)
                0: get_weight_byte = word[7:0];
                1: get_weight_byte = word[15:8];
                2: get_weight_byte = word[23:16];
                default: get_weight_byte = word[31:24];
            endcase
        end
    endfunction

    function signed [31:0] mul_i16_i8_to_i32;
        input signed [15:0] x;
        input signed [7:0] w;
        reg signed [31:0] x32;
        reg signed [31:0] w32;
        begin
            x32 = {{16{x[15]}}, x};
            w32 = {{24{w[7]}}, w};
            mul_i16_i8_to_i32 = x32 * w32;
        end
    endfunction

    function signed [63:0] mul_i32_i32_to_i64;
        input signed [31:0] a;
        input signed [31:0] b;
        reg signed [63:0] a64;
        reg signed [63:0] b64;
        begin
            a64 = {{32{a[31]}}, a};
            b64 = {{32{b[31]}}, b};
            mul_i32_i32_to_i64 = a64 * b64;
        end
    endfunction

    function signed [63:0] round_shift_i64;
        input signed [63:0] value;
        input [SCALE_SHIFT_WIDTH-1:0] shift;
        reg signed [63:0] rounding;
        reg signed [63:0] abs_value;
        begin
            if (shift == {SCALE_SHIFT_WIDTH{1'b0}}) begin
                round_shift_i64 = value;
            end else if (ROUND_ENABLE != 0) begin
                rounding = 64'sd1 <<< (shift - 1'b1);
                if (value >= 64'sd0) begin
                    round_shift_i64 = (value + rounding) >>> shift;
                end else begin
                    abs_value = -value;
                    round_shift_i64 = -((abs_value + rounding) >>> shift);
                end
            end else begin
                round_shift_i64 = value >>> shift;
            end
        end
    endfunction

    function signed [31:0] saturate_to_i32;
        input signed [ROW_ACC_WIDTH-1:0] value;
        reg signed [63:0] value64;
        begin
            value64 = value;
            if (value64 > 64'sd2147483647) begin
                saturate_to_i32 = 32'sh7fffffff;
            end else if (value64 < -64'sd2147483648) begin
                saturate_to_i32 = 32'sh80000000;
            end else begin
                saturate_to_i32 = value64[31:0];
            end
        end
    endfunction

    always @(posedge clk) begin
        if (reset_p) begin
            state <= ST_IDLE;
            mode_reg <= 1'b0;
            scale_shift_reg <= SCALE_SHIFT_DEFAULT;
            in_features_reg <= {FEATURE_WIDTH{1'b0}};
            out_features_reg <= {FEATURE_WIDTH{1'b0}};
            blocks_per_row_reg <= {FEATURE_WIDTH{1'b0}};
            row_group_base <= {FEATURE_WIDTH{1'b0}};
            block_index <= {FEATURE_WIDTH{1'b0}};
            scale_lane <= 16'd0;
            weight_col <= 16'd0;
            weight_lane_base <= 16'd0;
            apply_lane_base <= 16'd0;
            emit_lane <= 16'd0;
            weight_word_reg <= {TDATA_WIDTH{1'b0}};
            input_rd_en <= 1'b0;
            input_rd_addr <= {INPUT_ADDR_WIDTH{1'b0}};
            s_axis_tready <= 1'b0;
            m_axis_tdata <= 32'sd0;
            m_axis_tvalid <= 1'b0;
            m_axis_tlast <= 1'b0;
            m_axis_row <= {FEATURE_WIDTH{1'b0}};
            m_axis_block <= {FEATURE_WIDTH{1'b0}};
            m_axis_lane <= 16'd0;
            busy <= 1'b0;
            done <= 1'b0;
            error <= 1'b0;
            error_code <= ERR_NONE;
            debug_row <= {FEATURE_WIDTH{1'b0}};
            debug_block <= {FEATURE_WIDTH{1'b0}};
            debug_lane <= 16'd0;
            for (i = 0; i < LANES; i = i + 1) begin
                scale_q[i] <= 32'sd0;
                block_acc[i] <= 32'sd0;
                row_acc[i] <= {ROW_ACC_WIDTH{1'b0}};
                scale_q_stage[i] <= 32'sd0;
                block_acc_stage[i] <= 32'sd0;
                scaled_product[i] <= 64'sd0;
                scaled_shifted[i] <= 64'sd0;
            end
        end else begin
            done <= 1'b0;
            input_rd_en <= 1'b0;
            s_axis_tready <= 1'b0;

            case (state)
                ST_IDLE: begin
                    busy <= 1'b0;
                    m_axis_tvalid <= 1'b0;
                    m_axis_tlast <= 1'b0;
                    if (start) begin
                        error <= 1'b0;
                        error_code <= ERR_NONE;
                        if (busy) begin
                            error <= 1'b1;
                            error_code <= ERR_BUSY_START;
                        end else if (
                            in_features == {FEATURE_WIDTH{1'b0}} ||
                            out_features == {FEATURE_WIDTH{1'b0}} ||
                            (in_features % Q8_BLOCK_SIZE) != 0 ||
                            (LANES % WEIGHTS_PER_WORD) != 0 ||
                            scale_shift >= 6'd63
                        ) begin
                            error <= 1'b1;
                            error_code <= ERR_CONFIG;
                        end else begin
                            busy <= 1'b1;
                            mode_reg <= mode;
                            scale_shift_reg <= scale_shift;
                            in_features_reg <= in_features;
                            out_features_reg <= out_features;
                            blocks_per_row_reg <= in_features / Q8_BLOCK_SIZE;
                            row_group_base <= {FEATURE_WIDTH{1'b0}};
                            block_index <= {FEATURE_WIDTH{1'b0}};
                            scale_lane <= 16'd0;
                            weight_col <= 16'd0;
                            weight_lane_base <= 16'd0;
                            emit_lane <= 16'd0;
                            debug_row <= {FEATURE_WIDTH{1'b0}};
                            debug_block <= {FEATURE_WIDTH{1'b0}};
                            debug_lane <= 16'd0;
                            for (i = 0; i < LANES; i = i + 1) begin
                                scale_q[i] <= 32'sd0;
                                block_acc[i] <= 32'sd0;
                                row_acc[i] <= {ROW_ACC_WIDTH{1'b0}};
                            end
                            state <= ST_SCALE;
                        end
                    end
                end

                ST_SCALE: begin
                    s_axis_tready <= 1'b1;
                    debug_row <= row_group_base;
                    debug_block <= block_index;
                    debug_lane <= scale_lane;
                    if (s_axis_tvalid) begin
                        if (s_axis_tlast) begin
                            error <= 1'b1;
                            error_code <= ERR_TLAST;
                            busy <= 1'b0;
                            state <= ST_IDLE;
                        end else begin
                            scale_q[scale_lane] <= s_axis_tdata[31:0];
                            if (scale_lane == LANES - 1) begin
                                scale_lane <= 16'd0;
                                weight_col <= 16'd0;
                                weight_lane_base <= 16'd0;
                                for (i = 0; i < LANES; i = i + 1) begin
                                    block_acc[i] <= 32'sd0;
                                end
                                state <= ST_WEIGHT_RECV;
                            end else begin
                                scale_lane <= scale_lane + 16'd1;
                            end
                        end
                    end
                end

                ST_WEIGHT_RECV: begin
                    s_axis_tready <= 1'b1;
                    input_rd_en <= 1'b1;
                    input_rd_addr <= (block_index * Q8_BLOCK_SIZE) + weight_col;
                    debug_row <= row_group_base;
                    debug_block <= block_index;
                    debug_lane <= weight_lane_base;
                    if (s_axis_tvalid) begin
                        if (s_axis_tlast != final_input_word) begin
                            error <= 1'b1;
                            error_code <= ERR_TLAST;
                            busy <= 1'b0;
                            state <= ST_IDLE;
                        end else begin
                            weight_word_reg <= s_axis_tdata;
                            apply_lane_base <= weight_lane_base;
                            state <= ST_INPUT_WAIT;
                        end
                    end
                end


                ST_INPUT_WAIT: begin
                    input_rd_en <= 1'b1;
                    input_rd_addr <= (block_index * Q8_BLOCK_SIZE) + weight_col;
                    debug_row <= row_group_base;
                    debug_block <= block_index;
                    debug_lane <= apply_lane_base;
                    state <= ST_WEIGHT_APPLY;
                end

                ST_WEIGHT_APPLY: begin
                    debug_row <= row_group_base;
                    debug_block <= block_index;
                    debug_lane <= apply_lane_base;
                    for (b = 0; b < WEIGHTS_PER_WORD; b = b + 1) begin
                        if (apply_lane_base + b < LANES) begin
                            block_acc[apply_lane_base + b] <=
                                block_acc[apply_lane_base + b] +
                                mul_i16_i8_to_i32(input_rd_data, get_weight_byte(weight_word_reg, b));
                        end
                    end

                    if (last_weight_word_in_block) begin
                        weight_col <= 16'd0;
                        weight_lane_base <= 16'd0;
                        state <= ST_BLOCK_DONE;
                    end else if (weight_lane_base + WEIGHTS_PER_WORD >= LANES) begin
                        weight_lane_base <= 16'd0;
                        weight_col <= weight_col + 16'd1;
                        state <= ST_WEIGHT_RECV;
                    end else begin
                        weight_lane_base <= weight_lane_base + WEIGHTS_PER_WORD_U16;
                        state <= ST_WEIGHT_RECV;
                    end
                end

                ST_BLOCK_DONE: begin
                    debug_row <= row_group_base;
                    debug_block <= block_index;
                    debug_lane <= 16'd0;
                    if (mode_reg) begin
                        emit_lane <= 16'd0;
                        m_axis_tvalid <= 1'b0;
                        state <= ST_EMIT_BLOCK;
                    end else begin
                        for (i = 0; i < LANES; i = i + 1) begin
                            block_acc_stage[i] <= block_acc[i];
                            scale_q_stage[i] <= scale_q[i];
                        end
                        state <= ST_SCALE_MUL;
                    end
                end

                ST_SCALE_MUL: begin
                    debug_row <= row_group_base;
                    debug_block <= block_index;
                    debug_lane <= 16'd0;
                    for (i = 0; i < LANES; i = i + 1) begin
                        scaled_product[i] <= mul_i32_i32_to_i64(
                            block_acc_stage[i],
                            scale_q_stage[i]
                        );
                    end
                    state <= ST_SCALE_SHIFT;
                end

                ST_SCALE_SHIFT: begin
                    debug_row <= row_group_base;
                    debug_block <= block_index;
                    debug_lane <= 16'd0;
                    for (i = 0; i < LANES; i = i + 1) begin
                        scaled_shifted[i] <= round_shift_i64(
                            scaled_product[i],
                            scale_shift_reg
                        );
                    end
                    state <= ST_SCALE_ACCUM;
                end

                ST_SCALE_ACCUM: begin
                    debug_row <= row_group_base;
                    debug_block <= block_index;
                    debug_lane <= 16'd0;
                    for (i = 0; i < LANES; i = i + 1) begin
                        row_acc[i] <= row_acc[i] + scaled_shifted[i];
                    end
                    state <= ST_AFTER_SCALE;
                end

                ST_AFTER_SCALE: begin
                    if (last_block) begin
                        emit_lane <= 16'd0;
                        m_axis_tvalid <= 1'b0;
                        state <= ST_EMIT_ROW;
                    end else begin
                        block_index <= block_index + FEATURE_ONE;
                        scale_lane <= 16'd0;
                        weight_col <= 16'd0;
                        weight_lane_base <= 16'd0;
                        for (i = 0; i < LANES; i = i + 1) begin
                            scale_q[i] <= 32'sd0;
                            block_acc[i] <= 32'sd0;
                        end
                        state <= ST_SCALE;
                    end
                end

                ST_EMIT_ROW: begin
                    if (m_axis_tvalid) begin
                        if (m_axis_tready) begin
                            m_axis_tvalid <= 1'b0;
                            m_axis_tlast <= 1'b0;
                            emit_lane <= emit_lane + 16'd1;
                        end
                    end else if (emit_lane >= LANES) begin
                        if (last_group) begin
                            busy <= 1'b0;
                            done <= 1'b1;
                            state <= ST_IDLE;
                        end else begin
                            row_group_base <= row_group_base + LANES_FEATURE;
                            block_index <= {FEATURE_WIDTH{1'b0}};
                            scale_lane <= 16'd0;
                            weight_col <= 16'd0;
                            weight_lane_base <= 16'd0;
                            for (i = 0; i < LANES; i = i + 1) begin
                                scale_q[i] <= 32'sd0;
                                block_acc[i] <= 32'sd0;
                                row_acc[i] <= {ROW_ACC_WIDTH{1'b0}};
                            end
                            state <= ST_SCALE;
                        end
                    end else if (row_group_base + emit_lane >= out_features_reg) begin
                        emit_lane <= emit_lane + 16'd1;
                    end else begin
                        m_axis_tdata <= saturate_to_i32(row_acc[emit_lane]);
                        m_axis_tvalid <= 1'b1;
                        m_axis_tlast <= last_group &&
                            (row_group_base + emit_lane + 1'b1 >= out_features_reg);
                        m_axis_row <= row_group_base + emit_lane;
                        m_axis_block <= block_index;
                        m_axis_lane <= emit_lane;
                        debug_row <= row_group_base + emit_lane;
                        debug_block <= block_index;
                        debug_lane <= emit_lane;
                    end
                end

                ST_EMIT_BLOCK: begin
                    if (m_axis_tvalid) begin
                        if (m_axis_tready) begin
                            m_axis_tvalid <= 1'b0;
                            m_axis_tlast <= 1'b0;
                            emit_lane <= emit_lane + 16'd1;
                        end
                    end else if (emit_lane >= LANES) begin
                        if (last_block) begin
                            if (last_group) begin
                                busy <= 1'b0;
                                done <= 1'b1;
                                state <= ST_IDLE;
                            end else begin
                                row_group_base <= row_group_base + LANES_FEATURE;
                                block_index <= {FEATURE_WIDTH{1'b0}};
                                scale_lane <= 16'd0;
                                weight_col <= 16'd0;
                                weight_lane_base <= 16'd0;
                                for (i = 0; i < LANES; i = i + 1) begin
                                    scale_q[i] <= 32'sd0;
                                    block_acc[i] <= 32'sd0;
                                    row_acc[i] <= {ROW_ACC_WIDTH{1'b0}};
                                end
                                state <= ST_SCALE;
                            end
                        end else begin
                            block_index <= block_index + FEATURE_ONE;
                            scale_lane <= 16'd0;
                            weight_col <= 16'd0;
                            weight_lane_base <= 16'd0;
                            for (i = 0; i < LANES; i = i + 1) begin
                                scale_q[i] <= 32'sd0;
                                block_acc[i] <= 32'sd0;
                            end
                            state <= ST_SCALE;
                        end
                    end else if (row_group_base + emit_lane >= out_features_reg) begin
                        emit_lane <= emit_lane + 16'd1;
                    end else begin
                        m_axis_tdata <= block_acc[emit_lane];
                        m_axis_tvalid <= 1'b1;
                        m_axis_tlast <= last_group && last_block &&
                            (row_group_base + emit_lane + 1'b1 >= out_features_reg);
                        m_axis_row <= row_group_base + emit_lane;
                        m_axis_block <= block_index;
                        m_axis_lane <= emit_lane;
                        debug_row <= row_group_base + emit_lane;
                        debug_block <= block_index;
                        debug_lane <= emit_lane;
                    end
                end

                default: begin
                    error <= 1'b1;
                    error_code <= ERR_CONFIG;
                    busy <= 1'b0;
                    state <= ST_IDLE;
                end
            endcase
        end
    end

endmodule
