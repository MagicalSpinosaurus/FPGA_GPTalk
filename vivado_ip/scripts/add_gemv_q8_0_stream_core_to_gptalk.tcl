# Add Prompt 09 Q8_0 GEMV RTL and simulation testbench to the existing GPTalk
# Vivado project.
#
# Run from repository root with a configured Vivado environment:
#   vivado -mode batch -source vivado_ip/scripts/add_gemv_q8_0_stream_core_to_gptalk.tcl
#
# Or source this file from the Vivado Tcl console.

set script_dir [file normalize [file dirname [info script]]]
set repo_root [file normalize [file join $script_dir .. ..]]
set project_file [file join $repo_root hw vivado_project GPTalk.xpr]
set rtl_file [file join $repo_root vivado_ip rtl gemv_q8_0_stream_core.v]
set tb_file [file join $repo_root vivado_ip tb tb_gemv_q8_0_stream_core.v]

if {![file exists $project_file]} {
    error "Vivado project not found: $project_file"
}
if {![file exists $rtl_file]} {
    error "RTL file not found: $rtl_file"
}
if {![file exists $tb_file]} {
    error "Testbench file not found: $tb_file"
}

open_project $project_file

add_files -norecurse -fileset sources_1 $rtl_file
if {[llength [get_filesets -quiet sim_1]] == 0} {
    create_fileset -simset sim_1
}
add_files -norecurse -fileset sim_1 $tb_file
set_property file_type Verilog [get_files $rtl_file]
set_property file_type SystemVerilog [get_files $tb_file]
set_property include_dirs [list [file join $repo_root vivado_ip tb]] [get_filesets sim_1]

set_property top tb_gemv_q8_0_stream_core [get_filesets sim_1]
set_property target_language Verilog [current_project]

update_compile_order -fileset sources_1
update_compile_order -fileset sim_1

puts "Added GEMV Q8_0 RTL: $rtl_file"
puts "Added GEMV Q8_0 TB : $tb_file"
puts "Golden directory plusarg example:"
puts "  +GOLDEN_DIR=$repo_root/pycharm/golden/fake_gemv"
