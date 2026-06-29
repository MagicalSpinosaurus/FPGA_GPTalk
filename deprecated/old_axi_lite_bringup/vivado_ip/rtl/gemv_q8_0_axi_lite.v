`timescale 1ns / 1ps

// AXI-Lite bring-up wrapper for gemv_q8_0_stream_core.
//
// This wrapper keeps the proven GEMV datapath intact and exposes a small
// register file for Linux/devmem bring-up on Zynq. Input vector samples are
// written into a local register RAM. Q8_0 scale/weight stream words are pushed
// through a MMIO stream-data register after software polls stream_ready.

module gemv_q8_0_axi_lite #(
    parameter integer C_S_AXI_DATA_WIDTH = 32,
    parameter integer C_S_AXI_ADDR_WIDTH = 12,
    parameter integer LANES = 16,
    parameter integer Q8_BLOCK_SIZE = 32,
    parameter integer INPUT_MEM_DEPTH = 1024,
    parameter integer RESULT_DEPTH = 256,
    parameter [31:0] VERSION = 32'h0009_0001
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 S_AXI_ACLK CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF S_AXI, ASSOCIATED_RESET S_AXI_ARESETN, FREQ_HZ 100000000" *)
    input wire S_AXI_ACLK,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 S_AXI_ARESETN RST" *)
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input wire S_AXI_ARESETN,

    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXI, DATA_WIDTH 32, PROTOCOL AXI4LITE, FREQ_HZ 100000000, ADDR_WIDTH 12, HAS_BURST 0, HAS_LOCK 0, HAS_PROT 1, HAS_CACHE 0, HAS_QOS 0, HAS_REGION 0, SUPPORTS_NARROW_BURST 0, NUM_READ_OUTSTANDING 1, NUM_WRITE_OUTSTANDING 1" *)
    input wire [C_S_AXI_ADDR_WIDTH-1:0] S_AXI_AWADDR,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWPROT" *)
    input wire [2:0] S_AXI_AWPROT,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *)
    input wire S_AXI_AWVALID,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *)
    output wire S_AXI_AWREADY,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *)
    input wire [C_S_AXI_DATA_WIDTH-1:0] S_AXI_WDATA,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *)
    input wire [(C_S_AXI_DATA_WIDTH/8)-1:0] S_AXI_WSTRB,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *)
    input wire S_AXI_WVALID,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *)
    output wire S_AXI_WREADY,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *)
    output wire [1:0] S_AXI_BRESP,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *)
    output wire S_AXI_BVALID,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *)
    input wire S_AXI_BREADY,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *)
    input wire [C_S_AXI_ADDR_WIDTH-1:0] S_AXI_ARADDR,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARPROT" *)
    input wire [2:0] S_AXI_ARPROT,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *)
    input wire S_AXI_ARVALID,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *)
    output wire S_AXI_ARREADY,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *)
    output wire [C_S_AXI_DATA_WIDTH-1:0] S_AXI_RDATA,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *)
    output wire [1:0] S_AXI_RRESP,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *)
    output wire S_AXI_RVALID,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *)
    input wire S_AXI_RREADY
);

    localparam integer ADDR_LSB = 2;
    localparam integer OPT_MEM_ADDR_BITS = 7;

    localparam [7:0] REG_VERSION      = 8'h00;
    localparam [7:0] REG_CONTROL      = 8'h01;
    localparam [7:0] REG_STATUS       = 8'h02;
    localparam [7:0] REG_ERROR_CODE   = 8'h03;
    localparam [7:0] REG_SCALE_SHIFT  = 8'h04;
    localparam [7:0] REG_IN_FEATURES  = 8'h05;
    localparam [7:0] REG_OUT_FEATURES = 8'h06;
    localparam [7:0] REG_DEBUG_ROW    = 8'h07;
    localparam [7:0] REG_DEBUG_BLOCK  = 8'h08;
    localparam [7:0] REG_DEBUG_LANE   = 8'h09;
    localparam [7:0] REG_INPUT_ADDR   = 8'h0a;
    localparam [7:0] REG_INPUT_DATA   = 8'h0b;
    localparam [7:0] REG_STREAM_DATA  = 8'h0c;
    localparam [7:0] REG_STREAM_LAST  = 8'h0d;
    localparam [7:0] REG_RESULT_COUNT = 8'h0e;
    localparam [7:0] REG_RESULT_ADDR  = 8'h0f;
    localparam [7:0] REG_RESULT_DATA  = 8'h10;
    localparam [7:0] REG_RESULT_ROW   = 8'h11;
    localparam [7:0] REG_RESULT_BLOCK = 8'h12;
    localparam [7:0] REG_RESULT_LANE  = 8'h13;
    localparam [7:0] REG_RESULT_LAST  = 8'h14;

    reg [C_S_AXI_ADDR_WIDTH-1:0] axi_awaddr;
    reg axi_awready;
    reg axi_wready;
    reg [1:0] axi_bresp;
    reg axi_bvalid;
    reg [C_S_AXI_ADDR_WIDTH-1:0] axi_araddr;
    reg axi_arready;
    reg [C_S_AXI_DATA_WIDTH-1:0] axi_rdata;
    reg [1:0] axi_rresp;
    reg axi_rvalid;
    reg aw_en;

    assign S_AXI_AWREADY = axi_awready;
    assign S_AXI_WREADY = axi_wready;
    assign S_AXI_BRESP = axi_bresp;
    assign S_AXI_BVALID = axi_bvalid;
    assign S_AXI_ARREADY = axi_arready;
    assign S_AXI_RDATA = axi_rdata;
    assign S_AXI_RRESP = axi_rresp;
    assign S_AXI_RVALID = axi_rvalid;

    wire slv_reg_wren = axi_wready && S_AXI_WVALID && axi_awready && S_AXI_AWVALID;
    wire slv_reg_rden = axi_arready && S_AXI_ARVALID && !axi_rvalid;
    wire [7:0] wr_addr = axi_awaddr[ADDR_LSB + OPT_MEM_ADDR_BITS:ADDR_LSB];
    wire [7:0] rd_addr = axi_araddr[ADDR_LSB + OPT_MEM_ADDR_BITS:ADDR_LSB];

    reg mode_reg;
    reg [5:0] scale_shift_reg;
    reg [31:0] in_features_reg;
    reg [31:0] out_features_reg;
    reg [31:0] input_addr_reg;
    reg [31:0] result_addr_reg;
    reg stream_last_reg;
    reg done_sticky;
    reg stream_write_error;
    reg input_addr_error;
    reg result_overflow;

    reg core_start;
    wire core_reset_p = !S_AXI_ARESETN;

    wire input_rd_en;
    wire [15:0] input_rd_addr;
    reg signed [15:0] input_rd_data;

    reg [31:0] core_s_axis_tdata;
    reg core_s_axis_tvalid;
    wire core_s_axis_tready;
    reg core_s_axis_tlast;

    wire signed [31:0] core_m_axis_tdata;
    wire core_m_axis_tvalid;
    wire core_m_axis_tlast;
    wire [31:0] core_m_axis_row;
    wire [31:0] core_m_axis_block;
    wire [15:0] core_m_axis_lane;

    wire core_busy;
    wire core_done;
    wire core_error;
    wire [7:0] core_error_code;
    wire [31:0] core_debug_row;
    wire [31:0] core_debug_block;
    wire [15:0] core_debug_lane;

    reg signed [15:0] input_mem [0:INPUT_MEM_DEPTH-1];
    reg signed [31:0] result_data_mem [0:RESULT_DEPTH-1];
    reg [31:0] result_row_mem [0:RESULT_DEPTH-1];
    reg [31:0] result_block_mem [0:RESULT_DEPTH-1];
    reg [15:0] result_lane_mem [0:RESULT_DEPTH-1];
    reg result_last_mem [0:RESULT_DEPTH-1];
    reg [31:0] result_count;

    integer i;

    gemv_q8_0_stream_core #(
        .LANES(LANES),
        .Q8_BLOCK_SIZE(Q8_BLOCK_SIZE),
        .TDATA_WIDTH(32),
        .INPUT_ADDR_WIDTH(16),
        .FEATURE_WIDTH(32),
        .SCALE_SHIFT_WIDTH(6),
        .SCALE_SHIFT_DEFAULT(20),
        .ROW_ACC_WIDTH(64),
        .ROUND_ENABLE(1)
    ) core (
        .clk(S_AXI_ACLK),
        .reset_p(core_reset_p),
        .start(core_start),
        .mode(mode_reg),
        .scale_shift(scale_shift_reg),
        .in_features(in_features_reg),
        .out_features(out_features_reg),
        .input_rd_en(input_rd_en),
        .input_rd_addr(input_rd_addr),
        .input_rd_data(input_rd_data),
        .s_axis_tdata(core_s_axis_tdata),
        .s_axis_tvalid(core_s_axis_tvalid),
        .s_axis_tready(core_s_axis_tready),
        .s_axis_tlast(core_s_axis_tlast),
        .m_axis_tdata(core_m_axis_tdata),
        .m_axis_tvalid(core_m_axis_tvalid),
        .m_axis_tready(1'b1),
        .m_axis_tlast(core_m_axis_tlast),
        .m_axis_row(core_m_axis_row),
        .m_axis_block(core_m_axis_block),
        .m_axis_lane(core_m_axis_lane),
        .busy(core_busy),
        .done(core_done),
        .error(core_error),
        .error_code(core_error_code),
        .debug_row(core_debug_row),
        .debug_block(core_debug_block),
        .debug_lane(core_debug_lane)
    );

    always @(posedge S_AXI_ACLK) begin
        if (!S_AXI_ARESETN) begin
            input_rd_data <= 16'sd0;
        end else if (input_rd_en) begin
            if (input_rd_addr < INPUT_MEM_DEPTH[15:0]) begin
                input_rd_data <= input_mem[input_rd_addr];
            end else begin
                input_rd_data <= 16'sd0;
            end
        end
    end

    always @(posedge S_AXI_ACLK) begin
        if (!S_AXI_ARESETN) begin
            axi_awready <= 1'b0;
            axi_awaddr <= {C_S_AXI_ADDR_WIDTH{1'b0}};
            aw_en <= 1'b1;
        end else if (!axi_awready && S_AXI_AWVALID && S_AXI_WVALID && aw_en) begin
            axi_awready <= 1'b1;
            axi_awaddr <= S_AXI_AWADDR;
            aw_en <= 1'b0;
        end else if (S_AXI_BREADY && axi_bvalid) begin
            aw_en <= 1'b1;
            axi_awready <= 1'b0;
        end else begin
            axi_awready <= 1'b0;
        end
    end

    always @(posedge S_AXI_ACLK) begin
        if (!S_AXI_ARESETN) begin
            axi_wready <= 1'b0;
        end else if (!axi_wready && S_AXI_WVALID && S_AXI_AWVALID && aw_en) begin
            axi_wready <= 1'b1;
        end else begin
            axi_wready <= 1'b0;
        end
    end

    always @(posedge S_AXI_ACLK) begin
        if (!S_AXI_ARESETN) begin
            axi_bvalid <= 1'b0;
            axi_bresp <= 2'b00;
        end else if (axi_awready && S_AXI_AWVALID && !axi_bvalid && axi_wready && S_AXI_WVALID) begin
            axi_bvalid <= 1'b1;
            axi_bresp <= 2'b00;
        end else if (S_AXI_BREADY && axi_bvalid) begin
            axi_bvalid <= 1'b0;
        end
    end

    always @(posedge S_AXI_ACLK) begin
        if (!S_AXI_ARESETN) begin
            axi_arready <= 1'b0;
            axi_araddr <= {C_S_AXI_ADDR_WIDTH{1'b0}};
        end else if (!axi_arready && S_AXI_ARVALID) begin
            axi_arready <= 1'b1;
            axi_araddr <= S_AXI_ARADDR;
        end else begin
            axi_arready <= 1'b0;
        end
    end

    always @(posedge S_AXI_ACLK) begin
        if (!S_AXI_ARESETN) begin
            axi_rvalid <= 1'b0;
            axi_rresp <= 2'b00;
        end else if (slv_reg_rden) begin
            axi_rvalid <= 1'b1;
            axi_rresp <= 2'b00;
        end else if (axi_rvalid && S_AXI_RREADY) begin
            axi_rvalid <= 1'b0;
        end
    end

    always @(posedge S_AXI_ACLK) begin
        if (!S_AXI_ARESETN) begin
            mode_reg <= 1'b0;
            scale_shift_reg <= 6'd20;
            in_features_reg <= 32'd32;
            out_features_reg <= 32'd3;
            input_addr_reg <= 32'd0;
            result_addr_reg <= 32'd0;
            stream_last_reg <= 1'b0;
            done_sticky <= 1'b0;
            stream_write_error <= 1'b0;
            input_addr_error <= 1'b0;
            result_overflow <= 1'b0;
            result_count <= 32'd0;
            core_start <= 1'b0;
            core_s_axis_tdata <= 32'd0;
            core_s_axis_tvalid <= 1'b0;
            core_s_axis_tlast <= 1'b0;
            for (i = 0; i < INPUT_MEM_DEPTH; i = i + 1) begin
                input_mem[i] <= 16'sd0;
            end
            for (i = 0; i < RESULT_DEPTH; i = i + 1) begin
                result_data_mem[i] <= 32'sd0;
                result_row_mem[i] <= 32'd0;
                result_block_mem[i] <= 32'd0;
                result_lane_mem[i] <= 16'd0;
                result_last_mem[i] <= 1'b0;
            end
        end else begin
            core_start <= 1'b0;
            core_s_axis_tvalid <= 1'b0;
            core_s_axis_tlast <= 1'b0;

            if (core_done) begin
                done_sticky <= 1'b1;
            end

            if (core_m_axis_tvalid) begin
                if (result_count < RESULT_DEPTH) begin
                    result_data_mem[result_count[7:0]] <= core_m_axis_tdata;
                    result_row_mem[result_count[7:0]] <= core_m_axis_row;
                    result_block_mem[result_count[7:0]] <= core_m_axis_block;
                    result_lane_mem[result_count[7:0]] <= core_m_axis_lane;
                    result_last_mem[result_count[7:0]] <= core_m_axis_tlast;
                    result_count <= result_count + 32'd1;
                end else begin
                    result_overflow <= 1'b1;
                end
            end

            if (slv_reg_wren) begin
                case (wr_addr)
                    REG_CONTROL: begin
                        mode_reg <= S_AXI_WDATA[1];
                        if (S_AXI_WDATA[0]) begin
                            done_sticky <= 1'b0;
                            stream_write_error <= 1'b0;
                            input_addr_error <= 1'b0;
                            result_overflow <= 1'b0;
                            result_count <= 32'd0;
                            core_start <= 1'b1;
                        end
                        if (S_AXI_WDATA[2]) begin
                            done_sticky <= 1'b0;
                            stream_write_error <= 1'b0;
                            input_addr_error <= 1'b0;
                            result_overflow <= 1'b0;
                            result_count <= 32'd0;
                        end
                    end
                    REG_SCALE_SHIFT: begin
                        scale_shift_reg <= S_AXI_WDATA[5:0];
                    end
                    REG_IN_FEATURES: begin
                        in_features_reg <= S_AXI_WDATA;
                    end
                    REG_OUT_FEATURES: begin
                        out_features_reg <= S_AXI_WDATA;
                    end
                    REG_INPUT_ADDR: begin
                        input_addr_reg <= S_AXI_WDATA;
                    end
                    REG_INPUT_DATA: begin
                        if (input_addr_reg < INPUT_MEM_DEPTH) begin
                            input_mem[input_addr_reg[9:0]] <= S_AXI_WDATA[15:0];
                        end else begin
                            input_addr_error <= 1'b1;
                        end
                    end
                    REG_STREAM_DATA: begin
                        if (core_s_axis_tready) begin
                            core_s_axis_tdata <= S_AXI_WDATA;
                            core_s_axis_tlast <= stream_last_reg;
                            core_s_axis_tvalid <= 1'b1;
                        end else begin
                            stream_write_error <= 1'b1;
                        end
                    end
                    REG_STREAM_LAST: begin
                        stream_last_reg <= S_AXI_WDATA[0];
                    end
                    REG_RESULT_ADDR: begin
                        result_addr_reg <= S_AXI_WDATA;
                    end
                    default: begin
                    end
                endcase
            end
        end
    end

    always @(*) begin
        case (rd_addr)
            REG_VERSION:      axi_rdata = VERSION;
            REG_CONTROL:      axi_rdata = {30'd0, mode_reg, 1'b0};
            REG_STATUS:       axi_rdata = {
                23'd0,
                input_addr_error,
                stream_write_error,
                result_overflow,
                (result_count != 32'd0),
                core_s_axis_tready,
                core_error,
                done_sticky,
                core_busy
            };
            REG_ERROR_CODE:   axi_rdata = {24'd0, core_error_code};
            REG_SCALE_SHIFT:  axi_rdata = {26'd0, scale_shift_reg};
            REG_IN_FEATURES:  axi_rdata = in_features_reg;
            REG_OUT_FEATURES: axi_rdata = out_features_reg;
            REG_DEBUG_ROW:    axi_rdata = core_debug_row;
            REG_DEBUG_BLOCK:  axi_rdata = core_debug_block;
            REG_DEBUG_LANE:   axi_rdata = {16'd0, core_debug_lane};
            REG_INPUT_ADDR:   axi_rdata = input_addr_reg;
            REG_INPUT_DATA: begin
                if (input_addr_reg < INPUT_MEM_DEPTH) begin
                    axi_rdata = {{16{input_mem[input_addr_reg[9:0]][15]}}, input_mem[input_addr_reg[9:0]]};
                end else begin
                    axi_rdata = 32'd0;
                end
            end
            REG_STREAM_DATA:  axi_rdata = 32'd0;
            REG_STREAM_LAST:  axi_rdata = {31'd0, stream_last_reg};
            REG_RESULT_COUNT: axi_rdata = result_count;
            REG_RESULT_ADDR:  axi_rdata = result_addr_reg;
            REG_RESULT_DATA: begin
                if (result_addr_reg < RESULT_DEPTH) begin
                    axi_rdata = result_data_mem[result_addr_reg[7:0]];
                end else begin
                    axi_rdata = 32'd0;
                end
            end
            REG_RESULT_ROW: begin
                if (result_addr_reg < RESULT_DEPTH) begin
                    axi_rdata = result_row_mem[result_addr_reg[7:0]];
                end else begin
                    axi_rdata = 32'd0;
                end
            end
            REG_RESULT_BLOCK: begin
                if (result_addr_reg < RESULT_DEPTH) begin
                    axi_rdata = result_block_mem[result_addr_reg[7:0]];
                end else begin
                    axi_rdata = 32'd0;
                end
            end
            REG_RESULT_LANE: begin
                if (result_addr_reg < RESULT_DEPTH) begin
                    axi_rdata = {16'd0, result_lane_mem[result_addr_reg[7:0]]};
                end else begin
                    axi_rdata = 32'd0;
                end
            end
            REG_RESULT_LAST: begin
                if (result_addr_reg < RESULT_DEPTH) begin
                    axi_rdata = {31'd0, result_last_mem[result_addr_reg[7:0]]};
                end else begin
                    axi_rdata = 32'd0;
                end
            end
            default:          axi_rdata = 32'd0;
        endcase
    end

endmodule
