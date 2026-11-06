# R6 M2 + f30：真实 Gemini → 128路触觉基线

本目录冻结的是2026-11-06已加载的M2/DGM2固件及f30运行程序。它是在D1上增量形成的版本，不包含未构建成功、未加载的C3候选。旧平台、4D及11月3日统一候选保留在仓库原位置。

## 实际结果

| 层次 | 结果与边界 |
|---|---|
| Gemini/ARM/PL/PCA | 最新60秒：901采集、346处理、297次校验后的PL事务；39次UNKNOWN拒绝；1次源恢复、1次窗口续约 |
| 下游 | TCA CH1/CH3对应8片PCA可用；I²C错误0；结束后8片共512字节FULL_OFF精确读回 |
| 物理观察 | 用户确认本次“已震动”；此前8→32→64→128范围扩展及10分钟响应/恢复获用户确认 |
| 历史10分钟 | 2830次校验事务。采集、控制和用户观察分别记录，见evidence目录 |
| 尚未完成 | CH0/CH2的另128路；逐物理坐标一一验收；冷启动无人值守；SDK可靠释放；正式曝光帧龄证明 |

这是有监督、最低非零等级的DGM2联调基线，保留UNKNOWN关闭、CRC、序号、授权、故障停止和FULL_OFF。正式KPF1/FRESH仍被门禁阻断，不能把这次联调当作生产新鲜度验收。

## 从哪里看代码

| 入口 | 内容 |
|---|---|
| [OPEN_M2_VIVADO.cmd](OPEN_M2_VIVADO.cmd) | 生成/打开独立的Vivado审阅XPR；不会综合、实现或下载 |
| [板级顶层](fpga_c2/rtl/kv260_unified_board_top.sv) | PS BD、总线、复位、IOBUF和引脚guard |
| [约束目录](fpga_c2/constraints/) | SCL=H12、SDA=B10及CDC约束；不是只有空的constrs_1 |
| [M2适配器](fpga_c2/rtl/core/perception_axi_runtime_adapter.sv) | DGM2接收、矩阵校验、原子提交与控制门禁 |
| [触觉流水线](fpga_c2/rtl/core/kv260_haptic_pipeline.v) | Matrix Buffer→Map/Duty ROM→Scheduler→TCA/PCA突发→I²C |
| [C++源码](native/README.md) | bounded60与continuous600源码分别绑定实际ELF；共享深度几何核心 |
| [运行主入口](runtime_night/run_d1_live.py) | 父监督器、相机子进程、最新帧、矩阵提交、恢复和最终关闭 |
| [质量/矩阵](runtime_control/digital_matrix.py) | 保留距离/UNKNOWN，屏蔽离线分支，最低等级与目标掩码 |
| [运行说明](RUNBOOK.md) | 准备密封包、当前启动门禁、停止与恢复边界 |
| [证据](evidence/README.md) | 实测摘要及原证据哈希，不包含原始相机数据或串口原日志 |

实际使用逻辑掩码0xF0F0，覆盖CH1/CH3的8片PCA。矩阵到block的关系以ROM/软件反向映射为准；不能把逻辑128位范围直接解释成连续物理半边。

## 版本与复核

- 设计ID：0x52364D32；固件应用kv260-round6-matrixd2-20261106。
- 实际BIN SHA256：eb18234ff0d11adfb2167470e7a4ec3a213801c7ef89476263925673e4ed2021。
- f30运行入口SHA256：f30aa975288d770433fd235ea78edd758f1344802efec755f1124f87815ee300。
- [部署归档](deploy/M2_runtime_package.tar.gz)包含实际BIN、DTBO和shell.json；[产物清单](deploy/M2_ARTIFACT_MANIFEST.json)记录原BIT/XSA哈希。本次发布不重新构建、不重新加载。
- [来源清单](manifests/SOURCE_ORIGIN.json)记录D1基线、M2增量及密封运行文件；[native来源](native/SOURCE_ORIGIN.json)防止混用两套ELF源码。
- 厂商SDK v1.10.37及sysroot由使用者按许可另备，不随公开仓库分发。SDK库/XML哈希保持运行时校验，不修改驱动、USB权限或固件。

本轮实际执行57项Host测试、22个RTL案例/1983项检查，均通过；见manifests/PUBLICATION_VALIDATION.json。新机器Vivado GUI与完整重构建未在本轮执行，不能据此宣称可逐字节重建原Bitstream。
