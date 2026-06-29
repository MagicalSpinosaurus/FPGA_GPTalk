`timescale 1ns / 1ps

// Control/status-only AXI-Lite register block for the DMA GEMV path.
// Bulk input, weight/scale, and result movement is intentionally outside this
// register file and handled by BRAM plus AXI DMA streams.

module gemv_q8_0_ctrl_axi_lite #(
    parameter integer C_S_AXI_DATA_WIDTH = 32,
    parameter integer C_S_AXI_ADDR_WIDTH = 12,
    parameter [31:0] VERSION = 32'h000A_0001,
    parameter [31:0] INPUT_BRAM_BASE_RESET = 32'h0000_0000
) (
    input wire S_AXI_ACLK,
    input wire S_AXI_ARESETN,

    input wire [C_S_AXI_ADDR_WIDTH-1:0] S_AXI_AWADDR,
    input wire [2:0] S_AXI_AWPROT,
    input wire S_AXI_AWVALID,
    output wire S_AXI_AWREADY,
    input wire [C_S_AXI_DATA_WIDTH-1:0] S_AXI_WDATA,
    input wire [(C_S_AXI_DATA_WIDTH/8)-1:0] S_AXI_WSTRB,
    input wire S_AXI_WVALID,
    output wire S_AXI_WREADY,
    output wire [1:0] S_AXI_BRESP,
    output wire S_AXI_BVALID,
    input wire S_AXI_BREADY,
    input wire [C_S_AXI_ADDR_WIDTH-1:0] S_AXI_ARADDR,
    input wire [2:0] S_AXI_ARPROT,
    input wire S_AXI_ARVALID,
    output wire S_AXI_ARREADY,
    output wire [C_S_AXI_DATA_WIDTH-1:0] S_AXI_RDATA,
    output wire [1:0] S_AXI_RRESP,
    output wire S_AXI_RVALID,
    input wire S_AXI_RREADY,

    output reg start_pulse,
    output reg clear_pulse,
    output reg mode,
    output reg [5:0] scale_shift,
    output reg [31:0] in_features,
    output reg [31:0] out_features,
    output reg [31:0] input_bram_base,
    output reg [31:0] weight_stream_length,
    output reg [31:0] result_length,

    input wire core_busy,
    input wire core_done,
    input wire core_error,
    input wire [7:0] core_error_code,
    input wire stream_in_ready,
    input wire result_out_valid,
    input wire result_out_ready,
    input wire [31:0] debug_row,
    input wire [31:0] debug_block,
    input wire [15:0] debug_lane
);

    localparam integer ADDR_LSB = 2;
    localparam integer OPT_MEM_ADDR_BITS = 5;

    localparam [7:0] REG_VERSION       = 8'h00;
    localparam [7:0] REG_CONTROL       = 8'h01;
    localparam [7:0] REG_STATUS        = 8'h02;
    localparam [7:0] REG_ERROR_CODE    = 8'h03;
    localparam [7:0] REG_MODE          = 8'h04;
    localparam [7:0] REG_SCALE_SHIFT   = 8'h05;
    localparam [7:0] REG_IN_FEATURES   = 8'h06;
    localparam [7:0] REG_OUT_FEATURES  = 8'h07;
    localparam [7:0] REG_INPUT_BASE    = 8'h08;
    localparam [7:0] REG_WEIGHT_LENGTH = 8'h09;
    localparam [7:0] REG_RESULT_LENGTH = 8'h0a;
    localparam [7:0] REG_START         = 8'h0b;
    localparam [7:0] REG_DONE          = 8'h0c;
    localparam [7:0] REG_DEBUG_ROW     = 8'h0d;
    localparam [7:0] REG_DEBUG_BLOCK   = 8'h0e;
    localparam [7:0] REG_DEBUG_LANE    = 8'h0f;

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

    reg done_sticky;
    reg start_while_busy;

    wire slv_reg_wren = axi_wready && S_AXI_WVALID && axi_awready && S_AXI_AWVALID;
    wire slv_reg_rden = axi_arready && S_AXI_ARVALID && !axi_rvalid;
    wire [7:0] wr_addr = axi_awaddr[ADDR_LSB + OPT_MEM_ADDR_BITS:ADDR_LSB];
    wire [7:0] rd_addr = axi_araddr[ADDR_LSB + OPT_MEM_ADDR_BITS:ADDR_LSB];
    wire result_backpressure = result_out_valid && !result_out_ready;

    assign S_AXI_AWREADY = axi_awready;
    assign S_AXI_WREADY = axi_wready;
    assign S_AXI_BRESP = axi_bresp;
    assign S_AXI_BVALID = axi_bvalid;
    assign S_AXI_ARREADY = axi_arready;
    assign S_AXI_RDATA = axi_rdata;
    assign S_AXI_RRESP = axi_rresp;
    assign S_AXI_RVALID = axi_rvalid;

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
            start_pulse <= 1'b0;
            clear_pulse <= 1'b0;
            mode <= 1'b0;
            scale_shift <= 6'd20;
            in_features <= 32'd32;
            out_features <= 32'd3;
            input_bram_base <= INPUT_BRAM_BASE_RESET;
            weight_stream_length <= 32'd0;
            result_length <= 32'd0;
            done_sticky <= 1'b0;
            start_while_busy <= 1'b0;
        end else begin
            start_pulse <= 1'b0;
            clear_pulse <= 1'b0;

            if (core_done) begin
                done_sticky <= 1'b1;
            end

            if (slv_reg_wren) begin
                case (wr_addr)
                    REG_CONTROL: begin
                        if (S_AXI_WDATA[1]) begin
                            done_sticky <= 1'b0;
                            start_while_busy <= 1'b0;
                            clear_pulse <= 1'b1;
                        end
                        if (S_AXI_WDATA[0]) begin
                            done_sticky <= 1'b0;
                            if (core_busy) begin
                                start_while_busy <= 1'b1;
                            end else begin
                                start_pulse <= 1'b1;
                            end
                        end
                    end
                    REG_MODE: begin
                        mode <= S_AXI_WDATA[0];
                    end
                    REG_SCALE_SHIFT: begin
                        scale_shift <= S_AXI_WDATA[5:0];
                    end
                    REG_IN_FEATURES: begin
                        in_features <= S_AXI_WDATA;
                    end
                    REG_OUT_FEATURES: begin
                        out_features <= S_AXI_WDATA;
                    end
                    REG_INPUT_BASE: begin
                        input_bram_base <= S_AXI_WDATA;
                    end
                    REG_WEIGHT_LENGTH: begin
                        weight_stream_length <= S_AXI_WDATA;
                    end
                    REG_RESULT_LENGTH: begin
                        result_length <= S_AXI_WDATA;
                    end
                    REG_START: begin
                        if (S_AXI_WDATA[0]) begin
                            done_sticky <= 1'b0;
                            if (core_busy) begin
                                start_while_busy <= 1'b1;
                            end else begin
                                start_pulse <= 1'b1;
                            end
                        end
                    end
                    REG_DONE: begin
                        if (S_AXI_WDATA[0]) begin
                            done_sticky <= 1'b0;
                        end
                    end
                    default: begin
                    end
                endcase
            end
        end
    end

    always @(*) begin
        case (rd_addr)
            REG_VERSION:       axi_rdata = VERSION;
            REG_CONTROL:       axi_rdata = 32'd0;
            REG_STATUS:        axi_rdata = {
                23'd0,
                mode,
                start_while_busy,
                result_backpressure,
                result_out_valid,
                stream_in_ready,
                core_error,
                done_sticky,
                core_busy
            };
            REG_ERROR_CODE:    axi_rdata = {24'd0, core_error_code};
            REG_MODE:          axi_rdata = {31'd0, mode};
            REG_SCALE_SHIFT:   axi_rdata = {26'd0, scale_shift};
            REG_IN_FEATURES:   axi_rdata = in_features;
            REG_OUT_FEATURES:  axi_rdata = out_features;
            REG_INPUT_BASE:    axi_rdata = input_bram_base;
            REG_WEIGHT_LENGTH: axi_rdata = weight_stream_length;
            REG_RESULT_LENGTH: axi_rdata = result_length;
            REG_START:         axi_rdata = 32'd0;
            REG_DONE:          axi_rdata = {30'd0, core_done, done_sticky};
            REG_DEBUG_ROW:     axi_rdata = debug_row;
            REG_DEBUG_BLOCK:   axi_rdata = debug_block;
            REG_DEBUG_LANE:    axi_rdata = {16'd0, debug_lane};
            default:           axi_rdata = 32'd0;
        endcase
    end

endmodule
