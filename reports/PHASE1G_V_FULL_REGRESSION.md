# Phase 1G-V 完整 RTL 回归

**日期**：2026-11-01  **回归对象**：Phase 1G-U Lite 最新 RTL（含 `wdog_heartbeat`）
**结论**：**6/6 项全部通过，RTL 可冻结**

## 0. 回归基线（冻结前 SHA256）

`rtl/kv260_haptic_top.v` = `cdfb57cbfdc014b06c533a2374c308e51a1eefc339c2571936afed6352399dfb`
RTL 文件数 = **21**

完整 SHA256 清单见本文件末尾「附录 A」。

## 1. Phase 1B：21 项基础回归

`tb_21item_p1d.v` —— **未修改任何断言**

```
checks=21  errors=0
=== TB RESULT: PASS ===
```

21/21 全 PASS。日志：`phase1g/v_21item.log`

## 2. Phase 1D：I2C 378.7 kHz 时序

| 指标 | 要求 | 实测（min） | 结果 |
|---|---|---|---|
| SCL 频率 | ≤400 kHz | 周期 **2639.9 ns** → **378.79 kHz** | **PASS** |
| t_LOW | ≥1300 ns | **1319.9 ns** | **PASS** |
| t_HIGH | ≥600 ns | **1319.9 ns** | **PASS** |
| SDA data setup | ≥100 ns | **1309.6 ns** | **PASS** |
| START setup | ≥600 ns | **1319.9 ns** | **PASS** |
| STOP setup | ≥600 ns | **5321.0 ns** | **PASS** |

日志里 `avg` 值（260.18 kHz）被帧间间隙污染，**min 值才是真实总线时序**。

## 3. Phase 1E：16 块 PCA9685 初始化

```
init_done=1 healthy=1 online=ffff fault=0  t_init=48.287 ms
addr_bytes=119  data_bytes=1281  acks=1502  tca_selects=17
```

- 16/16 块全部被寻址
- 16/16 `PRE_SCALE = 0x79`（121 → 50.03 Hz）
- 16/16 `MODE2 = 0x04`（OUTDRV）
- 16/16 首次 `MODE1 = 0x30`（SLEEP|AI）
- TCA 选通 17 次 = 1 次去选 + 16 次单通道

**PHASE1E RESULT: checks=11 errors=0 → PASS**（`phase1g/v_phase1e.log`）

## 4. 256 通道 FULL_OFF

```
FULL_OFF violations: 0 / 256
```
160 个通道寄存器组（16 块 × 16 通道 × 4 寄存器）在 `init_done` 上升沿快照，
全部满足 `LEDn_ON=0x000`、`LEDn_OFF_H=0x10`。**0 违规 → PASS**

## 5. F1–F4 故障注入

| 故障 | 注入方式 | 检出 | 检出代码 |
|---|---|---|---|
| F1 错误 PRE_SCALE | `0x79 → 0x7A` | **CAUGHT** | `F1_PRESCALE` |
| F2 TCA 多通道 | 掩码 → `0x03` | **CAUGHT** | `F2_TCA_MULTI` |
| F3 初始化事务丢失 | 丢弃 BLK01 全部字节 | **CAUGHT** | `BLK_MISSING` |
| F4 FULL_OFF 损坏 | `LED0_OFF_H 0x10 → 0x00` | **CAUGHT** | `F4_FULL_OFF` |

**基线（黄金数据）：PASS，0 违规。** 每项故障各命中**唯一**对应代码，无误报。
**CHECKER SENSITIVITY: PASS（4/4）**，日志：`phase1g/v_faultinject.log`

> 方法学修正：本轮首次运行时基线报了 10 处违规，原因是初始化窗口截止时间仍用旧值
> `37474 µs`，而 Phase 1F 修复延时后 `init_done` 已移到 **48.287 ms**。
> 改用正确截止值后基线干净。**这是参数错误，不是 RTL 问题。**

## 6. 心跳与控制逻辑联合仿真

新增 `tb_joint_hb_ctrl.v`（不改功能 RTL），在真实 I2C 初始化流量下监测心跳：

```
bus_bytes=331  hb_rises=12  hb_period avg=999.934 us min=999.934 max=999.934
=== JOINT RESULT: checks=8 errors=0 → PASS ===
```

| 检查 | 结果 |
|---|---|
| J1 复位中心跳为低 | PASS |
| J2 I2C 总线确实活跃（331 字节） | PASS |
| J3 I2C 繁忙时心跳持续运行 | PASS |
| J4 心跳周期不受 I2C 影响（±2%） | PASS（999.934 µs） |
| J5 抖动极小（min=max） | PASS |
| J6 初始化期间 OE# 仍默认关断 | PASS |
| J7 控制活跃时停振 → 心跳停止 | PASS |
| J8 恢复时钟 → 心跳恢复 | PASS |

**结论：心跳与 PCA9685 控制链路完全解耦，新增心跳未破坏任何原有行为。**

## 7. 保留的已知验证局限（不得当作已证明）

