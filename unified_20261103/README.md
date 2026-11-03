# KV260 统一感知与触觉控制：2026-11-03 源码发布

本目录是最新**独立候选**的可迁移源码包，保留已有仓库根目录的历史4D实现。相机/算法、384B KPF1、完整触觉管线、PS Block Design、IOBUF、XDC、测试与构建入口均包含在内。

**实现完成不等于实体系统已打通。** 本候选尚未加载KV260，仍固定禁止物理输出；真实TCA地址ACK、PCA PWM、OE关闭和马达震动尚未通过。已验收的旧板卡运行记录不迁移成新候选的实板PASS。

## 从这里阅读

| 功能 | 文件 |
|---|---|
| 板级顶层、实际开漏引脚及同步器 | [kv260_unified_board_top.sv](board_20261103/rtl/kv260_unified_board_top.sv) |
| H12/B10、LVCMOS33、DRIVE4、SLOW、辅助弱上拉 | [kv260_unified_pmod.xdc](board_20261103/constraints/kv260_unified_pmod.xdc) |
| pad到第一级同步器的限定CDC例外 | [kv260_unified_cdc.xdc](board_20261103/constraints/kv260_unified_cdc.xdc) |
| PS HPM0_LPD→SmartConnect及地址分配 | [create_board_bd.tcl](board_20261103/scripts/create_board_bd.tcl) |
| KPF1接收→矩阵原子提交→触觉管线 | [unified_perception_haptic.sv](rtl/unified_perception_haptic.sv) |
| 调度/PWM/TCA/PCA/IIC/故障恢复 | [kv260_haptic_pipeline.v](rtl/kv260_haptic_pipeline.v) |
| 保存BDP1→C++→质量契约→384B离线入口 | [unified_app.py](runtime/unified_app.py) |
| 最新帧优先、忙时准入、超龄及恢复模型 | [frame_admission.py](runtime/frame_admission.py) |
| 本地构建的真实验收证据 | [BOARD_BUILD_ACCEPTANCE.json](evidence/BOARD_BUILD_ACCEPTANCE.json) |

```text
历史保存BDP1 → 冻结C++深度核心 → 冻结quality_bridge/codec → 384B KPF1
  → perception_axi_adapter → 真实generation确认的矩阵双缓冲
  → scheduler/dirty/map/LUT → PCA burst → TCA select → 100k IIC master
  → board IOBUF → H12/B10（本候选默认高阻，未作真实外设验收）
```

## 干净克隆后的离线测试

需要Python 3.10+、支持C++17的g++、Icarus Verilog。无需相机、串口、SDK、sudo或开发板。

```powershell
cd unified_20261103
python -B -X utf8 scripts/run_all.py
python -B -X utf8 board_20261103/tests/run_shell_test.py
```

脚本从环境变量/PATH寻找工具，也保留原开发机路径作为fallback。可设置`CXX`、`IVERILOG`、`VVP`、`VIVADO_BAT`。Windows双击`.cmd`默认使用PATH中的Python；也可设置`PYTHON_EXE`。

Host C++可执行文件从包内源码生成到`results/host_core/`，不提交未知来源EXE。所有新测试输出写入独立results或带时间戳的测试子目录。

`fixtures/saved_00.bdp`是历史848×480 RGB对齐F32米制保存帧，非本次LIVE数据。对应期望结果保留85个UNKNOWN和218个非零单元；回放时间字段不构成当前曝光或实时帧龄证据。

## Vivado 2025.2 重建与打开

```powershell
python -B -X utf8 board_20261103/scripts/build_board.py --stage project
# 生成新工程、PS BD、两份XDC；不综合、不连接硬件

python -B -X utf8 board_20261103/scripts/build_board.py --stage bitstream
# 在新目录综合/实现/生成bitstream，不部署、不使用JTAG
```

目标器件`xck26-sfvc784-2LV-c`。PS参考XCI/BD、1692项配置及官方引脚参考均已vendored到`board_20261103/platform/`，不需要原E盘旧工程。不要把旧历史报告里的绝对XPR路径当作可搬迁入口；先执行上面的`--stage project`，再使用`Open_Unified_Vivado.cmd`打开新生成工程。

**Windows必须使用短ASCII工作目录**，例如将本目录复制为`E:\KV260_SRC`后构建。Vivado的生成IP文件仍受260字节限制；在长路径Git克隆目录直接生成BD已实际触发`Common 17-680`。这是路径限制，不是PS参数或RTL错误；本发布另以短路径验证工程创建，未通过修改系统设置或重做实现来规避。

## 地址与运行边界

- `0x9F000000/64KiB`：统一KPF1邮箱及受控触觉桥接。
- `0x9C000000/64KiB`：触觉状态，只读；**与原4D裸矩阵控制窗口语义不同**。
- `0x9E000000/64KiB`：诊断GPIO、既有复位覆盖和恢复请求。
- 没有旧版`0x9D000000` AMD AXI IIC实例。旧`core/native_mailbox/live_mailbox.c`及冻结`runtime/run_live_pl.py`仅供历史阅读，**尚未适配此新布局，不能直接部署运行**。
- 当前`runtime/unified_app.py --mode live`明确拒绝。新实时准入策略仍是Host验证模型，尚未接入真实D455循环。
- 固定`SIM_CONTROL_ENABLE=0`、`ACTUATOR_PINS_ENABLE=0`、`oe_disable_o=1`；PARTIAL/UNKNOWN不取得物理控制许可。
- 250ms新鲜度与90%覆盖门槛未降低。内部实现以100MHz签核，真实PL0时钟需上板重新确认。

## 证据范围

| 项目 | 已取得结果 |
|---|---|
| 原工作区逻辑集成 | Host33项；RTL4组14场景4443项检查通过 |
| 原工作区板级外壳 | 48项模拟检查通过 |
| 原工作区Vivado实现 | WNS +2.648ns；WHS +0.010ns；管脚NSTD/UCIO为0；bitgen成功 |
| 全DRC | 保留1项RTSTAT-10 Warning，没有降低规则严重性 |
| 本发布副本复验 | 见[发布验证](PUBLICATION_VALIDATION.json)，与原工作区历史结果分开 |
| 新候选板上加载、实际IIC ACK、马达输出 | NOT_VERIFIED |

原始通过/失败记录完整保留在本机；Git只携带精选最终证据。`evidence/`中的旧路径、命令和早期`BOARD_IMPLEMENTATION=NOT_RUN`是明确标注的历史事实，不能覆盖最新板级构建状态。源码仅作可迁移工具适配；RTL、算法、冻结质量bridge/codec和夹具字节变化须由manifest明确检出。

## 发布内容与RAR

Git不保存Vivado projects/runs/cache、DCP、编译EXE/VVP、大量深度采集或重复失败产物。RAR另含同一Git提交的完整工作树（包括仓库既有历史代码）及来源可追溯的7,797,835字节`.bit`，其SHA256：

`d13c0fa9fbda9513c51f647388d347c7880865f18bf7f1f9d29236f58981a9e1`

该bitstream来自**发布前已完成的实现**；本轮只调整发布工具，没有重新综合或加载。RAR中的`RELEASE_METADATA.json`记录精确Git提交、构建输入与二进制哈希。没有附加未经验证的DTBO/部署包。

原始集成说明：[README_UNIFIED.md](README_UNIFIED.md)。板级实现原始说明：[README_BOARD.md](board_20261103/README_BOARD.md)。
