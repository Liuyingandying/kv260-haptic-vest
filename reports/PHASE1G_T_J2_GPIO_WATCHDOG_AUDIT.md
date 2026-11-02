# Phase 1G-T：J2 通用 GPIO 重新评估与硬件看门狗引脚方案

**日期**：2026-11-01  **前置**：Phase 1G-S 报告
**新方案**：H12=SCL(J2.1)、B10=SDA(J2.2)、**B11=WDOG_HEARTBEAT(J2.8)**，OE# 改由外部看门狗派生

---

## 1. i2s_in / i2s_out 的性质审查（本轮核心问题）

### 1.1 官方 BoardStore 实测证据（`kv260_carrier/1.3/board.xml`）

| 证据 | 内容 | 含义 |
|---|---|---|
| 组件定义 L518 | `<component name="i2s_in" display_name="I2S RECEIVER" type="chip" sub_type="chip" major_group=""/>` | **空元素**：无 `<pins>`、无 `<description>` |
| 组件定义 L519 | `<component name="i2s_out" display_name="I2S TRASMITTER" .../>` | 同上 |
| 接口定义 L440 | `<interface mode="slave" name="i2s_in" type="...i2s_rtl:1.0" of_component="i2s_in" preset_proc="i2s_in_preset">` | 是 **BD 接口**，带可选 preset |
| 接口定义 L442 | `<preferred_ip vendor="xilinx.com" library="ip" name="i2s_receiver" order="0"/>` | 关联的是**待实例化的 PL IP**，不是板上芯片 |
| 连接 L582-583 | `som240_1_i2s_in_connection`，`c1_st_index=81` | 信号只连到 **SOM240 连接器**，无其他板上器件 |

**关键对比**：`som240_1_connector` 有 **240 个 `<pins>`**（真连接器）；
`i2s_in`/`i2s_out` 的 `<pins>` 数量为 **0**。

### 1.2 判定

**i2s_in / i2s_out 是"外部器件接口抽象"，不是载板上的固定物理负载。**

支持理由（全部来自实际 XML 结构，非名称推断）：
1. 组件零引脚 —— 板上若有固定 codec，必然出现在 pins 或另有 chip 组件
2. 带 `preferred_ip` —— 指向用户要自行实例化的 `i2s_receiver`/`i2s_transmiter` IP
3. 带 `preset_proc` —— 与 mipi/hda/bank45_gpio 同一机制，属 **BD 可选接口预设**
4. 只有一条 connection，且指向 SOM240 连接器 —— 即"信号被引到连接器供外部使用"

### 1.3 证据等级（严格区分）

| 结论 | 等级 | 说明 |
|---|---|---|
| i2s 组件零引脚、纯接口抽象 | **CONFIRMED** | BoardStore XML 结构实测 |
| i2s 带可选 preset、指向 PL IP | **CONFIRMED** | 同上 |
| **i2s 是"外接可选 Pmod 式功能"** | **INFERRED（强）** | 由上述结构推出；**UG1089 关于 Pmod/音频 Pmod 的正文本轮未取得**，故不升级为官方文字确认 |
| J2 上是否存在板载固定负载（串阻/ESD/上拉） | **UNKNOWN** | BoardStore 不含此信息，需 XTP682 |

---

## 2. 三条候选映射的证据等级

| Scheme | Package Pin | SOM240 网络 | Bank | 载板接口占用 | J2 针号 | **综合等级** |
|---|---|---|---|---:|---|---|
| `i2c_scl_pin` | **H12** | `som240_1_a17` (idx 11) | 45 | **无（FREE）** | J2.1（未证） | **CROSS-CHECKED** |
| `i2c_sda_pin` | **B10** | `som240_1_b20` (idx 23) | 45 | **无（FREE）** | J2.2（未证） | **CROSS-CHECKED** |
| `wdog_heartbeat` | **B11** | `som240_1_c22` (idx 37) | 45 | **`i2s_in`** | J2.8（未证） | **INFERRED** |

- 器件层（pin 存在 / Bank45 / LVCMOS33 合法）：三根均 **CONFIRMED**
- SOM240 层（pin ↔ 针名）：三根均 **CONFIRMED**（AMD `k26c/part0_pins.xml`）
- **J2 针号层：三根均 UNKNOWN**（BoardStore 无此信息，需 XTP682）
- **B11 复用前提**：确认不实例化 I2S Pmod 功能，且载板无固定负载 → 目前 **INFERRED**

### 2.1 对"J2 通用 GPIO"的判断

- H12、B10 未被任何载板接口引用 → **可作通用 GPIO 的把握最高**
- B11 被 `i2s_in` 引用 → **只有在不使用 I2S 时才可复用**；由于 i2s 是可选外部接口（§1），
  复用**在架构上成立**，但必须确认 J2 上无板上固定负载。

---

## 3. 硬件看门狗架构评估

目标链路：
```
FPGA wdog_heartbeat (B11) → 外部看门狗/可重触发单稳态 → PCA9685 OE#
```

