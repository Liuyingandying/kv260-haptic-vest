# KV260统一逻辑链：独立集成候选

**板级补齐入口：** `board_20261103/`现在包含真实PS BD、板级IOBUF和两份XDC。`Open_Unified_Vivado.cmd`已改为只打开该板级项目，构建未通过时会拒绝打开，不再悄悄退回逻辑review。原逻辑工程仍可用 `Open_Logic_Review.cmd`阅读。实际构建结果以 `board_20261103/BOARD_BUILD_ACCEPTANCE.json`为准；未进行板卡加载或马达验收。

本目录将冻结感知软件、384B协议及4D触觉RTL放在同一独立工作区，并新增真正连接接收器与触觉矩阵缓冲的顶层。原工程、V1算法、质量bridge、codec、Golden和已加载板卡均未修改。

**当前交付层级：Host离线入口、RTL集成仿真，以及已完成PS BD/IOBUF/XDC、综合实现和bitstream生成的板级候选。板级构建不等于已经验收的板上完整系统，不能据此报告马达已经可用。**

## 现在怎么用

1. Windows双击 `Run_Offline_Acceptance.cmd`：检查保护哈希、运行Host测试、保存帧回放和RTL总线仿真。不会打开相机、串口、UIO或JTAG。
2. 双击 `Open_Unified_Vivado.cmd`：打开补齐的板级项目，综合顶层为 `kv260_unified_board_top`；其中内部核心仍为 `unified_perception_haptic`。原逻辑review入口改为 `Open_Logic_Review.cmd`。
3. 看 `runtime/unified_app.py`：保存BDP1 → 原C++核心 → 冻结质量桥接 → 冻结codec → 384B KPF1 / 96字仿真输入。LIVE模式明确拒绝。
4. 看 `rtl/unified_perception_haptic.sv`：一个KPF1接收入口连接到完整4D触觉管线，感知适配器是触觉矩阵的唯一写入者。

每次验收建立新的results目录，失败日志保留。`vivado/LATEST_REVIEW.json`记录实际创建的xpr和日志。

## 统一的数据路径

```text
冻结D455软件（保留参考，尚未接新板级运行器）
 / 保存BDP1 → 原C++ → 冻结质量桥接 → 384B KPF1
                                             ↓
统一AXI邮箱 → 原perception_axi_adapter
  ├─ 所有合法观察帧：保留矩阵、UNKNOWN、质量及身份，原子读回
  └─ COMPLETE且许可/健康/时效全部成立（仅TB允许控制）
        → 64个矩阵字 → COMMIT → 真实bank/generation确认
        → dirty比较 / scheduler / map / PWM LUT
        → block buffer → PCA字节组装 → TCA分支选择
        → IIC主机 → TB的理想开漏从机模型
```

PARTIAL可以有非零近障碍，仍只作为观察，不能取得实体输出许可。UNKNOWN不被编码成已知安全。断流/过期/重放沿用原session、sequence与250ms门槛。

## 必要整合修改

仅修改复制品 `rtl/kv260_haptic_pipeline.v`，新增统一模式和握手端口：

- 原adapter接管commit grant/discard，确认真实generation后再启动调度与续命，避免将旧buffer的“请求已接受”提前ack当作完整帧。
- 感知force_off进入既有抑制与FULL_OFF处理；健康来自实际初始化/故障/运行状态，不能常量伪造。
- grant等待整个下游空闲，包含最后一次IIC STOP。
- 新候选默认要求16块在线；统一模式启用TCA选择；100k单笔事务预算20ms（不改变250ms帧龄）。
- 原 `perception_axi_adapter.sv`、矩阵模块、scheduler、map、LUT、builder和质量契约保持原字节。
- 唯一参与编译的IIC主机是先前100k修正版 `i2c_master_100k_candidate.v`；旧master及旧顶层仅作参考，不在 `scripts/source_list.py`构建白名单中。

## 默认输出边界

- `SIM_CONTROL_ENABLE=0`：默认没有控制许可；只有离线TB显式置1。
- `ACTUATOR_PINS_ENABLE=0`：物理SCL/SDA拉低输出默认禁用。TB观察raw意图，并不驱动真实针脚。
- `oe_disable_o=1`固定：没有通过仿真许可打开实体OE的路径。
- 状态AXI只读，写入返回SLVERR；没有PS裸矩阵旁路绕过质量契约。

