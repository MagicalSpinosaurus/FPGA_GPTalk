# Prompt 09 Q8_0 GEMV simulation runner.
#
# Run from repository root after Vivado is available on PATH:
#   vivado -mode batch -source scripts/run_gemv_sim.tcl
#
# Result summary is written to:
#   logs/gemv_sim_result.txt

set script_dir [file normalize [file dirname [info script]]]
set repo_root [file normalize [file join $script_dir ..]]
set log_dir [file join $repo_root logs]
file mkdir $log_dir
set result_file [file join $log_dir gemv_sim_result.txt]

proc write_result {path status message} {
    set fd [open $path w]
    puts $fd "status: $status"
    puts $fd $message
    close $fd
}

proc fail_result {path message} {
    write_result $path "FAIL" $message
    error $message
}

proc write_text_file {path text} {
    set fd [open $path w]
    puts -nonewline $fd $text
    close $fd
}

proc run_command {log_path cmd_list} {
    set fd [open $log_path w]
    puts $fd [join $cmd_list " "]
    flush $fd
    set status [catch {exec {*}$cmd_list >@ $fd 2>@ $fd} err]
    close $fd
    if {$status != 0} {
        error "$err\nsee $log_path"
    }
}

proc write_gcc_redirect_source {path gcc_path gxx_path} {
    set fd [open $path w]
    puts $fd [format {#define _GNU_SOURCE
#include <alloca.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <stdarg.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static const char *redirect_path(const char *path) {
    if (path == 0) return path;
    if (strcmp(path, "/usr/bin/gcc") == 0) return "%s";
    if (strcmp(path, "/usr/bin/g++") == 0) return "%s";
    return path;
}

static char **redirect_argv(const char *new_path, const char *old_path, char *const argv[]) {
    if (new_path == old_path) return (char **)argv;
    int argc = 0;
    while (argv && argv[argc]) argc++;
    char **new_argv = alloca((argc + 1) * sizeof(char *));
    for (int i = 0; i < argc; i++) new_argv[i] = argv[i];
    new_argv[argc] = 0;
    if (argc > 0) new_argv[0] = (char *)new_path;
    return new_argv;
}

int access(const char *path, int mode) {
    static int (*real_access)(const char *, int) = 0;
    if (!real_access) real_access = dlsym(RTLD_NEXT, "access");
    return real_access(redirect_path(path), mode);
}

int faccessat(int dirfd, const char *path, int mode, int flags) {
    static int (*real_faccessat)(int, const char *, int, int) = 0;
    if (!real_faccessat) real_faccessat = dlsym(RTLD_NEXT, "faccessat");
    return real_faccessat(dirfd, redirect_path(path), mode, flags);
}

int stat(const char *path, struct stat *buf) {
    static int (*real_stat)(const char *, struct stat *) = 0;
    if (!real_stat) real_stat = dlsym(RTLD_NEXT, "stat");
    return real_stat(redirect_path(path), buf);
}

int lstat(const char *path, struct stat *buf) {
    static int (*real_lstat)(const char *, struct stat *) = 0;
    if (!real_lstat) real_lstat = dlsym(RTLD_NEXT, "lstat");
    return real_lstat(redirect_path(path), buf);
}

int stat64(const char *path, struct stat64 *buf) {
    static int (*real_stat64)(const char *, struct stat64 *) = 0;
    if (!real_stat64) real_stat64 = dlsym(RTLD_NEXT, "stat64");
    return real_stat64(redirect_path(path), buf);
}

int lstat64(const char *path, struct stat64 *buf) {
    static int (*real_lstat64)(const char *, struct stat64 *) = 0;
    if (!real_lstat64) real_lstat64 = dlsym(RTLD_NEXT, "lstat64");
    return real_lstat64(redirect_path(path), buf);
}

int __xstat(int ver, const char *path, struct stat *buf) {
    static int (*real_xstat)(int, const char *, struct stat *) = 0;
    if (!real_xstat) real_xstat = dlsym(RTLD_NEXT, "__xstat");
    return real_xstat(ver, redirect_path(path), buf);
}

int __lxstat(int ver, const char *path, struct stat *buf) {
    static int (*real_lxstat)(int, const char *, struct stat *) = 0;
    if (!real_lxstat) real_lxstat = dlsym(RTLD_NEXT, "__lxstat");
    return real_lxstat(ver, redirect_path(path), buf);
}

int __xstat64(int ver, const char *path, struct stat64 *buf) {
    static int (*real_xstat64)(int, const char *, struct stat64 *) = 0;
    if (!real_xstat64) real_xstat64 = dlsym(RTLD_NEXT, "__xstat64");
    return real_xstat64(ver, redirect_path(path), buf);
}

int __lxstat64(int ver, const char *path, struct stat64 *buf) {
    static int (*real_lxstat64)(int, const char *, struct stat64 *) = 0;
    if (!real_lxstat64) real_lxstat64 = dlsym(RTLD_NEXT, "__lxstat64");
    return real_lxstat64(ver, redirect_path(path), buf);
}

int fstatat(int dirfd, const char *path, struct stat *buf, int flags) {
    static int (*real_fstatat)(int, const char *, struct stat *, int) = 0;
    if (!real_fstatat) real_fstatat = dlsym(RTLD_NEXT, "fstatat");
    return real_fstatat(dirfd, redirect_path(path), buf, flags);
}

int fstatat64(int dirfd, const char *path, struct stat64 *buf, int flags) {
    static int (*real_fstatat64)(int, const char *, struct stat64 *, int) = 0;
    if (!real_fstatat64) real_fstatat64 = dlsym(RTLD_NEXT, "fstatat64");
    return real_fstatat64(dirfd, redirect_path(path), buf, flags);
}

int __fxstatat(int ver, int dirfd, const char *path, struct stat *buf, int flags) {
    static int (*real_fxstatat)(int, int, const char *, struct stat *, int) = 0;
    if (!real_fxstatat) real_fxstatat = dlsym(RTLD_NEXT, "__fxstatat");
    return real_fxstatat(ver, dirfd, redirect_path(path), buf, flags);
}

int __fxstatat64(int ver, int dirfd, const char *path, struct stat64 *buf, int flags) {
    static int (*real_fxstatat64)(int, int, const char *, struct stat64 *, int) = 0;
    if (!real_fxstatat64) real_fxstatat64 = dlsym(RTLD_NEXT, "__fxstatat64");
    return real_fxstatat64(ver, dirfd, redirect_path(path), buf, flags);
}

int open(const char *path, int flags, ...) {
    static int (*real_open)(const char *, int, ...) = 0;
    mode_t mode = 0;
    if (!real_open) real_open = dlsym(RTLD_NEXT, "open");
    if (flags & O_CREAT) {
        va_list ap;
        va_start(ap, flags);
        mode = va_arg(ap, mode_t);
        va_end(ap);
        return real_open(redirect_path(path), flags, mode);
    }
    return real_open(redirect_path(path), flags);
}

int open64(const char *path, int flags, ...) {
    static int (*real_open64)(const char *, int, ...) = 0;
    mode_t mode = 0;
    if (!real_open64) real_open64 = dlsym(RTLD_NEXT, "open64");
    if (flags & O_CREAT) {
        va_list ap;
        va_start(ap, flags);
        mode = va_arg(ap, mode_t);
        va_end(ap);
        return real_open64(redirect_path(path), flags, mode);
    }
    return real_open64(redirect_path(path), flags);
}

int openat(int dirfd, const char *path, int flags, ...) {
    static int (*real_openat)(int, const char *, int, ...) = 0;
    mode_t mode = 0;
    if (!real_openat) real_openat = dlsym(RTLD_NEXT, "openat");
    if (flags & O_CREAT) {
        va_list ap;
        va_start(ap, flags);
        mode = va_arg(ap, mode_t);
        va_end(ap);
        return real_openat(dirfd, redirect_path(path), flags, mode);
    }
    return real_openat(dirfd, redirect_path(path), flags);
}

int openat64(int dirfd, const char *path, int flags, ...) {
    static int (*real_openat64)(int, const char *, int, ...) = 0;
    mode_t mode = 0;
    if (!real_openat64) real_openat64 = dlsym(RTLD_NEXT, "openat64");
    if (flags & O_CREAT) {
        va_list ap;
        va_start(ap, flags);
        mode = va_arg(ap, mode_t);
        va_end(ap);
        return real_openat64(dirfd, redirect_path(path), flags, mode);
    }
    return real_openat64(dirfd, redirect_path(path), flags);
}

int execve(const char *path, char *const argv[], char *const envp[]) {
    static int (*real_execve)(const char *, char *const [], char *const []) = 0;
    const char *new_path = redirect_path(path);
    if (!real_execve) real_execve = dlsym(RTLD_NEXT, "execve");
    return real_execve(new_path, redirect_argv(new_path, path, argv), envp);
}

int execv(const char *path, char *const argv[]) {
    static int (*real_execv)(const char *, char *const []) = 0;
    const char *new_path = redirect_path(path);
    if (!real_execv) real_execv = dlsym(RTLD_NEXT, "execv");
    return real_execv(new_path, redirect_argv(new_path, path, argv));
}

int execvp(const char *path, char *const argv[]) {
    static int (*real_execvp)(const char *, char *const []) = 0;
    const char *new_path = redirect_path(path);
    if (!real_execvp) real_execvp = dlsym(RTLD_NEXT, "execvp");
    return real_execvp(new_path, redirect_argv(new_path, path, argv));
}
} $gcc_path $gxx_path]
    close $fd
}

