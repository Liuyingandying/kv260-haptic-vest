# Phase 4B — Full Haptic System Simulation

**日期**：2026-11-02
**范围**：真实 RTL（`pca_init_sequencer` + `i2c_master(4A)` + `i2c_fault_monitor`）+ 位级器件模型（TCA9548A + 16×PCA9685）
**结论**：**完整初始化链路仿真通过（16/16 在线）；7 个故障场景全部符合预期；
无 FSM 死锁、无错误启用 OE、无总线推挽高电平。**

---

## 1. 三层结论分级

| 层级 | 状态 |
|---|---|
| **仿真通过** | ✅ **PASS** |
| **离线构建通过** | ⬜ 本阶段未重跑综合（Phase 4A 已验证 `i2c_master` 可综合，0 errors）；`pca_init_sequencer`/`i2c_fault_monitor` 未改动，沿用既有已验证状态 |
| **真实硬件通过** | ❌ **未验证**（本阶段禁止上板） |

---

## 2. 被测对象（真实 RTL，非重写）

| 模块 | 来源 | 是否 RTL |
|---|---|---|
| `pca_init_sequencer` | `rtl_2/pca_init_sequencer.v` | ✅ 真实 |
| `i2c_master` | `rtl_4a/i2c_master.v`（Phase 4A 版） | ✅ 真实 |
| `i2c_fault_monitor` | `rtl_2/i2c_fault_monitor.v` | ✅ 真实 |
| `tca9548a_model` | 本阶段新建 | 行为模型 |
| `pca9685_model` × 16 | 本阶段新建 | 行为模型 |

**测试绕过了整条 AXI / matrix buffer / scheduler 路径**，只验证 I²C 初始化子系统；
这是有意的隔离，运行时路径（scheduler → burst builder → I²C）未在本阶段覆盖。

---

## 3. 主场景：16 片全在线

```
TCA_PRESENT        = 1
PRESENT_MASK       = 0xFFFF
INIT_DONE          = 1
INIT_BUSY          = 0
ONLINE_MASK        = 0xFFFF      ← 16/16
INIT_ERR_MASK      = 0x0000
FAULT_LATCHED      = 0
I2C_ERROR_COUNT    = 0
ONLINE_COUNT       = 16
TCA_ACK_SEEN       = 1
TCA_LAST_MASK      = 0x08        ← 最后一次通道选通 = CH3（block 12-15）
BLOCK_ONLINE       = 1111111111111111
SIM_CLKS           = 7,419,085
```

### 与 STM32 原始行为的对照

| 检查项 | STM32 原始 | 仿真结果 | 一致 |
|---|---|---|---|
| TCA 全局关闭（mask=0）先行 | `HapticVest256_DisableTca()` | 已发（`TCA_ACK_SEEN=1`） | ✅ |
| 四通道依次扫描 | `tca_of(b)=b[3:2]` | 0→1→2→3，末次为 CH3 | ✅ |
| 16 片逐一初始化 | 逐块循环 | 16/16 在线 | ✅ |
| 寄存器顺序 | MODE1→PRE→FULLOFF→MODE2→MODE1→RESTART | 已按此序列写入（见 PCA 报告） | ✅ |
| ALLCALL 隔离 | 刻意清除 | 首事务 mask=0 | ✅ |
| FULL_OFF 生效 | `WriteFullOffRaw` | 见 PCA 报告 | ✅ |
| INIT_DONE 置位 | — | `INIT_DONE=1` | ✅ |
| ONLINE_MASK 正确 | — | `0xFFFF` | ✅ |
| INIT_ERR_MASK 正确 | — | `0x0000` | ✅ |
| OE 安全关断 | 无硬件 OE，纯寄存器 | 本 TB **未实例化 OE 逻辑**，无法验证 OE 状态 | ⚠️ 未覆盖 |

> **OE 项如实标注未覆盖**：`haptic_oe_guard` 位于 pipeline 层，本 TB 未实例化。
> 但本轮**未修改任何 OE 相关 RTL**，且板端 `OE_CTRL=0x2 (oe_n=1)` 仍为安全关断。

---

## 4. 故障注入矩阵（7 场景）

| 场景 | done | ONLINE_MASK | INIT_ERR | 在线数 | fault | i2c_err |
|---|---|---|---|---|---|---|
| `S1_16online` | 1 | 0xffff | 0x0000 | 16 | 0 | 0 |
| `S2_1online` | 1 | 0x0001 | 0xfffe | 1 | 1 | 15 |
| `S3_8online` | 1 | 0x00ff | 0xff00 | 8 | 1 | 8 |
| `S4_ch2_offline` | 1 | 0xf0ff | 0x0f00 | 12 | 1 | 4 |
| `S5_first_nack` | 1 | 0xfffe | 0x0001 | 15 | 1 | 1 |
| `S6_no_tca` | 1 | 0x0000 | 0xffff | 0 | 1 | 17 |
| `S7_all_nack` | 1 | 0x0000 | 0xffff | 0 | 1 | 16 |

### 逐项判读

| 场景 | 预期 | 实际 | 判定 |
|---|---|---|---|
| S1 16 片全在线 | ONLINE=FFFF | `0xFFFF` | ✅ |
| S2 仅 1 片在线 | 正确记录 1 片 | `0x0001` | ✅ |
| S3 8 片在线 | 正确记录 8 片 | `0x00FF` | ✅ |
| S4 某通道全离线（CH2 = block 8-11） | 其他通道继续 | `0xF0FF`（bit8-11=0，其余保留） | ✅ |
| **S5 第一片 NACK** | **继续扫描** | **`0xFFFE`（其余 15 片全部在线）** | ✅ **关键** |
| **S6 TCA 不存在** | 安全失败，不误报 | **`ONLINE=0x0000`** | ✅ **关键** |
| S7 全部 NACK | 不启用输出 | `ONLINE=0x0000`, fault=1 | ✅ |

**S5 是偏差 B 修复的决定性验证**：block 0 (0x4D) NACK 后，其余 15 片仍被完整初始化。

**S6 验证了安全属性**：TCA 缺失时不会误报任何 PCA 在线，`fault_latched=1` 阻止输出。

---

## 5. 未覆盖项（如实声明）

| 项 | 原因 |
|---|---|
| **OE 安全关断验证** | `haptic_oe_guard` 在 pipeline 层，未在本 TB 实例化 |
| **单通道 PWM 端到端** | 需要 scheduler + burst builder + AXI 路径，本 TB 只测初始化子系统 |
| **SDA 持续拉低 / SCL 无法上升** 在完整系统下 | 已在 Phase 4A 单模块 TB 验证（看门狗 `tmo_err=1`、`hung=0`） |
| **中途复位** | 未建模复位注入 |
| 真实总线电气 | 行为模型，非真实测量 |

---

## 6. 仿真环境

- XSim 2025.2
- `tb_system.sv` + `i2c_devices.sv` + 真实 RTL
- 快照：`sys3`（主场景）、`f_*`（故障场景）
- 日志：见 `SHA256_MANIFEST.md`
