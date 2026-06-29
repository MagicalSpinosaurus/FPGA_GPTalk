# Create the Zybo Z7-20 DMA GEMV block design.
#
# S01 scope: generate the Vivado project, block design, HDL wrapper, and
# address-map report. This script does not launch synthesis, implementation,
# write_bitstream, or XSA export.
#
# Run:
#   vivado -mode batch -source scripts/create_zybo_gemv_dma_hw.tcl
#
# Outputs:
#   hw/zybo_gemv_dma/zybo_gemv_dma.xpr
#   logs/hw_dma_address_map.txt

error "DEPRECATED by S01.5: do not create hw/zybo_gemv_dma. Use scripts/create_or_update_gptalk_dma_bd.tcl with hw/vivado_project/GPTalk.xpr."

set script_dir [file normalize [file dirname [info script]]]
set repo_root [file normalize [file join $script_dir ..]]
set out_dir [file join $repo_root hw zybo_gemv_dma]
set log_dir [file join $repo_root logs]
file mkdir $log_dir

set board_repo [file join $repo_root vivado_board_files digilent-vivado-boards new board_files]
if {[file exists $board_repo]} {
    set_param board.repoPaths [list $board_repo]
}

set board_part "digilentinc.com:zybo-z7-20:part0:1.2"
set part_name "xc7z020clg400-1"
set bd_name "design_1"
set gemv_base 0x43C00000
set dma_base  0x40400000
set bram_base 0x42000000

set rtl_core [file join $repo_root vivado_ip rtl gemv_q8_0_stream_core.v]
set rtl_ctrl [file join $repo_root vivado_ip rtl gemv_q8_0_ctrl_axi_lite.v]
set rtl_top  [file join $repo_root vivado_ip rtl gemv_q8_0_dma_top.v]

foreach required [list $rtl_core $rtl_ctrl $rtl_top] {
    if {![file exists $required]} {
        error "missing RTL file: $required"
    }
}

proc try_set_properties {obj prop_values} {
    foreach {prop value} $prop_values {
        if {[catch {set_property $prop $value $obj} msg]} {
            puts "WARN: could not set $prop on $obj: $msg"
        }
    }
}

proc connect_if_present {a b} {
    if {[llength $a] && [llength $b]} {
        connect_bd_intf_net $a $b
        return 1
    }
    return 0
}

proc set_addr_segment {pattern offset range} {
    set segs [get_bd_addr_segs -quiet -regexp $pattern]
    if {[llength $segs] == 0} {
        puts "WARN: no address segment matched $pattern"
        return
    }
    foreach seg $segs {
        set_property offset $offset $seg
        set_property range $range $seg
    }
}

file delete -force $out_dir
create_project zybo_gemv_dma $out_dir -part $part_name
catch {set_property board_part $board_part [current_project]}
set_property target_language Verilog [current_project]

add_files -norecurse -fileset sources_1 [list $rtl_core $rtl_ctrl $rtl_top]
set_property file_type Verilog [get_files $rtl_core]
set_property file_type Verilog [get_files $rtl_ctrl]
set_property file_type Verilog [get_files $rtl_top]
update_compile_order -fileset sources_1

create_bd_design $bd_name

create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7 processing_system7_0
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
    -config {make_external "FIXED_IO, DDR" apply_board_preset "1" Master "Disable" Slave "Disable"} \
    [get_bd_cells processing_system7_0]
set_property -dict [list \
    CONFIG.PCW_USE_M_AXI_GP0 {1} \
    CONFIG.PCW_USE_S_AXI_HP0 {1} \
    CONFIG.PCW_EN_CLK0_PORT {1} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {50.000000} \
] [get_bd_cells processing_system7_0]

create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset rst_ps7_0_50M
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins rst_ps7_0_50M/slowest_sync_clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_ps7_0_50M/ext_reset_in]

create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect axi_ctrl_smc
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {3}] [get_bd_cells axi_ctrl_smc]
create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect axi_hp0_smc
set_property -dict [list CONFIG.NUM_SI {2} CONFIG.NUM_MI {1}] [get_bd_cells axi_hp0_smc]

create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma axi_dma_0
try_set_properties [get_bd_cells axi_dma_0] [list \
    CONFIG.c_include_sg {0} \
    CONFIG.c_include_mm2s {1} \
    CONFIG.c_include_s2mm {1} \
    CONFIG.c_m_axi_mm2s_data_width {32} \
    CONFIG.c_m_axi_s2mm_data_width {32} \
    CONFIG.c_m_axis_mm2s_tdata_width {32} \
    CONFIG.c_s_axis_s2mm_tdata_width {32} \
    CONFIG.c_include_mm2s_dre {1} \
    CONFIG.c_include_s2mm_dre {1} \
]