set rtl_file [file join $repo_root vivado_ip rtl gemv_q8_0_stream_core.v]
set tb_wrapper_file [file join $repo_root vivado_ip tb tb_gemv_q8_0_stream_core.v]
set tb_sv_file [file join $repo_root vivado_ip tb tb_gemv_q8_0_stream_core.sv]
set golden_dir [file join $repo_root pycharm golden fake_gemv]
set xsim_gcc_dir "/tools/Xilinx/Vivado/2024.2/tps/lnx64/gcc-9.3.0/bin"

foreach required [list $rtl_file $tb_wrapper_file $tb_sv_file] {
    if {![file exists $required]} {
        fail_result $result_file "missing HDL file: $required"
    }
}

foreach required [list \
    [file join $golden_dir input_i16.bin] \
    [file join $golden_dir weight_q8_fpga_layout.bin] \
    [file join $golden_dir scale_q_i32.bin] \
    [file join $golden_dir output_scaled_ref_i32.bin] \
    [file join $golden_dir output_block_acc_ref_i32.bin] \
] {
    if {![file exists $required]} {
        fail_result $result_file "missing golden file: $required"
    }
}

if {![file executable [file join $xsim_gcc_dir gcc]]} {
    fail_result $result_file "missing Vivado xsim GCC: [file join $xsim_gcc_dir gcc]"
}

