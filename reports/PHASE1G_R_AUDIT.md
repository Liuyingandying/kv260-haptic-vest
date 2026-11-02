# Phase 1G-R 审计修订报告

**日期**：2026-11-01  **性质**：审计修订 + 官方映射闭环
**结论**：**映射链条完成一半**（package pin ↔ SOM240 已官方确认；SOM240 ↔ J2 仍 UNKNOWN）

---

## 1. 纠正上一轮的照片审计错误（必须先行声明）

**上一轮的错误表述**：Phase 1G 报告 §3 写的是"工作区内不存在 KV260 实物照片 → 未进行核验"。

**事实区分**：
| 陈述 | 真伪 |
|---|---|
| 本地**工作区目录**内没有找到照片文件 | **真**（递归搜索 `E:\03_Academics\Competitions\嵌入式比赛` 与用户目录，近期图片全为 Gemini 项目产物） |
| 用户在**对话中**提供了 KV260 实物照片 | **真**（本轮已收到 J2 连接器近距照片，sha256 `aae1be34…32d0`，768×1024） |
| "因此无法核验 J2" | **假** —— 这是把"工作区无照片"错误外推为"没有照片可用" |

**修订后的照片核验结论**（基于本轮可见的 J2 近距照片）：

| 项 | 结论 | 依据 |
|---|---|---|
| **J2 身份** | **CONFIRMED** | 照片中丝印明确标注 `J2`，位于 2×6 排针正上方 |
| **Pin 1/2 所在端** | **CONFIRMED** | 丝印 `1`/`2` 位于排针**右端**（靠近 `R73` 一侧）；照片同时可见 `11`/`12` 在左端 |
| **Pin 11/12 所在端** | **CONFIRMED** | 丝印 `11`/`12` 位于排针**左端**（靠近 `AMD` 标志、`MH4` 安装孔一侧） |
| **Package Pin 映射** | **待官方资料闭环**（见 §3，已完成一半） | SOM240 侧已官方确认，J2 针号侧未确认 |

**同框参照物**（用于消除方向歧义）：`J8` 34 针连接器（标注 17/18/34）、`JAS 1` 跳线丝印、
`J15` GND 测试点、`MH4` 安装孔、6 个绿色状态 LED（PWR IN / SOM LPD / SOM FPD / CC PS / SOM PL / CC PL）、`R73` 位号、PCB 条码 `2DC026012215`。

**注意**：用户提到"多张照片"，本轮上下文中实际可见的是 **1 张 J2 近距照片**。
若此前还有其他照片，本轮未复现，故本报告只对该 1 张负责。

---

## 2. 官方资料获取结果（逐项如实记录）

| 目标资料 | 尝试方式 | 结果 | 是否伪造 |
|---|---|---|---|
| **XTP682**（KV260 Carrier Card Schematic） | web 检索 | **未取得**。仅见第三方转载站（whycan.com），**按"不使用来历不明替代文件"的约束拒绝采用** | 否 |
| **XTP685**（K26 SOM XDC） | web 检索 | **未取得**（需 AMD 账号登录后从产品页下载） | 否 |
| **UG1089**（KV260 Starter Kit 用户指南） | web 检索 + 直接抓取 | **身份确认**：`ug1089-kv260-starter-kit` 确为 KV260 用户指南（原以为是器件引脚手册，用户说法正确）。但 `docs.amd.com` 为 JS 渲染，`api/khub/.../content` 只返回导航页，**未取得 Pmod 引脚表正文** | 否 |
| **AMD 官方板卡文件（Vivado 自带）** | 本地 `XilinxBoardStore` | ✅ **已取得并实测**，见 §3 | — |

**必须明确：XTP682/XTP685 未取得，是 "SOM240 ↔ J2" 这一环无法闭合的唯一原因。**
我没有用任何第三方转载文件冒充官方资料。

---

## 3. 逐级追踪引脚（本轮实际完成度）

### 3.1 已官方确认的一环：Package Pin ↔ SOM240 连接器

