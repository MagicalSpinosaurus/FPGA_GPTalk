# Build the active GPTalk.xpr DMA GEMV bitstream and export XSA.
#
# S02 entrypoint. Do not run this during S01.5 verification.
#
# Run:
#   vivado -mode batch -source scripts/build_gptalk_dma_bitstream.tcl

set script_dir [file normalize [file dirname [info script]]]
set repo_root [file normalize [file join $script_dir ..]]
set project_xpr [file join $repo_root hw vivado_project GPTalk.xpr]
set project_dir [file dirname $project_xpr]
set log_dir [file join $repo_root logs]
set docs_dir [file join $repo_root docs]
set export_dir [file join $project_dir export]
file mkdir $log_dir
file mkdir $docs_dir
file mkdir $export_dir

if {![file exists $project_xpr]} {
    error "Active Vivado project is missing: $project_xpr"
}

proc try_run_strategy {run_name candidates} {
    foreach candidate $candidates {
        if {[catch {set_property strategy $candidate [get_runs $run_name]}] == 0} {
            return $candidate
        }
    }
    return "unchanged"
}

proc try_step_directive {run_name prop_name candidates} {
    foreach candidate $candidates {
        if {[catch {set_property $prop_name $candidate [get_runs $run_name]}] == 0} {
            return $candidate
        }
    }
    return "unchanged"
}

open_project $project_xpr

set synth_strategy [try_run_strategy synth_1 [list \
    "Flow_PerfOptimized_high" \
    "Vivado Synthesis Defaults" \
]]
set impl_strategy [try_run_strategy impl_1 [list \
    "Performance_ExplorePostRoutePhysOpt" \
    "Performance_Explore" \
    "Performance_NetDelay_high" \
    "Vivado Implementation Defaults" \
]]

set opt_directive [try_step_directive impl_1 STEPS.OPT_DESIGN.ARGS.DIRECTIVE [list Explore ExploreWithRemap Default]]
set place_directive [try_step_directive impl_1 STEPS.PLACE_DESIGN.ARGS.DIRECTIVE [list ExtraNetDelay_high ExtraPostPlacementOpt Explore Default]]
set phys_directive [try_step_directive impl_1 STEPS.PHYS_OPT_DESIGN.ARGS.DIRECTIVE [list AggressiveExplore Explore Default]]
set route_directive [try_step_directive impl_1 STEPS.ROUTE_DESIGN.ARGS.DIRECTIVE [list AggressiveExplore Explore NoTimingRelaxation Default]]
catch {set_property STEPS.PHYS_OPT_DESIGN.IS_ENABLED true [get_runs impl_1]}
catch {set_property STEPS.POST_ROUTE_PHYS_OPT_DESIGN.IS_ENABLED true [get_runs impl_1]}
set post_route_phys_directive [try_step_directive impl_1 STEPS.POST_ROUTE_PHYS_OPT_DESIGN.ARGS.DIRECTIVE [list AggressiveExplore Explore Default]]

set strategy_log [file join $log_dir vivado_impl_strategy.txt]
set strategy_fd [open $strategy_log w]
puts $strategy_fd "GPTalk DMA Vivado build strategy"
puts $strategy_fd "Project: $project_xpr"
puts $strategy_fd "synth_1 strategy: $synth_strategy"
puts $strategy_fd "impl_1 strategy: $impl_strategy"
puts $strategy_fd "opt_design directive: $opt_directive"
puts $strategy_fd "place_design directive: $place_directive"
puts $strategy_fd "phys_opt_design directive: $phys_directive"
puts $strategy_fd "route_design directive: $route_directive"
puts $strategy_fd "post_route_phys_opt_design directive: $post_route_phys_directive"
close $strategy_fd

reset_run synth_1
launch_runs synth_1 -jobs 8
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
    error "synth_1 did not complete"
}
if {[get_property STATUS [get_runs synth_1]] ne "synth_design Complete!"} {
    error "synth_1 failed: [get_property STATUS [get_runs synth_1]]"
}

reset_run impl_1
launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
    error "impl_1 did not complete"
}
if {![string match "*Complete*" [get_property STATUS [get_runs impl_1]]]} {
    error "impl_1 failed: [get_property STATUS [get_runs impl_1]]"
}

open_run impl_1
set bit_candidates [glob -nocomplain [file join $project_dir GPTalk.runs impl_1 *.bit]]
if {[llength $bit_candidates] == 0} {
    error "bitstream not found under [file join $project_dir GPTalk.runs impl_1]"
}
set bit_file [lindex $bit_candidates 0]
set xsa_file [file join $export_dir GPTalk_dma.xsa]
write_hw_platform -fixed -include_bit -force -file $xsa_file

set active_doc [file join $docs_dir 00_ACTIVE_KR.md]
set doc_fd [open $active_doc a]
puts $doc_fd ""
puts $doc_fd "## S02 빌드 산출물 기록"
puts $doc_fd ""
puts $doc_fd "- 기록 시각: [clock format [clock seconds] -format {%Y-%m-%d %H:%M:%S %Z}]"
puts $doc_fd "- Bitstream: `$bit_file`"
puts $doc_fd "- XSA: `$xsa_file`"
puts $doc_fd "- Strategy log: `$strategy_log`"
close $doc_fd

puts "BIT_FILE=$bit_file"
puts "XSA_FILE=$xsa_file"
puts "STRATEGY_LOG=$strategy_log"