foreach hdl_file [list $rtl_file $tb_wrapper_file $tb_sv_file] {
    set fd [open $hdl_file r]
    set text [read $fd]
    close $fd
    if {[regexp -nocase {(^|[^A-Za-z0-9_])(real|shortreal|float|floating-point)([^A-Za-z0-9_]|$)} $text -> pre token post]} {
        fail_result $result_file "forbidden HDL token '$token' found in $hdl_file"
    }
}

array unset tb_modules
foreach tb_file [list $tb_wrapper_file $tb_sv_file] {
    set fd [open $tb_file r]
    set text [read $fd]
    close $fd
    foreach match [regexp -all -inline -line {^[ \t]*module[ \t]+([A-Za-z_][A-Za-z0-9_$]*)} $text] {
        if {[regexp {^[ \t]*module[ \t]+([A-Za-z_][A-Za-z0-9_$]*)} $match -> module_name]} {
            if {[info exists tb_modules($module_name)]} {
                fail_result $result_file "duplicate testbench module '$module_name' in $tb_modules($module_name) and $tb_file"
            }
            set tb_modules($module_name) $tb_file
        }
    }
}

set sim_project_dir [file join $repo_root logs gemv_sim_project]
file delete -force $sim_project_dir
set xsim_run_dir [file join $sim_project_dir gemv_q8_0_sim.sim sim_1 behav xsim]
file mkdir $xsim_run_dir

set run_status "PASS"
set run_message "Prompt 09 GEMV simulation passed.\nRTL: $rtl_file\nTB: $tb_wrapper_file\nGolden: $golden_dir"