create_bd_cell -type ip -vlnv xilinx.com:ip:axis_data_fifo mm2s_axis_fifo
try_set_properties [get_bd_cells mm2s_axis_fifo] [list \
    CONFIG.TDATA_NUM_BYTES {4} \
    CONFIG.FIFO_DEPTH {1024} \
    CONFIG.HAS_TKEEP {1} \
    CONFIG.HAS_TLAST {1} \
]

create_bd_cell -type ip -vlnv xilinx.com:ip:axis_data_fifo s2mm_axis_fifo
try_set_properties [get_bd_cells s2mm_axis_fifo] [list \
    CONFIG.TDATA_NUM_BYTES {4} \
    CONFIG.FIFO_DEPTH {1024} \
    CONFIG.HAS_TKEEP {1} \
    CONFIG.HAS_TLAST {1} \
]

create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl axi_input_bram_ctrl
try_set_properties [get_bd_cells axi_input_bram_ctrl] [list \
    CONFIG.DATA_WIDTH {32} \
    CONFIG.SINGLE_PORT_BRAM {1} \
]

create_bd_cell -type ip -vlnv xilinx.com:ip:blk_mem_gen input_vector_bram
try_set_properties [get_bd_cells input_vector_bram] [list \
    CONFIG.Memory_Type {True_Dual_Port_RAM} \
    CONFIG.Use_Byte_Write_Enable {true} \
    CONFIG.Byte_Size {8} \
    CONFIG.Write_Width_A {32} \
    CONFIG.Read_Width_A {32} \
    CONFIG.Write_Width_B {32} \
    CONFIG.Read_Width_B {32} \
    CONFIG.Write_Depth_A {16384} \
    CONFIG.Register_PortA_Output_of_Memory_Primitives {false} \
    CONFIG.Register_PortB_Output_of_Memory_Primitives {false} \
]

create_bd_cell -type module -reference gemv_q8_0_dma_top gemv_q8_0_dma_top_0

set fclk [get_bd_pins processing_system7_0/FCLK_CLK0]
set resetn [get_bd_pins rst_ps7_0_50M/peripheral_aresetn]

foreach pin [list \
    [get_bd_pins processing_system7_0/M_AXI_GP0_ACLK] \
    [get_bd_pins processing_system7_0/S_AXI_HP0_ACLK] \
    [get_bd_pins axi_ctrl_smc/aclk] \
    [get_bd_pins axi_hp0_smc/aclk] \
    [get_bd_pins axi_dma_0/s_axi_lite_aclk] \
    [get_bd_pins axi_dma_0/m_axi_mm2s_aclk] \
    [get_bd_pins axi_dma_0/m_axi_s2mm_aclk] \
    [get_bd_pins mm2s_axis_fifo/s_axis_aclk] \
    [get_bd_pins s2mm_axis_fifo/s_axis_aclk] \
    [get_bd_pins axi_input_bram_ctrl/s_axi_aclk] \
    [get_bd_pins gemv_q8_0_dma_top_0/S_AXI_ACLK] \
] {
    connect_bd_net $fclk $pin
}

foreach pin [list \
    [get_bd_pins axi_ctrl_smc/aresetn] \
    [get_bd_pins axi_hp0_smc/aresetn] \
    [get_bd_pins axi_dma_0/axi_resetn] \
    [get_bd_pins mm2s_axis_fifo/s_axis_aresetn] \
    [get_bd_pins s2mm_axis_fifo/s_axis_aresetn] \
    [get_bd_pins axi_input_bram_ctrl/s_axi_aresetn] \
    [get_bd_pins gemv_q8_0_dma_top_0/S_AXI_ARESETN] \
] {
    connect_bd_net $resetn $pin
}

connect_bd_intf_net [get_bd_intf_pins processing_system7_0/M_AXI_GP0] [get_bd_intf_pins axi_ctrl_smc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_ctrl_smc/M00_AXI] [get_bd_intf_pins gemv_q8_0_dma_top_0/S_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_ctrl_smc/M01_AXI] [get_bd_intf_pins axi_dma_0/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins axi_ctrl_smc/M02_AXI] [get_bd_intf_pins axi_input_bram_ctrl/S_AXI]

connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXI_MM2S] [get_bd_intf_pins axi_hp0_smc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXI_S2MM] [get_bd_intf_pins axi_hp0_smc/S01_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_hp0_smc/M00_AXI] [get_bd_intf_pins processing_system7_0/S_AXI_HP0]

connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXIS_MM2S] [get_bd_intf_pins mm2s_axis_fifo/S_AXIS]
connect_bd_intf_net [get_bd_intf_pins mm2s_axis_fifo/M_AXIS] [get_bd_intf_pins gemv_q8_0_dma_top_0/S_AXIS]
connect_bd_intf_net [get_bd_intf_pins gemv_q8_0_dma_top_0/M_AXIS] [get_bd_intf_pins s2mm_axis_fifo/S_AXIS]
connect_bd_intf_net [get_bd_intf_pins s2mm_axis_fifo/M_AXIS] [get_bd_intf_pins axi_dma_0/S_AXIS_S2MM]

connect_bd_intf_net [get_bd_intf_pins axi_input_bram_ctrl/BRAM_PORTA] [get_bd_intf_pins input_vector_bram/BRAM_PORTA]
if {![connect_if_present [get_bd_intf_pins -quiet gemv_q8_0_dma_top_0/INPUT_BRAM_PORT] [get_bd_intf_pins -quiet input_vector_bram/BRAM_PORTB]]} {
    connect_bd_net [get_bd_pins gemv_q8_0_dma_top_0/INPUT_BRAM_CLK]  [get_bd_pins input_vector_bram/clkb]
    connect_bd_net [get_bd_pins gemv_q8_0_dma_top_0/INPUT_BRAM_RST]  [get_bd_pins input_vector_bram/rstb]
    connect_bd_net [get_bd_pins gemv_q8_0_dma_top_0/INPUT_BRAM_EN]   [get_bd_pins input_vector_bram/enb]
    connect_bd_net [get_bd_pins gemv_q8_0_dma_top_0/INPUT_BRAM_WE]   [get_bd_pins input_vector_bram/web]
    connect_bd_net [get_bd_pins gemv_q8_0_dma_top_0/INPUT_BRAM_ADDR] [get_bd_pins input_vector_bram/addrb]
    connect_bd_net [get_bd_pins gemv_q8_0_dma_top_0/INPUT_BRAM_DIN]  [get_bd_pins input_vector_bram/dinb]
    connect_bd_net [get_bd_pins gemv_q8_0_dma_top_0/INPUT_BRAM_DOUT] [get_bd_pins input_vector_bram/doutb]
}

assign_bd_address
set_addr_segment {.*SEG_gemv_q8_0_dma_top_0.*} $gemv_base 4K
set_addr_segment {.*SEG_axi_dma_0_Reg.*} $dma_base 64K
set_addr_segment {.*SEG_axi_input_bram_ctrl.*} $bram_base 64K

validate_bd_design
save_bd_design

set address_report [file join $log_dir hw_dma_address_map.txt]
set addr_fd [open $address_report w]
puts $addr_fd "Zybo Z7-20 GEMV DMA address map"
puts $addr_fd "Generated by scripts/create_zybo_gemv_dma_hw.tcl"
puts $addr_fd ""
puts $addr_fd [format "PL clock target: 50 MHz"]
puts $addr_fd [format "GEMV control base requested: 0x%08X" $gemv_base]
puts $addr_fd [format "AXI DMA base requested:      0x%08X" $dma_base]
puts $addr_fd [format "Input BRAM base requested:   0x%08X" $bram_base]
puts $addr_fd ""
foreach seg [get_bd_addr_segs -quiet -regexp {.*SEG_.*}] {
    puts $addr_fd [format "%s OFFSET=%s RANGE=%s" $seg [get_property OFFSET $seg] [get_property RANGE $seg]]
}
close $addr_fd

set bd_file [get_files [file join $out_dir zybo_gemv_dma.srcs sources_1 bd $bd_name ${bd_name}.bd]]
make_wrapper -files $bd_file -top
add_files -norecurse [file join $out_dir zybo_gemv_dma.gen sources_1 bd $bd_name hdl ${bd_name}_wrapper.v]
set_property top ${bd_name}_wrapper [current_fileset]
update_compile_order -fileset sources_1

puts "S01_DMA_BD_CREATED=1"
puts "PROJECT_DIR=$out_dir"
puts "ADDRESS_REPORT=$address_report"
puts [format "GEMV_CONTROL_BASE=0x%08X" $gemv_base]
puts [format "AXI_DMA_BASE=0x%08X" $dma_base]
puts [format "INPUT_BRAM_BASE=0x%08X" $bram_base]