### 3.1 六条硬性要求 vs 方案满足度

| # | 要求 | 单稳态方案(B) | 看门狗 IC 方案(A) |
|---|---|---|---|
| 1 | 上电默认关断 | ✅ Q 上电为低 → MOSFET 关 → OE# 由外部上拉为高 | ⚠️ 依器件；部分 IC 上电 /WDO 高阻，需确认 |
| 2 | 心跳正常后允许使能 | ✅ Q 高 → 使能 | ⚠️ 需反相（/WDO 低=故障） |
| 3 | 心跳停止后自动关断 | ✅ τ 超时 → Q 低 | ✅ 超时输出 |
| 4 | **FPGA 时钟停止不导致持续使能** | ✅ 时钟停 → 心跳停 → τ 超时关断 | ✅ 同 |
| 5 | FPGA 引脚高阻/复位/断电保持安全 | ✅ 无心跳 → 关断 | ✅ 同 |
| 6 | **不依赖 pl_clk0** | ✅ 纯 RC 定时，与 FPGA 时钟无关 | ✅ 自身振荡器 |

### 3.2 方案 A：独立看门狗 IC

代表器件（**仅举例，未采购、未定型**）：TPS3813 / MAX6369 / TPS3430 类。
- 优点：超时精度高(±%)、有窗口看门狗选项、封装小
- 缺点：
  1. 输出多为 **/WDO 或 /RESET（故障时低）**，与"故障时应为高"相反 → **需一级反相**（74LVC1G04）
  2. 上电默认状态依器件不同，**必须逐器件核对数据手册**
  3. 部分型号 WDI 需在窗口内翻转（窗口看门狗），心跳频率需匹配
- 结论：**可用，但不是最省心的失效安全方案**

### 3.3 方案 B（推荐）：可重触发单稳态 + N-MOSFET

代表器件（**仅举例，未定型**）：74LVC1G123 / 74HC123（可重触发单稳态）。
```
              +3.3V
                │
             [10k]  ← OE# 外部上拉（默认 = 关断）
                │
   OE# ─────────┼──────── PCA9685 OE# （低有效）
                │
            ┌───┴───┐
            │ N-MOS │  (如 2N7002 / BSS138)
            └───┬───┘
                │ source → GND
              gate ← 74LVC1G123  Q
                       │
   FPGA B11 ──────────►│A (下降沿重触发)
                       │
              Rext/Cext → τ = 50..200 ms
```
- 上电：Q = LOW → MOSFET 关断 → OE# 被上拉为高 → **PCA9685 输出关闭** ✅
- 心跳(~1 kHz)：每个边沿重触发 → Q 保持 HIGH → MOSFET 导通 → OE# 拉低 → **使能** ✅
- 心跳消失(>τ)：Q 回落 LOW → 关断 ✅
- **完全不依赖 `pl_clk0`** ✅
- 缺点：τ 精度依 RC 容差（±20~30%），需留足余量

**推荐 τ ≈ 100 ms，心跳 1 kHz（周期 1 ms），余量 100 倍。**

### 3.4 方案 C：分立 RC + 施密特触发器（74LVC1G17）

最简、最便宜，但 RC 充放电曲线受温度/容差影响大，τ 精度差，仅适合粗保护。

### 3.5 外部电路电源/电平/上拉/故障默认状态检查

| 项 | 要求 | 状态 |
|---|---|---|
| 看门狗电源 | **独立 3.3 V**，不得由 FPGA 提供（否则 FPGA 断电时看门狗同时失效） | 必须落实 |
| 逻辑电平 | 心跳为 LVCMOS33 (3.3 V)；看门狗输入阈值需在 3.3 V 下 >0.7·VCC | 需按器件核对 |
| OE# 上拉 | 10 kΩ → 3.3 V（**必需**，保证默认关断） | 未实施 |
| 心跳输入偏置 | 建议 B11 → 10 kΩ 下拉，防 FPGA 高阻时输入浮空误触发 | 建议 |
| 故障默认 | 看门狗自身断电/失效时，OE# 必须回到高（关断） | 由上拉保证 ✅ |
| 单点故障 | 若 MOSFET 击穿短路 → OE# 被持续拉低 = **持续使能**（危险） | 需评估，可加串联电阻或双器件冗余 |

**⚠️ 单点故障提醒**：MOSFET 短路失效会导致 OE# 永久拉低。这是本方案唯一未消除的危险失效模式，
需要在后续硬件评审中单独评估（例如串联 1 kΩ + 定期自检，或改用双 MOSFET 串联）。

---

## 4. 候选 XDC v2

新增：`constraints/kv260_haptic_j2_candidate_v2.xdc`（3,499 B）
**v1 保留未覆盖**：`constraints/kv260_haptic_j2_candidate.xdc`（4,944 B）