if {[catch {
    if {[info exists ::env(XILINX_VIVADO)]} {
        set vivado_root [file normalize $::env(XILINX_VIVADO)]
    } else {
        set vivado_root "/tools/Xilinx/Vivado/2024.2"
    }
    set vivado_bin [file join $vivado_root bin]
    set vivado_libroot [file join $vivado_root lib lnx64.o]
    set vivado_libdir [exec [file join $vivado_bin ldlibpath.sh] $vivado_libroot]
    set shared_data [file normalize [file join $vivado_root .. .. SharedData data]]
    set rdi_datadir [file join $vivado_root data]
    if {[file exists $shared_data]} {
        set rdi_datadir "$rdi_datadir:$shared_data"
    }

    set glbl_file [file join $vivado_root data verilog src glbl.v]
    if {![file exists $glbl_file]} {
        fail_result $result_file "missing Vivado glbl.v: $glbl_file"
    }

    set gcc_redirect_c [file join $log_dir xsim_gcc_redirect.c]
    set gcc_redirect_so [file join $log_dir xsim_gcc_redirect.so]
    write_gcc_redirect_source $gcc_redirect_c [file join $xsim_gcc_dir gcc] [file join $xsim_gcc_dir g++]
    run_command [file join $log_dir xsim_gcc_redirect_build.log] [list \
        [file join $xsim_gcc_dir gcc] -shared -fPIC -O2 \
        -I/usr/include/x86_64-linux-gnu \
        -B/usr/lib/x86_64-linux-gnu/ \
        -L/usr/lib/x86_64-linux-gnu \
        -o $gcc_redirect_so $gcc_redirect_c -ldl \
    ]

    set prj_file [file join $xsim_run_dir tb_gemv_q8_0_stream_core_vlog.prj]
    set include_dir [file join $repo_root vivado_ip tb]
    write_text_file $prj_file [format {# compile verilog/system verilog design source files
verilog xil_defaultlib -i "%s" "%s"
sv xil_defaultlib -i "%s" "%s"
verilog xil_defaultlib "%s"
nosort
} $include_dir $rtl_file $include_dir $tb_wrapper_file $glbl_file]

    write_text_file [file join $xsim_run_dir xsim.ini] [format {uvm=%s
xil_defaultlib=xsim.dir/xil_defaultlib
} [file join $vivado_root data xsim system_verilog uvm]]

    set old_cwd [pwd]
    cd $xsim_run_dir

    run_command [file join $xsim_run_dir compile.log] [list \
        [file join $vivado_bin xvlog] --incr --relax -L uvm -prj $prj_file \
    ]

    set saved_ld_preload_exists [info exists ::env(LD_PRELOAD)]
    if {$saved_ld_preload_exists} {
        set saved_ld_preload $::env(LD_PRELOAD)
    }
    set ::env(RDI_DATADIR) $rdi_datadir
    set ::env(RDI_APPROOT) $vivado_root
    set ::env(RDI_BINROOT) $vivado_bin
    set ::env(RDI_BINDIR) $vivado_bin
    set ::env(RDI_LIBDIR) $vivado_libdir
    set ::env(RDI_BASEROOT) [file dirname $vivado_root]
    set ::env(RDI_INSTALLROOT) [file dirname [file dirname $vivado_root]]
    set ::env(RDI_INSTALLVER) [file tail $vivado_root]
    set ::env(RDI_SHARED_DATA) [file normalize [file join $vivado_root .. .. SharedData]]
    set ::env(LD_LIBRARY_PATH) $vivado_libdir
    set ::env(LD_PRELOAD) $gcc_redirect_so
    set ::env(PATH) "$xsim_gcc_dir:$vivado_bin:$::env(PATH)"

    set xelab_status [catch {
        run_command [file join $xsim_run_dir elaborate.log] [list \
            [file join $vivado_bin unwrapped lnx64.o xelab] \
            --incr --debug typical --relax --mt 8 \
            -L xil_defaultlib -L uvm -L unisims_ver -L unimacro_ver -L secureip \
            --snapshot tb_gemv_q8_0_stream_core_behav \
            xil_defaultlib.tb_gemv_q8_0_stream_core xil_defaultlib.glbl \
            -log elaborate.log \
        ]
    } xelab_error]
    if {$saved_ld_preload_exists} {
        set ::env(LD_PRELOAD) $saved_ld_preload
    } else {
        unset -nocomplain ::env(LD_PRELOAD)
    }
    if {$xelab_status != 0} {
        error $xelab_error
    }

    set simulate_log [file join $xsim_run_dir simulate.log]
    run_command [file join $xsim_run_dir simulate_stdout.log] [list \
        [file join $vivado_bin xsim] tb_gemv_q8_0_stream_core_behav \
        --runall --testplusarg "GOLDEN_DIR=$golden_dir" --log simulate.log \
    ]

    cd $old_cwd

    if {![file exists $simulate_log]} {
        error "simulate.log not found: $simulate_log"
    }
    set sim_fd [open $simulate_log r]
    set sim_text [read $sim_fd]
    close $sim_fd
    if {[regexp {\[FAIL\]} $sim_text]} {
        error "testbench reported FAIL; see $simulate_log"
    }
    foreach required_pass [list \
        {\[PASS\] mode=0 scaled outputs matched golden/fake_gemv} \
        {\[PASS\] mode=1 block-acc outputs matched golden/fake_gemv} \
        {\[PASS\] tb_gemv_q8_0_stream_core completed} \
    ] {
        if {![regexp $required_pass $sim_text]} {
            error "missing expected PASS line: $required_pass"
        }
    }
    set run_message [join [list \
        "Prompt 09 GEMV simulation passed." \
        "RTL: $rtl_file" \
        "TB: $tb_wrapper_file" \
        "Golden: $golden_dir" \
        {[PASS] mode=0 scaled outputs matched golden/fake_gemv} \
        {[PASS] mode=1 block-acc outputs matched golden/fake_gemv} \
        {[PASS] tb_gemv_q8_0_stream_core completed} \
    ] "\n"]
    catch {close_sim -quiet}
} sim_error]} {
    set run_status "FAIL"
    set run_message "Vivado simulation failed:\n$sim_error"
}

if {[string equal $run_status "PASS"]} {
    write_result $result_file $run_status $run_message
    puts $run_message
} else {
    write_result $result_file $run_status $run_message
    error $run_message
}