来源：`E:\vivado\2025.2\Vivado\data\xh​ub\boards\XilinxBoardStore\boards\Xilinx\k26c\1.4\part0_pins.xml`
文件头：`Copyright (C) 2023, Advanced Micro Devices, Inc` · `part_name="XCK26-SFVC784-2LV-C"` · Apache-2.0
（Vivado 2025.2 随安装分发的 AMD 官方板卡文件，**非第三方**）

正则解析出 **205 条 pin 记录**，其中 8/8 命中候选：

| Package Pin | 官方 SOM240 记录 | SOM240 index | Bank（Vivado 器件库） |
|---|---|---|---:|---:|
| **H12** | `som240_1_a17` | **11** | 45 |
| **B10** | `som240_1_b20` | **23** | 45 |
| **E12** | `som240_1_b21` | **24** | 45 |
| **D11** | `som240_1_b22` | **25** | 45 |
| **B11** | `som240_1_c22` | **37** | 45 |
| **E10** | `som240_1_d20` | **51** | 45 |
| **D10** | `som240_1_d21` | **52** | 45 |
| **C11** | `som240_1_d22` | **53** | 45 |

**重要观察**：8 个 SOM240 索引是 `11, 23, 24, 25, 37, 51, 52, 53` —— **不连续**。
说明第三方表给出的"8 个 package pin 顺序"是一个**逻辑顺序**，不是 SOM240 针序。

### 3.2 待确认的一环：SOM240 ↔ J2 针号

| 链路环节 | 状态 | 依据 |
|---|---|---|
| J2 是目标连接器 | **CONFIRMED** | 实物照片丝印 |
| J2 Pin1/2、Pin11/12 所在端 | **CONFIRMED** | 实物照片丝印 |
| Package Pin ↔ SOM240 net | **CONFIRMED** | AMD 官方 k26c 板卡文件 |
| **SOM240 net ↔ 载板 J2 针号（HDA 网络名）** | **UNKNOWN** | **需 XTP682 载板原理图 —— 未取得** |
| Bank 归属 | **CONFIRMED** | Vivado 器件库，全部 Bank 45 |
| IOSTANDARD LVCMOS33 合法性 | **CONFIRMED** | `place_design` 通过 |
| **载板 Bank45 实际 VCCO** | **UNKNOWN** | 需 XTP682 或实测 |

### 3.3 用户提出的 8 条链，逐条核验结果

| 用户给的链路 | Package Pin 存在 | SOM240 net（官方） | J2 针号 | 链路完整性 |
|---|---|---|---|---|
| H12 → HDA11 → J2-1 | ✅ | `som240_1_a17` (idx 11) | ? | **部分确认** |
| B10 → HDA15 → J2-2 | ✅ | `som240_1_b20` (idx 23) | ? | **部分确认** |
| E10 → HDA12 → J2-3 | ✅ | `som240_1_d20` (idx 51) | ? | **部分确认** |
| E12 → HDA16_CC → J2-4 | ✅ | `som240_1_b21` (idx 24) | ? | **部分确认** |
| D10 → HDA13 → J2-5 | ✅ | `som240_1_d21` (idx 52) | ? | **部分确认** |
| D11 → HDA17 → J2-6 | ✅ | `som240_1_b22` (idx 25) | ? | **部分确认** |
| C11 → HDA14 → J2-7 | ✅ | `som240_1_d22` (idx 53) | ? | **部分确认** |
| B11 → HDA18 → J2-8 | ✅ | `som240_1_c22` (idx 37) | ? | **部分确认** |

### 3.4 结构性一致性检查（这是本轮最强的旁证）

用户给的 HDA 编号排列：`HDA11, HDA15, HDA12, HDA16_CC, HDA13, HDA17, HDA14, HDA18` → 对应 J2 `1,2,3,4,5,6,7,8`。

注意规律：**HDA11–HDA14 落在奇数针（1,3,5,7），HDA15–HDA18 落在偶数针（2,4,6,8）**。
这正好符合 2×6 排针的**双排结构**（一排 4 个 + 一排 4 个）。

再看第三方表的顺序：`H12, E10, D10, C11, B10, E12, D11, B11`
= `HDA11, HDA12, HDA13, HDA14, HDA15, HDA16_CC, HDA17, HDA18`
= **先排 A 排（奇数针），再排 B 排（偶数针）** —— 与用户映射完全自洽。

