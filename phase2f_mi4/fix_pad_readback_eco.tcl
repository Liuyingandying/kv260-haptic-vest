# Minimal physical-boundary correction: internal consumers must use IOBUF.O,
# never the package-side IO net. No protocol/state/clock/address changes.
set moved 0
set bufs [get_cells -hier -filter {REF_NAME == IOBUF}]
if {[llength $bufs] != 2} {error "Expected exactly two IOBUFs"}
foreach b $bufs {
 set padnets [get_nets -segments -of_objects [get_pins $b/IO]]
 set sense [get_nets -of_objects [get_pins $b/O]]
 foreach p [get_pins -leaf -of_objects $padnets -filter {DIRECTION == IN}] {
  set c [get_cells -of_objects $p]
  set ref [get_property REF_NAME $c]
  if {$ref eq "INBUF"} {continue}
  if {$ref ne "FDRE" && $ref ne "FDSE"} {error "Unexpected pad-side load $p type=$ref"}
  set old [get_nets -of_objects $p]
  puts "ECO_MOVE $p FROM=$old TO=$sense"
  disconnect_net -net $old -objects $p
  connect_net -hierarchical -net $sense -objects $p
  incr moved
 }
}
if {$moved != 4} {error "Expected four misplaced sample loads; got $moved"}
puts "ECO_MOVED=$moved"
