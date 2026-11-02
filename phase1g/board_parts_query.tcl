puts "=== BOARD PARTS matching kv260 ==="
set bl [get_board_parts -quiet *kv260*]
if {[llength $bl] == 0} { puts "NO kv260 board part installed" } else {
  foreach b $bl { puts "  $b" }
}
puts "=== BOARD PARTS matching k26 ==="
set bl2 [get_board_parts -quiet *k26*]
foreach b $bl2 { puts "  $b" }
puts "=== DONE ==="
