# KV260统一板级工程：仅绑定实际板级顶层的两根开漏IIC端口。
# 官方来源：AMD KV260 BIST固定commit 60c3f54的kv260_bist_pin.xdc
# HDA11 / PMOD_GPIO[0] = H12；HDA15 / PMOD_GPIO[4] = B10；HDIO Bank45。
# 本地来源副本：E:/01_Projects/FPGA_Embedded/KV260_PMOD_Audit_20261011/kv_pinout/official_kv260_bist_pin.xdc
# J2实体针号/朝向属于第三方及既有观察佐证；不是本轮核验拟购revision XTP682。
# 本文件不分配未知OE、功率关断或watchdog针，也不把内部AXI/clk/reset分配为GPIO。
# 顶层数量/对象断言放在普通board_pre_opt.tcl，managed XDC不使用if/foreach。
set_property PACKAGE_PIN H12 [get_ports i2c_scl_io]
set_property PACKAGE_PIN B10 [get_ports i2c_sda_io]
set_property IOSTANDARD LVCMOS33 [get_ports {i2c_scl_io i2c_sda_io}]
set_property DRIVE 4 [get_ports {i2c_scl_io i2c_sda_io}]
set_property SLEW SLOW [get_ports {i2c_scl_io i2c_sda_io}]
# 弱上拉只用于释放态的辅助偏置，与历史pullup诊断一致；不能代替外部IIC上拉/供电核验。
set_property PULLTYPE PULLUP [get_ports {i2c_scl_io i2c_sda_io}]
# 时钟来自Zynq PS pl_clk0，频率/时序由PS IP生成的约束及实际时钟对象核验。
# 不创建不存在的板外100MHz时钟，不加虚构输入/输出延时或宽泛false_path。

# pad到同步器第一级的精确例外位于implementation-only的kv260_unified_cdc.xdc。
