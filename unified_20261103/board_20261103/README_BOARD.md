# KV260真实板级工程补齐

上一级review工程是内部逻辑仿真项目，没有PS BD/IOBUF/XDC，不能作为完整板级工程。此目录补齐这些缺失；原review与原成功板卡版本均保留。

## 打开哪里

构建通过后双击 `Open_Board_Vivado.cmd`，或打开 `LATEST_BUILD.json`中真实project路径。新顶层是 **kv260_unified_board_top**，不再把AXI总线和内部clock/reset作为外部GPIO。

```text
PS HPM0_LPD（保持原128位配置）→ SmartConnect
  ├─ 0x9F000000/64K：统一KPF1接收与触觉桥接
  ├─ 0x9C000000/64K：触觉状态，只读
  └─ 0x9E000000/64K：诊断GPIO、原复位覆盖、恢复请求

统一核心 → 唯一开漏IOBUF → 两根实际顶层IIC端口
IOBUF.O → meta → sync → 原IIC核心及诊断回读
```

## 真实约束

文件：`constraints/kv260_unified_pmod.xdc`。

| 顶层端口 | 器件封装脚 | 电气标准 | 驱动/边沿 | 上拉 |
|---|---|---|---|---|
| i2c_scl_io | H12 | LVCMOS33 | 4mA / SLOW | 弱PULLUP辅助 |
| i2c_sda_io | B10 | LVCMOS33 | 4mA / SLOW | 弱PULLUP辅助 |

映射依据是官方KV260 BIST和K26 BoardStore，字节副本与哈希位于platform/manifests。封装脚不等于实体连接器针号；本轮未新增接线、测量VCCO或验收外部上拉。

只有这两个实际外部端口，没有给内部AXI/clock/reset编造PACKAGE_PIN，也没有猜测OE/watchdog新引脚。异步例外只切pad到第一级meta寄存器D；后级保留时序。未降低NSTD-1/UCIO-1，未添加虚构0ns外部延时。

## PS、复位与时钟

PS配置来自已验收参考XCI/BD，生成器正确拼接XCI的长字符串分片；真实Vivado对象对比1692项，发现差异立即停止。未执行任何PS初始化程序、AFI/OEN或硬件寄存器写入。

总线沿用16拍startup，payload reset沿用 `startup & (同步PS resetn | 原GPIO bit31复位覆盖)`。bit29仅申请原故障FSM完整重初始化；bit30不放行物理输出。若payload reset仍低，未来板测禁止直接读邮箱。

PS静态ACT_FREQ为96.968727MHz、请求100MHz，保持基线；实现的pre-opt沿用100MHz内部时序预算，不能把预算当成实际板上时钟。运行250ms门槛前必须核验实际PL0=100MHz，当前CLOCK_RUNTIME=NOT_VERIFIED。

## 默认行为与未完成验收

板级代码仍固定 `SIM_CONTROL_ENABLE=0`、`ACTUATOR_PINS_ENABLE=0`，OE保持禁止，因此IOBUF默认高阻；综合可能优化掉不可能启动的控制分支。源码层次、时序、bitstream生成均不能用作真实输出许可或振动证据。

本轮不加载bitstream、不操作串口/JTAG/USB相机、IIC或执行器，不改QSPI/SD。真实TCA地址ACK、PCA寄存器、物理OE及马达尚未验收。现有Linux实时runner也尚未针对新布局验收；旧helper依赖已移除的0x9D诊断IIC，不能直接运行。

## 复现与证据

`python -B -X utf8 scripts/build_board.py --stage bitstream` 在全新project/run中创建BD、生成wrapper、综合、实现、时序/DRC/IO核查及bitstream，任何失败保留日志；不打开硬件。

`tests/run_shell_test.py`仅使用PS与IOBUF模型验证外壳，48项通过，不是PS/USB/电气或马达实测。原统一核心的14场景4443项RTL结果仍只代表离线模拟。

真实构建状态以 `LATEST_BUILD.json`、对应results/build目录内报告及最终BOARD_BUILD_ACCEPTANCE.json为准。

## 本轮已完成的实际构建

- 新板级项目：`projects/build_20261103T135845492277Z/kv260_unified_board.xpr`。
- 修正后的综合：0 Error、0 Critical Warning；板级外壳48项检查通过。
- 布局布线与bitstream生成通过：WNS +2.648ns、WHS +0.010ns；14745/14745可布线net完成，0 routing errors。
- H12/B10及两级同步器在路由后重新查询通过；USB四路overcurrent在路由后均为GND；1692项PS配置保持基线。
- NSTD-1/UCIO-1专门DRC为0项违例。完整DRC保留1项RTSTAT-10 Warning，涉及SmartConnect内部20条无可布线负载的reset-pipe网络，没有降级或隐藏规则。
- bitstream SHA256：`d13c0fa9fbda9513c51f647388d347c7880865f18bf7f1f9d29236f58981a9e1`。
- 最终只读校验Vivado退出码0，证据：`results/postcheck_20261103T143545009515Z/RESULT.json`。

构建过程中出现的参数导出、managed XDC和后处理API错误均有原始日志保留。最终一次route/bitgen已成功，随后仅修正了后处理查询并只读复验；上游脚本exit1没有改写成0，最终校验独立记录exit0。实际汇总为 `BOARD_BUILD_ACCEPTANCE.json: LOCAL_BUILD_ACCEPTANCE=PASS`。

**板卡从未加载本候选，物理输出仍默认禁止。以上不等于真实TCA/PCA或震动通过。**
