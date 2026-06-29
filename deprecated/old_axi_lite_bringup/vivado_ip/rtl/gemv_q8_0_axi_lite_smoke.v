`timescale 1ns / 1ps

// Minimal AXI-Lite register target for Zybo bring-up.
//
// This module is a fallback access build used when the full GEMV datapath is
// too large to route in the current bring-up pass. It preserves the software
// visible control/status register window needed for Linux/devmem smoke tests,
// but it does not execute fake_gemv.

module gemv_q8_0_axi_lite_smoke #(
    parameter integer C_S_AXI_DATA_WIDTH = 32,
    parameter integer C_S_AXI_ADDR_WIDTH = 12,
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

    reg mode_reg;
    reg [5:0] scale_shift_reg;
    reg [31:0] in_features_reg;
    reg [31:0] out_features_reg;
    reg [31:0] input_addr_reg;
    reg [31:0] input_data_reg;
    reg [31:0] stream_data_reg;
    reg stream_last_reg;
    reg done_sticky;
    reg [31:0] result_addr_reg;

    wire slv_reg_wren = axi_wready && S_AXI_WVALID && axi_awready && S_AXI_AWVALID;
    wire slv_reg_rden = axi_arready && S_AXI_ARVALID && !axi_rvalid;
    wire [7:0] wr_addr = axi_awaddr[ADDR_LSB + OPT_MEM_ADDR_BITS:ADDR_LSB];
    wire [7:0] rd_addr = axi_araddr[ADDR_LSB + OPT_MEM_ADDR_BITS:ADDR_LSB];

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
            input_data_reg <= 32'd0;
            stream_data_reg <= 32'd0;
            stream_last_reg <= 1'b0;
            done_sticky <= 1'b0;
            result_addr_reg <= 32'd0;
        end else if (slv_reg_wren) begin
            case (wr_addr)
                REG_CONTROL: begin
                    mode_reg <= S_AXI_WDATA[1];
                    if (S_AXI_WDATA[0]) begin
                        done_sticky <= 1'b1;
                    end
                    if (S_AXI_WDATA[2]) begin
                        done_sticky <= 1'b0;
                    end
                end
                REG_SCALE_SHIFT: scale_shift_reg <= S_AXI_WDATA[5:0];
                REG_IN_FEATURES: in_features_reg <= S_AXI_WDATA;
                REG_OUT_FEATURES: out_features_reg <= S_AXI_WDATA;
                REG_INPUT_ADDR: input_addr_reg <= S_AXI_WDATA;
                REG_INPUT_DATA: input_data_reg <= S_AXI_WDATA;
                REG_STREAM_DATA: stream_data_reg <= S_AXI_WDATA;
                REG_STREAM_LAST: stream_last_reg <= S_AXI_WDATA[0];
                REG_RESULT_ADDR: result_addr_reg <= S_AXI_WDATA;
                default: begin
                end
            endcase
        end
    end

    always @(*) begin
        case (rd_addr)
            REG_VERSION: axi_rdata = VERSION;
            REG_CONTROL: axi_rdata = {30'd0, mode_reg, 1'b0};
            REG_STATUS: axi_rdata = {23'd0, 1'b1, 6'd0, done_sticky, 1'b0};
            REG_ERROR_CODE: axi_rdata = 32'd0;
            REG_SCALE_SHIFT: axi_rdata = {26'd0, scale_shift_reg};
            REG_IN_FEATURES: axi_rdata = in_features_reg;
            REG_OUT_FEATURES: axi_rdata = out_features_reg;
            REG_DEBUG_ROW: axi_rdata = 32'd0;
            REG_DEBUG_BLOCK: axi_rdata = 32'd0;
            REG_DEBUG_LANE: axi_rdata = 32'd0;
            REG_INPUT_ADDR: axi_rdata = input_addr_reg;
            REG_INPUT_DATA: axi_rdata = input_data_reg;
            REG_STREAM_DATA: axi_rdata = stream_data_reg;
            REG_STREAM_LAST: axi_rdata = {31'd0, stream_last_reg};
            REG_RESULT_COUNT: axi_rdata = 32'd0;
            REG_RESULT_ADDR: axi_rdata = result_addr_reg;
            REG_RESULT_DATA: axi_rdata = 32'd0;
            REG_RESULT_ROW: axi_rdata = 32'd0;
            REG_RESULT_BLOCK: axi_rdata = 32'd0;
            REG_RESULT_LANE: axi_rdata = 32'd0;
            REG_RESULT_LAST: axi_rdata = 32'd0;
            default: axi_rdata = 32'd0;
        endcase
    end

    wire unused_axi_inputs = &{1'b0, S_AXI_AWPROT, S_AXI_ARPROT, S_AXI_WSTRB};

endmodule
