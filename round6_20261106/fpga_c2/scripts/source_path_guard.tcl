# Read-only project-object isolation guard. No property writes or run operations.
# Source after open/create and after adding new guard source; before reset_runs.
if {![info exists board_root]} {error "PATH_GUARD requires explicit board_root"}
set guard_root [file normalize $board_root]
if {[file tail $guard_root] ne "fpga_c2"} {error "PATH_GUARD requires independent R6 fpga root"}
proc guard_inside {path root} {
    set p [string map {\\ /} [file normalize $path]]
    set r [string trimright [string map {\\ /} [file normalize $root]] /]
    return [expr {[string equal -nocase $p $r] ||
                  [string match -nocase "${r}/*" $p]}]
}
if {[llength [get_projects -quiet]] != 1} {error "PATH_GUARD needs exactly one project"}
set guard_project_dir [file normalize [get_property DIRECTORY [current_project]]]
if {![guard_inside $guard_project_dir [file join $guard_root p]]} {
    error "PATH_GUARD project escaped: $guard_project_dir"
}
set guard_count 0
foreach guard_file [get_files -all -quiet] {
    set guard_name [get_property NAME $guard_file]
    if {![guard_inside $guard_name $guard_root]} {
        error "PATH_GUARD source/input escaped R6 fpga: $guard_name"
    }
    if {![file isfile $guard_name]} {error "PATH_GUARD missing source/input: $guard_name"}
    incr guard_count
}
if {$guard_count == 0} {error "PATH_GUARD no project input files"}
foreach guard_run [get_runs -quiet] {
    set guard_dir [get_property DIRECTORY $guard_run]
    if {![guard_inside $guard_dir [file join $guard_root p]]} {
        error "PATH_GUARD run directory escaped: $guard_run $guard_dir"
    }
    foreach guard_property [list_property $guard_run] {
        if {[string match *.TCL.PRE $guard_property] || [string match *.TCL.POST $guard_property] ||
            $guard_property eq "INCREMENTAL_CHECKPOINT"} {
            set guard_hook [get_property $guard_property $guard_run]
            if {$guard_hook ne "" && ![guard_inside $guard_hook $guard_root]} {
                error "PATH_GUARD run hook/checkpoint escaped: $guard_run $guard_property $guard_hook"
            }
        }
    }
}
puts "R6_PATHS_VERIFIED inputs=$guard_count project=$guard_project_dir root=$guard_root"
