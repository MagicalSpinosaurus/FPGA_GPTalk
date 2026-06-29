`timescale 1ns / 1ps

// DMA-facing GEMV wrapper.
//
// Data movement contract:
//   - PS writes the int16 input vector through an AXI BRAM Controller.
//   - AXI DMA MM2S streams packed Q8_0 scale/weight words into S_AXIS.
//   - GEMV results leave M_AXIS and are captured by AXI DMA S2MM.
//   - AXI-Lite is control/status only.

module gemv_q8_0_dma_top #(
    parameter integer C_S_AXI_DATA_WIDTH = 32,
    parameter integer C_S_AXI_ADDR_WIDTH = 12,
    parameter integer LANES = 16,
    parameter integer Q8_BLOCK_SIZE = 32,
    parameter integer INPUT_ADDR_WIDTH = 16,
    parameter [31:0] VERSION = 32'h000A_0001
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 S_AXI_ACLK CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF S_AXI:S_AXIS:M_AXIS:INPUT_BRAM_PORT, ASSOCIATED_RESET S_AXI_ARESETN, FREQ_HZ 50000000" *)
    input wire S_AXI_ACLK,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 S_AXI_ARESETN RST" *)
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input wire S_AXI_ARESETN,

    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXI, DATA_WIDTH 32, PROTOCOL AXI4LITE, FREQ_HZ 50000000, ADDR_WIDTH 12, HAS_BURST 0, HAS_LOCK 0, HAS_PROT 1, HAS_CACHE 0, HAS_QOS 0, HAS_REGION 0, SUPPORTS_NARROW_BURST 0, NUM_READ_OUTSTANDING 1, NUM_WRITE_OUTSTANDING 1" *)
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
    input wire S_AXI_RREADY,

    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TDATA" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXIS, TDATA_NUM_BYTES 4, HAS_TKEEP 1, HAS_TLAST 1, FREQ_HZ 50000000" *)
    input wire [31:0] S_AXIS_TDATA,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TKEEP" *)
    input wire [3:0] S_AXIS_TKEEP,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TVALID" *)
    input wire S_AXIS_TVALID,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TREADY" *)
    output wire S_AXIS_TREADY,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TLAST" *)
    input wire S_AXIS_TLAST,

    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TDATA" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME M_AXIS, TDATA_NUM_BYTES 4, HAS_TKEEP 1, HAS_TLAST 1, FREQ_HZ 50000000" *)
    output wire [31:0] M_AXIS_TDATA,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TKEEP" *)
    output wire [3:0] M_AXIS_TKEEP,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TVALID" *)
    output wire M_AXIS_TVALID,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TREADY" *)
    input wire M_AXIS_TREADY,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TLAST" *)
    output wire M_AXIS_TLAST,

    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 INPUT_BRAM_PORT CLK" *)
    (* X_INTERFACE_PARAMETER = "MASTER_TYPE BRAM_CTRL, MEM_WIDTH 32, MEM_SIZE 65536" *)
    output wire INPUT_BRAM_CLK,
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 INPUT_BRAM_PORT RST" *)
    output wire INPUT_BRAM_RST,
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 INPUT_BRAM_PORT EN" *)
    output wire INPUT_BRAM_EN,
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 INPUT_BRAM_PORT WE" *)
    output wire [3:0] INPUT_BRAM_WE,
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 INPUT_BRAM_PORT ADDR" *)
    output wire [31:0] INPUT_BRAM_ADDR,
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 INPUT_BRAM_PORT DIN" *)
    output wire [31:0] INPUT_BRAM_DIN,
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 INPUT_BRAM_PORT DOUT" *)
    input wire [31:0] INPUT_BRAM_DOUT
);

    wire ctrl_start;
    wire ctrl_clear;
    wire ctrl_mode;
    wire [5:0] ctrl_scale_shift;
    wire [31:0] ctrl_in_features;
    wire [31:0] ctrl_out_features;
    wire [31:0] ctrl_input_bram_base;
    wire [31:0] ctrl_weight_stream_length;
    wire [31:0] ctrl_result_length;

    wire core_input_rd_en;
    wire [INPUT_ADDR_WIDTH-1:0] core_input_rd_addr;
    wire signed [15:0] core_input_rd_data;

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

    reg input_half_sel;
    wire [31:0] input_addr_u32 = {{(32-INPUT_ADDR_WIDTH){1'b0}}, core_input_rd_addr};

    assign INPUT_BRAM_CLK = S_AXI_ACLK;
    assign INPUT_BRAM_RST = !S_AXI_ARESETN;
    assign INPUT_BRAM_EN = core_input_rd_en;
    assign INPUT_BRAM_WE = 4'b0000;
    assign INPUT_BRAM_DIN = 32'd0;
    assign INPUT_BRAM_ADDR = (input_addr_u32 >> 1) << 2;
    assign core_input_rd_data = input_half_sel ? INPUT_BRAM_DOUT[31:16] : INPUT_BRAM_DOUT[15:0];

    assign M_AXIS_TDATA = core_m_axis_tdata;
    assign M_AXIS_TKEEP = 4'hf;
    assign M_AXIS_TVALID = core_m_axis_tvalid;
    assign M_AXIS_TLAST = core_m_axis_tlast;

    always @(posedge S_AXI_ACLK) begin
        if (!S_AXI_ARESETN) begin
            input_half_sel <= 1'b0;
        end else if (core_input_rd_en) begin
            input_half_sel <= core_input_rd_addr[0];
        end
    end

    gemv_q8_0_ctrl_axi_lite #(
        .C_S_AXI_DATA_WIDTH(C_S_AXI_DATA_WIDTH),
        .C_S_AXI_ADDR_WIDTH(C_S_AXI_ADDR_WIDTH),
        .VERSION(VERSION)
    ) ctrl (
        .S_AXI_ACLK(S_AXI_ACLK),
        .S_AXI_ARESETN(S_AXI_ARESETN),
        .S_AXI_AWADDR(S_AXI_AWADDR),
        .S_AXI_AWPROT(S_AXI_AWPROT),
        .S_AXI_AWVALID(S_AXI_AWVALID),
        .S_AXI_AWREADY(S_AXI_AWREADY),
        .S_AXI_WDATA(S_AXI_WDATA),
        .S_AXI_WSTRB(S_AXI_WSTRB),
        .S_AXI_WVALID(S_AXI_WVALID),
        .S_AXI_WREADY(S_AXI_WREADY),
        .S_AXI_BRESP(S_AXI_BRESP),
        .S_AXI_BVALID(S_AXI_BVALID),
        .S_AXI_BREADY(S_AXI_BREADY),
        .S_AXI_ARADDR(S_AXI_ARADDR),
        .S_AXI_ARPROT(S_AXI_ARPROT),
        .S_AXI_ARVALID(S_AXI_ARVALID),
        .S_AXI_ARREADY(S_AXI_ARREADY),
        .S_AXI_RDATA(S_AXI_RDATA),
        .S_AXI_RRESP(S_AXI_RRESP),
        .S_AXI_RVALID(S_AXI_RVALID),
        .S_AXI_RREADY(S_AXI_RREADY),
        .start_pulse(ctrl_start),
        .clear_pulse(ctrl_clear),
        .mode(ctrl_mode),
        .scale_shift(ctrl_scale_shift),
        .in_features(ctrl_in_features),
        .out_features(ctrl_out_features),
        .input_bram_base(ctrl_input_bram_base),
        .weight_stream_length(ctrl_weight_stream_length),
        .result_length(ctrl_result_length),
        .core_busy(core_busy),
        .core_done(core_done),
        .core_error(core_error),
        .core_error_code(core_error_code),
        .stream_in_ready(S_AXIS_TREADY),
        .result_out_valid(core_m_axis_tvalid),
        .result_out_ready(M_AXIS_TREADY),
        .debug_row(core_debug_row),
        .debug_block(core_debug_block),
        .debug_lane(core_debug_lane)
    );

    gemv_q8_0_stream_core #(
        .LANES(LANES),
        .Q8_BLOCK_SIZE(Q8_BLOCK_SIZE),
        .TDATA_WIDTH(32),
        .INPUT_ADDR_WIDTH(INPUT_ADDR_WIDTH),
        .FEATURE_WIDTH(32),
        .SCALE_SHIFT_WIDTH(6),
        .SCALE_SHIFT_DEFAULT(20),
        .ROW_ACC_WIDTH(64),
        .ROUND_ENABLE(1)
    ) core (
        .clk(S_AXI_ACLK),
        .reset_p(!S_AXI_ARESETN),
        .start(ctrl_start),
        .mode(ctrl_mode),
        .scale_shift(ctrl_scale_shift),
        .in_features(ctrl_in_features),
        .out_features(ctrl_out_features),
        .input_rd_en(core_input_rd_en),
        .input_rd_addr(core_input_rd_addr),
        .input_rd_data(core_input_rd_data),
        .s_axis_tdata(S_AXIS_TDATA),
        .s_axis_tvalid(S_AXIS_TVALID),
        .s_axis_tready(S_AXIS_TREADY),
        .s_axis_tlast(S_AXIS_TLAST),
        .m_axis_tdata(core_m_axis_tdata),
        .m_axis_tvalid(core_m_axis_tvalid),
        .m_axis_tready(M_AXIS_TREADY),
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

endmodule