**三源结构一致性（用户映射 / 第三方表顺序 / 官方 SOM240 记录）成立 → 判定 CROSS-CHECKED。**
但结构自洽**不能**替代原理图：它不能证明 HDA11 就是 H12（而非 E10 之外的另一根）。

---

## 4. 电源审计（本轮新发现，重要）

### 4.1 官方板卡文件中发现的 `hda_iic_switch`（潜在冲突）

`kv260_carrier/1.3/board.xml`（AMD 官方）第 281 行定义：

```xml
<interface name="hda_iic_switch" mode="master" type="xilinx.com:interface:iic_rtl:1.0" of_component="hda_iic_switch">
  <port_map logical_port="SCL_I" physical_port="iic_scl_i" dir="inout" />
  ...
<component name="hda_iic_switch" display_name="HDA IIC SWITCH" ...>
  <description>AXI IIC connected to HDA IIC SWITCH on AI Vision Starter Kit carrier card</description>
  <connection name="som240_connector_1_hda_iic_switch_connection" ...>
    <connection_map ... c1_st_index="196" c1_end_index="197" c2_st_index="0" c2_end_index="1"/>
```

**含义**：KV260 载板上存在一个名为 **HDA IIC SWITCH** 的器件（很可能是 TCA9548A 类 I2C 多路复用器），
其上游接 SOM240 索引 **196/197** 的 I²C 信号。

**这对我们的方案是一个必须查清的风险**：
- 若 J2 的 HDA11/HDA15 是**直连 PL 的裸引脚** → 我们的开漏 I²C 方案成立。
- 若它们**经过载板上的 HDA IIC SWITCH** → 我们在 PL 上再挂一个开漏 I²C 主控会与该 mux **冲突**，
  整个 SCL/SDA 方案需要重新设计（或改为使用载板 mux 的上游）。

**本轮无法判定**，因为需要 XTP682 载板原理图确认 HDA11/HDA15 的走线。
**在查清前，SCL/SDA 的引脚分配保持 CANDIDATE，且不得接线。**

### 4.2 电源项核查表

| 项 | 状态 | 说明 |
|---|---|---|
| Bank45 VCCO 供电网络 | **UNKNOWN** | 需载板原理图 |
| PL_3V3 是否实际供给 Bank45 | **UNKNOWN** | 需原理图；**不能因 `place_design` 接受 LVCMOS33 就认定板卡真的给了 3.3 V** |
| J2 3.3V 电源针与 Bank45 VCCO 是否同一网络 | **UNKNOWN** | 明确**不是**可以假设等同的 |
| Pmod 供电能力（电流上限） | **UNKNOWN** | 需原理图或实测 |
| SCL/SDA 外部上拉要求 | **REQUIRED**（4.7 kΩ→3.3 V） | 开漏总线必需；KV260 侧 `candidate_pmod.xdc` 无 PULLUP 兜底 |
| OE# 外部上拉 | **REQUIRED**（4.7–10 kΩ→3.3 V） | 未配置时唯一关断保证 |
| 板上是否已有上拉/串阻 | **UNKNOWN** | 与外部上拉并联会过强 |

---

## 5. 引脚分配（维持候选，不升级）

| RTL 信号 | 候选 Package Pin | SOM240 net（官方） | Bank | IOSTANDARD | 状态 |
|---|---|---|---|---|---|
| `i2c_scl_pin` | **H12** | `som240_1_a17` | 45 | LVCMOS33 | **CANDIDATE** |
| `i2c_sda_pin` | **B10** | `som240_1_b20` | 45 | LVCMOS33 | **CANDIDATE** |
| `oe_n` | **B11** | `som240_1_c22` | 45 | LVCMOS33 | **CANDIDATE** |

**升级为 CONFIRMED 的三个必要条件，目前均未满足：**
1. ❌ XTP682 确认 SOM240 net ↔ J2 针号（含 HDA 编号）
2. ❌ 确认 HDA11/HDA15 是否经载板 `hda_iic_switch`
3. ❌ 实测 Bank45 VCCO 与 J2 VCC 电压

---

## 6. Phase 1G-R 独立自检（重新执行，10 项）