上述代码不构成已接好的OE电路。既有外部TCA地址NACK、真实供电/接线和马达关闭仍需实体验收，不能由仿真解决。

## 连续帧准入

冻结adapter接受新SEAL时会撤销旧控制；若在旧PCA刷新中途直接交付新COMPLETE，可能保守中止事务。因此新软件准入策略为：COMPLETE仅在整条管线空闲时交付，忙时保留最新一帧；交付前重新核算帧龄。PARTIAL/UNKNOWN/故障优先撤销，不等待刷新完成。

该策略的Host模型与测试不等于真实D455循环已应用。旧 `runtime/run_live_pl.py`和 `core/native_mailbox/live_mailbox.c`仅是原样冻结参考：其中IIC-disabled断言不能直接用于新架构，不能未经板级集成就启动。

忙时撤销可能使原IIC主机按受控abort结束，故障监控仍会闭锁。这种情况不是“再等空闲”就能继续：必须等全部数字FULL_OFF完成、状态进入HOLD（状态offset0x54低3位=5）、off_pending（0x38）清零且下游排空，然后对公开 `recovery_request_i` 给一次上升沿。该请求仅交给原恢复FSM，完整初始化成功后才解除fault；之后仍需新的session/RESUME帧。早到请求会被忽略，不会排队自动重试。物理OE仍禁止，数字FULL_OFF不是实体安全认证。

## 保存帧证据

`fixtures/saved_00.bdp`是原transfer_bundle的历史848×480、RGB对齐F32米制保存帧，非本轮相机采集。其session/时间字段是回放契约，绝不是当前曝光或实时新鲜度证据。生成结果为PARTIAL，保留85个UNKNOWN及218个非零单元；384B结果以fixture逐字节对照。

## 上板前仍需完成

1. PS BD、板级IOBUF与XDC已在board_20261103补齐，且完成综合实现；未来加载前仍需核对UIO布局、复位和真实PL时钟，本轮没有板测。
2. 完成上述准入策略到真实运行器、原生邮箱程序的适配，重新验证实时帧龄和逐帧读回。
3. 板级候选综合/实现/内部时序/管脚DRC及bitstream已完成；部署前仍需建立并验证恢复包、运行时配置和实体关闭条件。
4. 独立解决TCA首地址ACK；再验PCA初始化、FULL_OFF与实体OE/功率关断，然后才可能进行限时单马达验收。

**当前不能报告“相机到马达实板打通”，也不能拿Host速度当KV260实测。** 各项真实结果与日志在results及 `INTEGRATION_STATUS.json`。

## 第一阶段实际验收：逻辑整合

`Run_Offline_Acceptance.cmd`已实际完整执行，退出码0；日志为 `results/acceptance_20261103T133413395208Z/RESULT.json`。

- 46条保护快照校验通过，原工程源文件未改动。
- Host 33项测试通过（15项实际C++/桥接/协议测试＋18项最新帧准入测试）。
- 历史保存深度重新执行原核心和桥接，384B逐字节一致。
- RTL 4组、14场景、4443项检查通过：完整模拟PWM链、忙时撤销和完整重新初始化恢复、NACK、默认禁止输出、保存帧回放、断流和超龄。
- 第一阶段仅完成Vivado源码项目；后续板级补齐已另行完成综合/实现/bitstream生成。两个阶段均没有硬件访问。

RTL仿真使用1MHz时钟、TICKS_PER_US=1降低事件数，250ms规则未修改。IIC按100k配置，由真实FSM运行；不以该缩放仿真宣称实板频率或性能。

## 第二阶段实际验收：板级工程补齐

`board_20261103/BOARD_BUILD_ACCEPTANCE.json`记录实际PASS：PS参数1692项一致、H12/B10管脚约束和CDC已生效、路由后USB四GND、WNS +2.648ns/WHS +0.010ns、管脚DRC通过、bitstream已生成。具体项目、构建命令及全部失败/成功日志见该目录。默认输出仍禁止；没有加载板卡或操作马达。
