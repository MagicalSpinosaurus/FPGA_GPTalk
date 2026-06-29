# Generate implementation diagnostics for the DMA GEMV design.
#
# This script is safe to source from a failed Vivado run. It writes reports and
# does not modify the design.

if {![info exists repo_root]} {
    set script_dir [file normalize [file dirname [info script]]]
    set repo_root [file normalize [file join $script_dir ..]]
}

set report_dir [file join $repo_root reports]
file mkdir $report_dir

proc report_or_warn {label command} {
    if {[catch {uplevel 1 $command} msg]} {
        puts "WARN: $label failed: $msg"
    } else {
        puts "REPORT_OK: $label"
    }
}

if {[llength [get_runs -quiet impl_1]]} {
    report_or_warn "open impl_1" {open_run impl_1}
} elseif {[llength [get_runs -quiet synth_1]]} {
    report_or_warn "open synth_1" {open_run synth_1}
}

report_or_warn "hierarchical utilization" [list \
    report_utilization -hierarchical -file [file join $report_dir full_gemv_util_hier.rpt]]

report_or_warn "timing summary" [list \
    report_timing_summary -delay_type max -max_paths 50 -file [file join $report_dir full_gemv_timing_summary.rpt]]

report_or_warn "route status" [list \
    report_route_status -file [file join $report_dir full_gemv_route_status.rpt]]

report_or_warn "congestion" [list \
    report_design_analysis -congestion -file [file join $report_dir full_gemv_congestion.rpt]]

report_or_warn "qor suggestions" [list \
    report_qor_suggestions -file [file join $report_dir full_gemv_qor_suggestions.rpt]]

puts "REPORT_DIR=$report_dir"
