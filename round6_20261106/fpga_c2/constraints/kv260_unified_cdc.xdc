# 只在综合后使用，端点已从实际综合网表核验；不用通配或整组时钟豁免。
# 仅切异步pad到第一级D；meta→sync以及核心路径正常进行时序分析。
set_false_path -from [get_ports i2c_scl_io] -to [get_pins {scl_meta_reg/D}]
set_false_path -from [get_ports i2c_sda_io] -to [get_pins {sda_meta_reg/D}]