| # | 项 | 局限 | 状态 |
|---|---|---|---|
| 1 | **T5b** | 计数器用的是寄存器指针字节（`0xFE`），非真实 PRE_SCALE 数据写 | 弱校验 |
| 2 | **T8c** | 该轮 `init_healthy=0`，只验证了 fail-closed 分支 | 空洞 |
| 3 | **T10b** | `oe_n==1` 与复位默认相同，OE 从未使能 | 空洞 |
| 4 | **T11** | 同上 | 空洞 |
| 5 | 21 项 TB 的 `I2C_TIMEOUT_US=200` / `FS_TIMEOUT_MS=2` | 小到使初始化无法完成，故 T8c/T10b/T11 必然走安全分支 | 配置局限 |
| 6 | **板级 I/O 时序** | 未做（J2 针号未确认） | **NOT VERIFIED** |
| 7 | **真实硬件** | 未上板 | **NOT VERIFIED** |
| 8 | IOBUF 电气特性 | 综合已推断 2 个 IOBUF，但真实驱动能力/上升沿未测 | **NOT VERIFIED** |

### 补充说明（重要）
本轮的 T10b/T11 仅在**21 项 TB 的短超时配置**下运行，因此属于空洞校验。
**真正的"故障→OE 关断"证据**来自：
- Phase 1B Gap-1 诊断（diag C：`oe_n=0 / armed=1` 正向使能）
- Phase 1G-U Lite 心跳测试（H6：停振 → 看门狗超时 → `pca_oe_n=1`）
- 联合仿真 J6/J7

这三者共同覆盖了 OE 安全路径，但**都不是 21 项断言本身**。

## 8. RTL 冻结决定

6/6 通过 → **冻结当前 RTL**，基线 SHA256 见附录 A。
进入 PS/PL Block Design 集成阶段。

冻结范围：
- `rtl/*.v`（21 个文件）—— **只读**
- `constraints/kv260_haptic_j2_candidate_v2.xdc` —— 候选约束（未加入工程）
- 任何变更必须走新的隔离阶段并重新回归

## 9. 边界声明

- 未修改任何功能 RTL（本轮仅新增 TB）
- 未修改任何测试断言
- **未生成部署 bitstream**
- 未执行 JTAG / `loadapp`
- 未连接 PCA9685 / 执行器
- 未修改供电
- AX7020 原工程未改动

## 附录 A：冻结 RTL SHA256

| 文件 | SHA256 |
|---|---|
| `axi_status_regs.v` | `57b15b840fc6104c47bf1ec5e19aab95bbd61609ea080f5966fdefd5bde91c2c` |
| `block_refresh_buffer.v` | `ff873640f523c1be3170c2f1da96f61dda61222f488bdb564fdf2cc4787427ec` |
| `dirty_detector.v` | `525c1401db9afba8ee1605e2f6801282208b8a4b328d298280f62fa0931adbe8` |
| `duty_lut_rom.v` | `96ff1cdce67dbcfc21e5c12782bc608dc135f29d3b6ab89e1f4c0403734f4122` |
| `failsafe_timer.v` | `e9f731463c98578b7038b6584256c5a399771ae14bff68d5ff2af70bbb45d90f` |
| `fault_recovery_ctrl.v` | `c622752364dd60a493b731c7aa54ba6cfe04bedec52148bbcb1428cf5586fe15` |
| `haptic_oe_guard.v` | `12fa6ca51b8cee00519333353b166ba91fe3e173dbf3e3a1e3a9a14184cfa853` |
| `i2c_fault_monitor.v` | `078c48cff70e2dfc5e398ed7a7e416a0be79812c3427ef6111e9d5ab1d9e5f6c` |
| `i2c_master.v` | `b0282ba70f368f761036b52729b276478225fe388140a6065dd4d20b9f75ccae` |
| `i2c_transaction_builder.v` | `cafbc7be5851751a540190e02267dc55adba2b2e8ad30caf7bf57c9931aced1d` |
| `kv260_haptic_pipeline.v` | `899240a2d94c5535b18686c8251a38c49872bb52e4767902c33a9151fe71f0d7` |
| `kv260_haptic_top.v` | `cdfb57cbfdc014b06c533a2374c308e51a1eefc339c2571936afed6352399dfb` |
| `matrix_buffer.v` | `bf7564d6b3d3fdc28b76655587f434efc371e87abc292c13514c3108fcf9a394` |
| `matrix_buffer_axi.v` | `53dc927cdfe4a1c098331910ae86be8b43dd722cf53abee6e46307210cb67397` |
| `matrix_map_rom.v` | `5d9ed61f2c48dcc3cdc554396d0f786daef988af1599b21b4569a0d96cc2116b` |
| `pca9685_burst_builder.v` | `3fd532529719775a069747cbb69301b9f6191dfe18b1066cbf1c604f7eea850a` |
| `pca_init_sequencer.v` | `a245e27123b9ad749cae6dddfe005061d46d25eb7035dbef0935b17826c1eeaf` |
| `scheduler_fsm.v` | `2c2557057f2a846a2814a81a4ec6ca1d2f6bf0806df9685cfda36ef66090d8eb` |
| `tactile_render_top.v` | `ab16f6327b9e8256294b6b254f58234bd9b469007d2c6bb6688f813521cc6320` |
| `tactile_v12_ctrl.v` | `d12212aa568e3f10ff97fb97ae70cbc7f0d63ccde8ecec6e91cf82be0350fea8` |
| `tca9548_inject.v` | `6d51f4a3c058e488ba5b36a485dbca5c28f98695b8d7b6c1135192557410fb78` |