| # | 检查项 | 结果 | 说明 |
|---|---|---|---|
| 1 | Package Pin 是否真实存在 | ✅ **PASS** | Vivado 器件库 721 pin；8/8 存在 |
| 2 | 是否有重复 LOC | ✅ **PASS** | H12/B10/B11 互不相同 |
| 3 | Bank 与 IOSTANDARD 兼容 | ✅ **PASS** | 全部 Bank45；`place_design` 接受 LVCMOS33 |
| 4 | J2 物理针脚 ↔ Package Pin 映射一致 | ⚠️ **部分** | 照片确认了 J2 两端；SOM240 官方确认；**中间一段（SOM240↔J2）缺失** |
| 5 | RTL 端口名完全一致 | ✅ **PASS** | `kv260_haptic_top.v` L57/L58/L61 |
| 6 | 开漏 I²C 电气实现 | ⚠️ **PARTIAL** | RTL 只拉低/释放；**IOBUF 推断未验证**；**HDA mux 风险未排除** |
| 7 | OE_N 安全极性 | ✅ **PASS** | `oe_n=1`=关断；复位默认 1；外部上拉必需 |
| 8 | XDC 是否误约束其他接口 | ✅ **PASS** | 仅 3 个端口 |
| 9 | AX7020 原工程完全未变 | ✅ **PASS** | HEAD `287e2ee…`；`status` 39 行 |
| 10 | 本轮是否产生非授权硬件操作 | ✅ **PASS** | bitstream 0；未 loadapp/JTAG/I²C/接执行器/改供电 |

---

## 7. 剩余风险

| # | 风险 | 等级 | 阻塞点 |
|---|---|---|---|
| 1 | SOM240 ↔ J2 映射未知 | **高** | 需 XTP682 |
| 2 | **HDA IIC SWITCH 可能与 PL 侧 I²C 冲突** | **高（新增）** | 需 XTP682 确认走线 |
| 3 | Bank45 实际 VCCO 未知 | 高 | 需原理图/实测 |
| 4 | J2 VCC 电压未知 | 高 | 需实测 |
| 5 | 板上拉/串阻未知 | 中 | 需原理图/目视 |
| 6 | IOBUF 推断未验证 | 中 | 需真实 XDC 综合 |
| 7 | OE 时钟停振缺口 | 高 | 需硬件看门狗 |
| 8 | BD 集成与 PS 侧 AXI 驱动未做 | 高 | 需开发 |
| 9 | 驱动级参数未知 | 高 | 需硬件资料 |

---

## 8. 结论

**本轮把引脚链条从 0 环推进到 1.5 环：**
- ✅ 器件层：package pin 存在、Bank 45、LVCMOS33 合法（此前已确认）
- ✅ **新增**：package pin ↔ SOM240 net（AMD 官方板卡文件，8/8 确认）
- ✅ **修订**：照片核验（J2 身份、Pin1/2 端、Pin11/12 端）
- ⚠️ **新增风险**：载板 `hda_iic_switch` 可能使 PL 侧 I²C 方案失效
- ❌ 仍缺：SOM240 net ↔ J2 针号（XTP682）

**不把结构性自洽当作原理图证据。不把候选 XDC 当作已验证约束。**


---

# 附录：Phase 1G-S 补充结论（BoardStore 深度追踪）

1. **HDA IIC Switch 网络冲突：CLOSED**。其上游为 `som240_1_d16/d17` = **G11/F10**，
   与 8 个候选 pin 无交集（证据见 `PHASE1G_S_BOARDSTORE_TRACE.md` §4）。
2. **6 个候选 pin 被官方载板文件分配给 I2S**：
   `i2s_in` = E12/D11/B11，`i2s_out` = E10/D10/C11。
   仅 **H12、B10** 未被任何载板接口引用。
3. 因此 **`oe_n` 原候选 B11 需重新决策**；H12/B10 可保留。
4. BoardStore **不含** J2/Pmod 针号定义、**不含** HDA11–HDA18 网络名 →
   "SOM240 ↔ J2" 这一环**依然只能由 XTP682 闭合**。
5. BoardStore 版本号（1.2/1.3）与载板 Rev 1.0/Rev 2.0 无对应关系，修订差异 **UNKNOWN**。
