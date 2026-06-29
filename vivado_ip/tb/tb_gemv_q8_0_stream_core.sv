`timescale 1ns / 1ps

module tb_gemv_q8_0_stream_core;
    localparam integer LANES = 16;
    localparam integer Q8_BLOCK_SIZE = 32;
    localparam integer SCALE_SHIFT = 20;
    localparam integer IN_FEATURES = 32;
    localparam integer OUT_FEATURES = 3;
    localparam integer PADDED_OUT_FEATURES = 16;
    localparam integer BLOCKS_PER_ROW = IN_FEATURES / Q8_BLOCK_SIZE;
    localparam integer ROW_GROUPS = PADDED_OUT_FEATURES / LANES;
    localparam integer INPUT_BYTES = IN_FEATURES * 2;
    localparam integer WEIGHT_BYTES = PADDED_OUT_FEATURES * IN_FEATURES;
    localparam integer SCALE_BYTES = PADDED_OUT_FEATURES * BLOCKS_PER_ROW * 4;
    localparam integer SCALED_BYTES = OUT_FEATURES * 4;
    localparam integer BLOCK_ACC_BYTES = OUT_FEATURES * BLOCKS_PER_ROW * 4;
    localparam integer TIMEOUT_CYCLES = 20000;

    reg clk;
    reg reset_p;
    reg start;
    reg mode;
    reg [5:0] scale_shift;
    reg [31:0] in_features;
    reg [31:0] out_features;

    wire input_rd_en;
    wire [15:0] input_rd_addr;
    reg signed [15:0] input_rd_data;

    reg [31:0] s_axis_tdata;
    reg s_axis_tvalid;
    wire s_axis_tready;
    reg s_axis_tlast;

    wire signed [31:0] m_axis_tdata;
    wire m_axis_tvalid;
    reg m_axis_tready;
    wire m_axis_tlast;
    wire [31:0] m_axis_row;
    wire [31:0] m_axis_block;
    wire [15:0] m_axis_lane;

    wire busy;
    wire done;
    wire error;
    wire [7:0] error_code;
    wire [31:0] debug_row;
    wire [31:0] debug_block;
    wire [15:0] debug_lane;

    reg [7:0] input_bytes [0:INPUT_BYTES-1];
    reg [7:0] weight_bytes [0:WEIGHT_BYTES-1];
    reg [7:0] scale_bytes [0:SCALE_BYTES-1];
    reg [7:0] scaled_bytes [0:SCALED_BYTES-1];
    reg [7:0] block_acc_bytes [0:BLOCK_ACC_BYTES-1];

    reg signed [15:0] input_mem [0:IN_FEATURES-1];
    reg signed [31:0] scaled_ref [0:OUT_FEATURES-1];
    reg signed [31:0] block_acc_ref [0:(OUT_FEATURES*BLOCKS_PER_ROW)-1];

    string golden_dir;
    integer plusargs_ok;
    integer failure_count;
    integer output_count;
    integer expected_outputs;
    reg active;
    reg current_mode;

    gemv_q8_0_stream_core #(
        .LANES(LANES),
        .Q8_BLOCK_SIZE(Q8_BLOCK_SIZE),
        .TDATA_WIDTH(32),
        .INPUT_ADDR_WIDTH(16),
        .FEATURE_WIDTH(32),
        .SCALE_SHIFT_WIDTH(6),
        .SCALE_SHIFT_DEFAULT(SCALE_SHIFT),
        .ROW_ACC_WIDTH(64),
        .ROUND_ENABLE(1)
    ) dut (
        .clk(clk),
        .reset_p(reset_p),
        .start(start),
        .mode(mode),
        .scale_shift(scale_shift),
        .in_features(in_features),
        .out_features(out_features),
        .input_rd_en(input_rd_en),
        .input_rd_addr(input_rd_addr),
        .input_rd_data(input_rd_data),
        .s_axis_tdata(s_axis_tdata),
        .s_axis_tvalid(s_axis_tvalid),
        .s_axis_tready(s_axis_tready),
        .s_axis_tlast(s_axis_tlast),
        .m_axis_tdata(m_axis_tdata),
        .m_axis_tvalid(m_axis_tvalid),
        .m_axis_tready(m_axis_tready),
        .m_axis_tlast(m_axis_tlast),
        .m_axis_row(m_axis_row),
        .m_axis_block(m_axis_block),
        .m_axis_lane(m_axis_lane),
        .busy(busy),
        .done(done),
        .error(error),
        .error_code(error_code),
        .debug_row(debug_row),
        .debug_block(debug_block),
        .debug_lane(debug_lane)
    );

    initial begin
        clk = 1'b0;
        forever #5 clk = ~clk;
    end

    always @(posedge clk) begin
        if (reset_p) begin
            input_rd_data <= 16'sd0;
        end else if (input_rd_en) begin
            if (input_rd_addr < IN_FEATURES) begin
                input_rd_data <= input_mem[input_rd_addr];
            end else begin
                input_rd_data <= 16'sd0;
                failure_count <= failure_count + 1;
                $display("[FAIL] input_rd_addr out of range: %0d", input_rd_addr);
            end
        end
    end

    always @(posedge clk) begin
        if (!reset_p && active && m_axis_tvalid && m_axis_tready) begin
            check_output_word;
        end
    end

    function signed [15:0] le_i16_input;
        input integer byte_index;
        reg [15:0] bits;
        begin
            bits = {input_bytes[byte_index + 1], input_bytes[byte_index]};
            le_i16_input = $signed(bits);
        end
    endfunction

    function signed [31:0] le_i32_scale;
        input integer byte_index;
        reg [31:0] bits;
        begin
            bits = {scale_bytes[byte_index + 3], scale_bytes[byte_index + 2], scale_bytes[byte_index + 1], scale_bytes[byte_index]};
            le_i32_scale = $signed(bits);
        end
    endfunction

    function signed [31:0] le_i32_scaled_ref;
        input integer byte_index;
        reg [31:0] bits;
        begin
            bits = {scaled_bytes[byte_index + 3], scaled_bytes[byte_index + 2], scaled_bytes[byte_index + 1], scaled_bytes[byte_index]};
            le_i32_scaled_ref = $signed(bits);
        end
    endfunction

    function signed [31:0] le_i32_block_ref;
        input integer byte_index;
        reg [31:0] bits;
        begin
            bits = {block_acc_bytes[byte_index + 3], block_acc_bytes[byte_index + 2], block_acc_bytes[byte_index + 1], block_acc_bytes[byte_index]};
            le_i32_block_ref = $signed(bits);
        end
    endfunction

    function [31:0] packed_weight_word;
        input integer byte_index;
        begin
            packed_weight_word = {
                weight_bytes[byte_index + 3],
                weight_bytes[byte_index + 2],
                weight_bytes[byte_index + 1],
                weight_bytes[byte_index]
            };
        end
    endfunction

    task read_input_file;
        string path;
        integer fd;
        integer got;
        begin
            path = {golden_dir, "/input_i16.bin"};
            fd = $fopen(path, "rb");
            if (fd == 0) $fatal(1, "[FAIL] open %s", path);
            got = $fread(input_bytes, fd);
            $fclose(fd);
            if (got != INPUT_BYTES) $fatal(1, "[FAIL] fread %s got=%0d expected=%0d", path, got, INPUT_BYTES);
        end
    endtask

    task read_weight_file;
        string path;
        integer fd;
        integer got;
        begin
            path = {golden_dir, "/weight_q8_fpga_layout.bin"};
            fd = $fopen(path, "rb");
            if (fd == 0) $fatal(1, "[FAIL] open %s", path);
            got = $fread(weight_bytes, fd);
            $fclose(fd);
            if (got != WEIGHT_BYTES) $fatal(1, "[FAIL] fread %s got=%0d expected=%0d", path, got, WEIGHT_BYTES);
        end
    endtask

    task read_scale_file;
        string path;
        integer fd;
        integer got;
        begin
            path = {golden_dir, "/scale_q_i32.bin"};
            fd = $fopen(path, "rb");
            if (fd == 0) $fatal(1, "[FAIL] open %s", path);
            got = $fread(scale_bytes, fd);
            $fclose(fd);
            if (got != SCALE_BYTES) $fatal(1, "[FAIL] fread %s got=%0d expected=%0d", path, got, SCALE_BYTES);
        end
    endtask

    task read_scaled_ref_file;
        string path;
        integer fd;
        integer got;
        begin
            path = {golden_dir, "/output_scaled_ref_i32.bin"};
            fd = $fopen(path, "rb");
            if (fd == 0) $fatal(1, "[FAIL] open %s", path);
            got = $fread(scaled_bytes, fd);
            $fclose(fd);
            if (got != SCALED_BYTES) $fatal(1, "[FAIL] fread %s got=%0d expected=%0d", path, got, SCALED_BYTES);
        end
    endtask

    task read_block_ref_file;
        string path;
        integer fd;
        integer got;
        begin
            path = {golden_dir, "/output_block_acc_ref_i32.bin"};
            fd = $fopen(path, "rb");
            if (fd == 0) $fatal(1, "[FAIL] open %s", path);
            got = $fread(block_acc_bytes, fd);
            $fclose(fd);
            if (got != BLOCK_ACC_BYTES) $fatal(1, "[FAIL] fread %s got=%0d expected=%0d", path, got, BLOCK_ACC_BYTES);
        end
    endtask

    task load_golden;
        integer i;
        begin
            read_input_file;
            read_weight_file;
            read_scale_file;
            read_scaled_ref_file;
            read_block_ref_file;

            for (i = 0; i < IN_FEATURES; i = i + 1) begin
                input_mem[i] = le_i16_input(i * 2);
            end
            for (i = 0; i < OUT_FEATURES; i = i + 1) begin
                scaled_ref[i] = le_i32_scaled_ref(i * 4);
            end
            for (i = 0; i < OUT_FEATURES * BLOCKS_PER_ROW; i = i + 1) begin
                block_acc_ref[i] = le_i32_block_ref(i * 4);
            end
        end
    endtask

    task reset_dut;
        begin
            reset_p = 1'b1;
            start = 1'b0;
            mode = 1'b0;
            scale_shift = SCALE_SHIFT[5:0];
            in_features = IN_FEATURES;
            out_features = OUT_FEATURES;
            s_axis_tdata = 32'd0;
            s_axis_tvalid = 1'b0;
            s_axis_tlast = 1'b0;
            m_axis_tready = 1'b1;
            active = 1'b0;
            output_count = 0;
            expected_outputs = 0;
            repeat (5) @(posedge clk);
            reset_p = 1'b0;
            repeat (2) @(posedge clk);
        end
    endtask

    task send_word;
        input [31:0] data;
        input last;
        begin
            @(posedge clk);
            s_axis_tdata <= data;
            s_axis_tlast <= last;
            s_axis_tvalid <= 1'b1;
            @(posedge clk);
            while (!s_axis_tready) begin
                @(posedge clk);
            end
            s_axis_tvalid <= 1'b0;
            s_axis_tlast <= 1'b0;
            s_axis_tdata <= 32'd0;
        end
    endtask

    task drive_packet;
        integer group;
        integer block;
        integer lane;
        integer col;
        integer lane_base;
        integer scale_index;
        integer weight_index;
        reg last;
        begin
            for (group = 0; group < ROW_GROUPS; group = group + 1) begin
                for (block = 0; block < BLOCKS_PER_ROW; block = block + 1) begin
                    for (lane = 0; lane < LANES; lane = lane + 1) begin
                        scale_index = ((group * BLOCKS_PER_ROW + block) * LANES + lane) * 4;
                        send_word(le_i32_scale(scale_index), 1'b0);
                    end
                    for (col = 0; col < Q8_BLOCK_SIZE; col = col + 1) begin
                        for (lane_base = 0; lane_base < LANES; lane_base = lane_base + 4) begin
                            weight_index = (((group * BLOCKS_PER_ROW + block) * Q8_BLOCK_SIZE + col) * LANES) + lane_base;
                            last = (group == ROW_GROUPS - 1) &&
                                   (block == BLOCKS_PER_ROW - 1) &&
                                   (col == Q8_BLOCK_SIZE - 1) &&
                                   (lane_base == LANES - 4);
                            send_word(packed_weight_word(weight_index), last);
                        end
                    end
                end
            end
        end
    endtask

    task check_output_word;
        reg signed [31:0] expected;
        integer row;
        integer block;
        reg expected_last;
        begin
            row = m_axis_row;
            block = m_axis_block;
            expected_last = (output_count == expected_outputs - 1);

            if ($isunknown(m_axis_tdata) || $isunknown(m_axis_row) ||
                $isunknown(m_axis_block) || $isunknown(m_axis_lane) ||
                $isunknown(m_axis_tlast)) begin
                failure_count = failure_count + 1;
                $display("[FAIL] output contains X/Z");
            end

            if (m_axis_tlast !== expected_last) begin
                failure_count = failure_count + 1;
                $display("[FAIL] tlast mismatch output=%0d got=%0b expected=%0b", output_count, m_axis_tlast, expected_last);
            end

            if (current_mode == 1'b0) begin
                if (row < 0 || row >= OUT_FEATURES) begin
                    failure_count = failure_count + 1;
                    $display("[FAIL] scaled row out of range row=%0d", row);
                    expected = 32'sdx;
                end else begin
                    expected = scaled_ref[row];
                end
                if (m_axis_tdata !== expected) begin
                    failure_count = failure_count + 1;
                    $display("[FAIL] scaled mismatch row=%0d got=%0d expected=%0d", row, m_axis_tdata, expected);
                end
            end else begin
                if (row < 0 || row >= OUT_FEATURES || block < 0 || block >= BLOCKS_PER_ROW) begin
                    failure_count = failure_count + 1;
                    $display("[FAIL] block_acc index out of range row=%0d block=%0d", row, block);
                    expected = 32'sdx;
                end else begin
                    expected = block_acc_ref[row * BLOCKS_PER_ROW + block];
                end
                if (m_axis_tdata !== expected) begin
                    failure_count = failure_count + 1;
                    $display("[FAIL] block_acc mismatch row=%0d block=%0d lane=%0d got=%0d expected=%0d", row, block, m_axis_lane, m_axis_tdata, expected);
                end
            end

            output_count = output_count + 1;
        end
    endtask

    task run_mode;
        input mode_value;
        input [127:0] mode_name;
        integer cycle;
        integer start_failures;
        begin
            reset_dut;
            current_mode = mode_value;
            expected_outputs = mode_value ? (OUT_FEATURES * BLOCKS_PER_ROW) : OUT_FEATURES;
            start_failures = failure_count;
            active = 1'b1;

            @(posedge clk);
            mode <= mode_value;
            scale_shift <= SCALE_SHIFT[5:0];
            in_features <= IN_FEATURES;
            out_features <= OUT_FEATURES;
            start <= 1'b1;
            @(posedge clk);
            start <= 1'b0;

            drive_packet;

            cycle = 0;
            while (cycle < TIMEOUT_CYCLES && !done && !error) begin
                cycle = cycle + 1;
                @(posedge clk);
            end
            repeat (2) @(posedge clk);
            active = 1'b0;
            s_axis_tvalid <= 1'b0;
            s_axis_tlast <= 1'b0;

            if (cycle >= TIMEOUT_CYCLES) begin
                failure_count = failure_count + 1;
                $display("[FAIL] %0s timeout", mode_name);
            end
            if (error) begin
                failure_count = failure_count + 1;
                $display("[FAIL] %0s DUT error_code=%0d debug_row=%0d debug_block=%0d debug_lane=%0d", mode_name, error_code, debug_row, debug_block, debug_lane);
            end
            if (output_count != expected_outputs) begin
                failure_count = failure_count + 1;
                $display("[FAIL] %0s output count mismatch got=%0d expected=%0d", mode_name, output_count, expected_outputs);
            end

            if (failure_count == start_failures) begin
                $display("[PASS] %0s outputs matched golden/fake_gemv", mode_name);
            end
        end
    endtask

    initial begin
        golden_dir = "/home/user22/Desktop/smollm2-zybo/pycharm/golden/fake_gemv";
        plusargs_ok = $value$plusargs("GOLDEN_DIR=%s", golden_dir);
        failure_count = 0;
        output_count = 0;
        expected_outputs = 0;
        active = 1'b0;
        current_mode = 1'b0;
        load_golden;

        run_mode(1'b0, "mode=0 scaled");
        run_mode(1'b1, "mode=1 block-acc");

        if (failure_count == 0) begin
            $display("[PASS] tb_gemv_q8_0_stream_core completed");
            $finish;
        end else begin
            $fatal(1, "[FAIL] tb_gemv_q8_0_stream_core failures=%0d", failure_count);
        end
    end
endmodule