```tcl
set_property -dict {PACKAGE_PIN H12 IOSTANDARD LVCMOS33 PULLTYPE PULLUP} [get_ports i2c_scl_pin]
set_property -dict {PACKAGE_PIN B10 IOSTANDARD LVCMOS33 PULLTYPE PULLUP} [get_ports i2c_sda_pin]
set_property -dict {PACKAGE_PIN B11 IOSTANDARD LVCMOS33} [get_ports wdog_heartbeat]
```

### 4.1 ⚠️ 一个必须先解决的阻塞：RTL 端口不存在

当前 `rtl/kv260_haptic_top.v` 的端口是 **`oe_n`（output）**，
**没有 `wdog_heartbeat` 端口**。

因此 **XDC v2 无法直接套用到现有 RTL** —— 会产生 `get_ports` 找不到对象的错误。
要落地 v2，必须先做 **RTL 改动**（新增心跳输出、内部产生 ~1 kHz 方波），
而**本阶段禁止修改功能 RTL**。

**结论：v2 是"目标方案约束"，不是可立即使用的约束。**

---

## 5. 七个问题的回答

### Q1. i2s_in/out 是否属于可选 Pmod 功能？
**是（INFERRED 强，非官方文字确认）。** BoardStore 中两者均为**零引脚组件**、
带 `preferred_ip`（待实例化 PL IP）与 `preset_proc`（BD 可选预设），且只连到 SOM240 连接器。
**但 UG1089 的 Pmod/Supported Peripherals 正文本轮未取得**，故不能称"官方文档确认"。

### Q2. B11 是否可以在不使用 I2S Pmod 时复用？
**架构上可以（INFERRED）。** 前提三项：
① 不实例化 `i2s_in_preset`；② XTP682 确认 J2/B11 无板载固定负载；③ 接受放弃音频用途。
**在 XTP682 到手前不得视为可用。**

### Q3. 三个信号的候选映射证据等级？
| 信号 | 等级 |
|---|---|
| `i2c_scl_pin` → H12 | **CROSS-CHECKED**（Vivado DB + AMD SOM240 文件；FREE） |
| `i2c_sda_pin` → B10 | **CROSS-CHECKED**（同上；FREE） |
| `wdog_heartbeat` → B11 | **INFERRED**（被 `i2s_in` 占用，需确认可放弃） |
| **所有三根的 J2 针号** | **UNKNOWN** |

### Q4. 是否仍然需要 XTP682？
**仍然需要，且是唯一关键缺口。** BoardStore 只到 SOM240 层，
无法给出 J2 针号，也无法证明 J2 上是否有固定负载。**没有替代资料。**

### Q5. 外部硬件看门狗是否能解决 OE 时钟停振问题？
**能。** 只要有两点：
① 看门狗**不依赖 `pl_clk0`**（用自身 RC 或自身振荡器）——方案 B/C 天然满足；
② 上电/故障默认输出为"关断"——由 OE# 外部上拉 + MOSFET 关断保证。
**推荐方案 B（可重触发单稳态 + N-MOSFET，τ≈100 ms，心跳 1 kHz）。**
**遗留单点故障风险**：MOSFET 短路 → OE# 持续拉低 = 持续使能，需专项评估。

### Q6. 还有哪些电气条件必须实测？
1. **Bank 45 实际 VCCO**（若为 1.8 V，整个 LVCMOS33 方案作废）
2. **J2 VCC 针电压**（不等于 VCCO）
3. **J2 各 IO 是否有板载负载**（串阻/ESD/上拉）
4. SCL/SDA 外部 4.7 kΩ 上拉是否已存在
5. OE# 10 kΩ 上拉是否已存在
6. 看门狗独立 3.3 V 电源的电压/电流余量
7. 心跳信号在 B11 上的实际波形与幅值
8. Pmod 供电电流能力（**注：此前"100 mA"数字本轮未找到任何出处，不采信**）

### Q7. 是否可以进入下一阶段 Vivado 完整工程集成？
**可以进入，但有两条限制：**
- ✅ 可用 XDC **v1**（H12/B10/B11→oe_n）做 BD 集成与综合验证（**仅验证用途**）
- ❌ **不能**用 XDC v2 直接套用（`wdog_heartbeat` 端口不存在，需先改 RTL）
- ❌ 任何情况下**不得**生成部署 bitstream

---

## 6. 权限边界执行情况

| 禁止项 | 执行 |
|---|---|
| 修改功能 RTL | ✅ 未修改（`pca_init_sequencer.v` sha `a245e271…`，RTL 全部未动） |
| 修改 AX7020 原工程 | ✅ 未修改（HEAD `287e2ee…`，status 39 行） |
| 生成 bitstream | ✅ 0 个 |
| JTAG / loadapp | ✅ 未执行 |
| 接 PCA9685 / 执行器 | ✅ 未连接 |
| 修改供电 | ✅ 未修改 |
| 实际硬件测试 | ✅ 未进行 |
| 清理 Git | ✅ 未清理 |
| 覆盖 v1 XDC | ✅ v1 保留 |
| 加入部署工程 | ✅ 未加入任何工程 |
