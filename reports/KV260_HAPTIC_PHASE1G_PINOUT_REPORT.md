# KV260 触觉控制 Phase 1G 引脚映射审计报告

**日期**：2026-11-01  **板卡**：Kria KV260 Vision AI Starter Kit，载板 **revB**
**器件**：`xck26-sfvc784-2LV-c`  **工具**：Vivado 2025.2

> 本轮**未进行任何硬件操作**：未生成 bitstream、未烧录、未 `loadapp`、未 JTAG、未真实 I²C、未接执行器、未改供电。

---

## 1. 执行摘要

| 结论 | 状态 |
|---|---|
| 8 个候选 package pin 在器件中**真实存在**，且**全部属于 IOBANK 45**、均为 IO 引脚 | **CONFIRMED**（Vivado 器件数据库实测） |
| IOSTANDARD **LVCMOS33 在 Bank 45 合法** | **CONFIRMED**（`place_design` 绑定该约束后无错误完成） |
| 8 个 package pin 集合与第三方表 + 项目 `candidate_pmod.xdc` 三源一致 | **CROSS-CHECKED** |
| **J2 物理针号 ↔ package pin 映射** | **UNKNOWN** |
| **J2 Pin 1 物理方向** | **UNKNOWN** |
| **载板上 Bank 45 实际 VCCO** | **UNKNOWN** |
| **实物照片核验** | **未进行**（工作区内无照片，见 §3） |
| 是否可直接出部署 bitstream | **否**（候选约束，禁止部署） |

---

## 2. 官方资料清单

| # | 资料 | 版本/标识 | 来源 | 本轮取得 | 支持的结论 |
|---|---|---|---|---|---|
| 1 | **Vivado 器件数据库** `xck26-sfvc784-2LV-c` | Vivado 2025.2 build 6299465 | 本地工具自带的 AMD 官方器件模型 | ✅ **已实测** | 引脚是否存在、所属 Bank、引脚功能名 |
| 2 | Kria SOM Carrier Card Design Guide **UG1091** | — | [PDF](https://www.mikrocontroller.net/attachment/573192/ug1091-carrier-card-design.pdf) | ⚠️ 检索到公开副本，**未逐页定位 J2 表** | 载板设计规范（未引用具体页码） |
| 3 | Kria KV260 Vision AI Starter Kit 用户手册 | amd-sk-kv260-g-um1 | [PDF](https://www.element14.cn/site/binaries/content/assets/common/product-family-documents/amd-sk-kv260-g/amd-sk-kv260-g-um1.pdf) | ⚠️ 检索到，**未逐页定位 J2 表** | 板级连接器（未引用具体页码） |
| 4 | KV260 数据手册 | — | [PDF](https://www.c2s.gov.in/Technical_Data_sheet/Kria_KV_data_sheet.pdf) | ⚠️ 检索到 | 板卡规格 |
| 5 | **XTP682** | — | — | ❌ **未取得** | 无法引用 |
| 6 | **KV260 Carrier Card 原理图** | — | — | ❌ **未取得** | **无法确认 J2 针号映射与 VCCO** |
| 7 | 项目 `candidate_pmod.xdc` | 512 B | `KV260_Basic_Preboard/kv260_i2c_bringup_20261019T090440Z/` | ✅ 已读 | 项目候选 H12/B10；文件头自述 "Candidate only ... must be confirmed" |
| 8 | 项目 `control/INTERFACE.md` L88 附近 | — | 同工程 | ✅ 已读 | "KV260 Pmod uses HDIO bank45, official reference LVCMOS33；J2 物理方向/上拉/电压 remain CONDITIONAL" |

**必须明确：第 6 项（载板原理图）缺失，是 J2 针号映射无法闭合的**根本原因**。**
任何以"约等于"或博客推断补上的 LOC 都是不允许的。

---

## 3. J2 实物照片核验（**Phase 1G-R 已修订**）

**修订说明**：本节原写"工作区内无照片 → 未核验"，这是把**工作区无照片**错误外推成
**没有照片可用**。用户已在对话中提供 J2 近距实物照片（sha256 `aae1be34…`，768×1024），
据此修订如下：

| 项 | 修订后结论 |
|---|---|
| J2 身份 | **CONFIRMED**（照片丝印 `J2`） |
| Pin 1/2 所在端 | **CONFIRMED**（右端，靠 `R73`） |
| Pin 11/12 所在端 | **CONFIRMED**（左端，靠 `AMD` 标志 / `MH4`） |
| Package Pin 映射 | 待官方资料闭环（SOM240 侧已确认，J2 侧未确认） |

同框参照物：`J8`(34pin)、`JAS 1`、`J15` GND、`MH4`、6 个状态 LED、`R73`、条码 `2DC026012215`。
**本轮上下文中实际可见的照片为 1 张**；若此前另有照片，本轮未复现。

### 3.1 原始表述（保留作为纠错痕迹）

检索范围与结果：
- 工作区 `E:\03_Academics\Competitions\嵌入式比赛` 递归搜索 `*.jpg/*.jpeg/*.png/*.heic/*.bmp`，近 3 天新增文件**全部为 Gemini 项目输出图**（深度图、矩阵图、GUI 截图），无任何 KV260 板卡照片；
- `%USERPROFILE%\Downloads`、`Desktop`、`Pictures` 近 7 天仅有 1 张与本事无关的系统截图（`屏幕截图 2026-10-26 153412.png`，3971 B）。

**因此**：
- ❌ 无法识别 J2 连接器实物
- ❌ 无法识别 Pin 1 丝印标记
- ❌ 无法确认照片方向与官方编号一致
- ❌ 无法与官方原理图交叉验证

**本报告不假装已经检查过照片。** 若后续提供照片，需重做本节。

---

## 4. J2 Pin 1 方向判定

| 判定途径 | 状态 | 说明 |
|---|---|---|
| 官方原理图 | **UNKNOWN** | 未取得 |
| XTP682 | **UNKNOWN** | 未取得 |
| 实物照片 | **NOT CHECKED** | 无照片 |
| 丝印目视 | **NOT CHECKED** | 无现场 |
| 万用表通断 | **NOT DONE** | 无现场 |

**结论：J2 Pin 1 方向 = UNKNOWN。禁止据此接线。**
插反可能将 VCC 与 GND/IO 短接，永久损坏 KV260。

---

## 5. Package Pin 映射表（器件层已确认 / 连接器层未知）

### 5.1 器件层：Vivado 数据库实测（**CONFIRMED**）

查询方法：`create_project -in_memory -part xck26-sfvc784-2LV-c` → 载入最小设计触发器件模型 →
`get_package_pins`。实测器件共 **721 个 package pin、15 个 IOBANK**（含 43/44/45/46/64/65/66 等）。

| 候选 pin | 存在于器件 | 所属 Bank | 引脚功能名（Vivado `PIN_FUNC`） | 状态 |
|---|---|---|---|---|
| **H12** | ✅ 是 | **45** | `IO_L4N_AD12N_45` | **CONFIRMED** |
| **E10** | ✅ 是 | **45** | `IO_L7P_HDGC_45` | **CONFIRMED** |
| **D10** | ✅ 是 | **45** | `IO_L7N_HDGC_45` | **CONFIRMED** |
| **C11** | ✅ 是 | **45** | `IO_L9P_AD11P_45` | **CONFIRMED** |
| **B10** | ✅ 是 | **45** | `IO_L9N_AD11N_45` | **CONFIRMED** |
| **E12** | ✅ 是 | **45** | `IO_L8P_HDGC_45` | **CONFIRMED** |
| **D11** | ✅ 是 | **45** | `IO_L8N_HDGC_45` | **CONFIRMED** |
| **B11** | ✅ 是 | **45** | `IO_L10P_AD10P_45` | **CONFIRMED** |

Bank 45 在器件中共有 **24 个 IO 引脚**；上面 8 个是其中的一部分。
引脚功能名含 `AD*`（模拟兼容 HDIO）与 `HDGC`（HD 全局时钟），符合 ZynqMP HDIO 命名。

### 5.2 连接器层：J2 针号 ↔ package pin（**UNKNOWN**）

| 序（第三方表顺序） | Package Pin | 对应 J2 物理针号 | 状态 |
|---|---|---|---|
| 1 | H12 | ? | **UNKNOWN** |
| 2 | E10 | ? | **UNKNOWN** |
| 3 | D10 | ? | **UNKNOWN** |
| 4 | C11 | ? | **UNKNOWN** |
| 5 | B10 | ? | **UNKNOWN** |
| 6 | E12 | ? | **UNKNOWN** |
| 7 | D11 | ? | **UNKNOWN** |
| 8 | B11 | ? | **UNKNOWN** |

**8 个 package pin 的"集合"已三源互证（CONFIRMED/CROSS-CHECKED），
但"哪一个落在 J2 第几针"完全没有证据 → UNKNOWN。**
这是本轮最重要的未闭合项。

---

## 6. Bank 与 VCCO 电气核验

| 项 | 值 | 状态 | 依据 |
|---|---|---|---|
| Bank 编号 | **45** | **CONFIRMED** | Vivado `get_package_pins ... -filter "BANK == 45"` |
| Bank 类型 | HDIO（引脚名 `AD*`/`HDGC`，ZynqMP HD 系列） | **INFERRED** | 引脚命名规律，非官方文档直述 |
| IOSTANDARD 支持 LVCMOS33 | **是** | **CONFIRMED** | 用 H12/B10/E10 绑定 `IOSTANDARD LVCMOS33` 后 `place_design` **无错误完成**（`phase1g/place_lvcmos33_probe.log`） |
| **载板上 Bank 45 实际 VCCO** | ? | **UNKNOWN** | 需载板原理图或实测 |
| **J2 VCC 针电压** | ? | **UNKNOWN** | 需实测 |
| **J2 VCC 是否与 Bank45 VCCO 同一电源** | ? | **UNKNOWN** | **不能把 J2 的 3.3V 等同于 Bank45 VCCO** |

> ⚠️ 工具"接受 LVCMOS33"只说明**器件允许**，不说明**载板真的给了 3.3 V**。
> 若 VCCO 实际为 1.8 V 而 IOSTANDARD 写 LVCMOS33，实现阶段会报 VCCO 冲突或损坏 IO。

---

## 7. SCL / SDA / OE_N 引脚分配（候选）

### 7.1 RTL 端口（逐行核对，`rtl/kv260_haptic_top.v`）

| 行 | 声明 |
|---|---|
| L57 | `inout  wire        i2c_scl_pin,` |
| L58 | `inout  wire        i2c_sda_pin,` |
| L61 | `output wire        oe_n,` |

**端口名与规格表完全一致，无大小写/拼写差异。**

### 7.2 候选分配

| RTL 信号 | 类型 | 候选 Package Pin | Bank | IOSTANDARD | 选择理由 |
|---|---|---|---|---|---|
| `i2c_scl_pin` | inout 开漏 | **H12** | 45 | LVCMOS33 | 沿用项目 `candidate_pmod.xdc` 既有候选，保持连续性 |
| `i2c_sda_pin` | inout 开漏 | **B10** | 45 | LVCMOS33 | 同上 |
| `oe_n` | output | **B11** | 45 | LVCMOS33 | 从同一已核验集合中选**非 HDGC** 引脚，把 E10/D10/E12/D11 四个时钟能力引脚留给未来时钟需求 |

### 7.3 逐项检查

| 检查项 | 结果 |
|---|---|
| 引脚冲突 | ✅ 无。H12 / B10 / B11 三者互不相同，且不与其他已用端口冲突（工程内无其他 XDC） |
| Bank 与 IOSTANDARD 兼容 | ✅ **CONFIRMED**（`place_design` 通过） |
| SCL/SDA 的 IOBUF 推断 | ⚠️ **NOT VERIFIED**。RTL 仅声明 `inout`，真实 IOBUF 需在实现后确认；此前 OOC 综合把 tricell 转成 logic（2 条 `Synth 8-5799`），**必须用本 XDC 重新综合后确认出现 3 个 IOB** |
| OE_N 默认关断状态 | ✅ RTL 侧 `oe_n` 复位值 = `1'b1`（关断）；`haptic_oe_guard.v` L140-150 |
| 外部上拉要求 | ✅ **必需**：SCL/SDA 各 4.7 kΩ→3.3 V；OE# 4.7–10 kΩ→3.3 V |

### 7.4 OE_N 时钟停振安全缺口 —— 硬件解决建议

**问题**：`oe_n` 是寄存器输出；若 `pl_clk0` 停振且此前处于使能态（`oe_n=0`），
引脚会**一直保持低电平 = 输出持续使能**，RTL 无法自救。

**硬件建议（按推荐度排序）**：
1. **RC 硬件看门狗**：`oe_n` 经 RC（如 100 kΩ + 1 µF ≈ 100 ms）接 OE#，同时 FPGA 侧用
   ~1 kHz 方波"心跳"驱动该节点。时钟停振 → 心跳消失 → 电容放电 → OE# 被上拉拉高 → 关断。
   简单、无源、失效安全。
2. **独立看门狗芯片**（如 TPS3813 / MAX6369）：FPGA 输出心跳喂狗，超时输出拉高 OE#。
   最可靠，可覆盖 FPGA 死锁而非仅时钟停振。
3. **模拟开关 + 上拉**：用 FPGA 心跳控制一个 MOSFET 把 OE# 拉到地；心跳消失 → 释放 → 上拉关断。
4. **禁止**仅依赖软件 FULL_OFF（走 I²C，总线挂死时无效）。

**本轮不实现任何一项**（属硬件改动，需现场确认后另行授权）。

---

## 8. 候选 XDC 内容

文件：`constraints/kv260_haptic_j2_candidate.xdc`（3,432 B）

```tcl
set_property -dict {PACKAGE_PIN H12 IOSTANDARD LVCMOS33 PULLTYPE PULLUP} [get_ports i2c_scl_pin]
set_property -dict {PACKAGE_PIN B10 IOSTANDARD LVCMOS33 PULLTYPE PULLUP} [get_ports i2c_sda_pin]
set_property -dict {PACKAGE_PIN B11 IOSTANDARD LVCMOS33} [get_ports oe_n]
```

**这份 XDC 明确禁止用于部署 bitstream**，文件头含 6 条 TODO：
J2 Pin1 方向、J2 针号映射、实际 VCCO、J2 VCC 电压、板上拉/串阻、哪根 pin 真的接到 J2。
`oe_n` **故意不加 PULLTYPE**：内部上拉会与外部上拉打架，内部下拉会在未配置时**使能输出**。

**未自动加入任何综合工程**，未触发 bitstream。

---

## 9. I²C 上拉与电气连接要求

| 项 | 要求 | 状态 |
|---|---|---|
| SCL 上拉 | **4.7 kΩ → 3.3 V** | 必需，未实施 |
| SDA 上拉 | **4.7 kΩ → 3.3 V** | 必需，未实施 |
| OE# 上拉 | **4.7–10 kΩ → 3.3 V** | 必需（安全），未实施 |
| 3.3 V 来源 | 必须与 bank45 VCCO **同一电源域**，或至少同一电平参考 | **UNKNOWN** |
| 共地 | KV260 GND ↔ TCA9548A GND ↔ PCA9685 GND ↔ 外部电源 GND **单点共地** | **UNKNOWN** |
| VDD vs V+ | PCA9685 **VDD（3.3 V 逻辑）与 V+（执行器）必须分离**，禁止并接 | 需现场落实 |
| 上拉电压禁用 5 V | 5 V 上拉可能损坏 HDIO（绝对最大 3.3 V + 余量） | 硬性要求 |

I²C 总线速率（仿真已证）：**378.78 kHz**，t_LOW/t_HIGH 均 **1319.9 ns**，符合 Fast-mode。

---

## 10. Phase 1G-F 独立自检（10 项）

| # | 检查项 | 结果 | 证据 |
|---|---|---|---|
| 1 | Package Pin 是否真实存在 | ✅ **通过** | 器件共 721 pin；H12/B10/B11 均 `EXISTS`，`PIN_FUNC` 有实际名称 |
| 2 | 是否有重复 LOC | ✅ **通过** | 仅 3 条 LOC：H12 / B10 / B11，互不相同 |
| 3 | Bank 与 IOSTANDARD 是否兼容 | ✅ **通过** | `place_design` 绑定 LVCMOS33 后无错误完成 |
| 4 | J2 物理针脚与 package pin 映射是否一致 | ❌ **无法检查** | 映射本身 UNKNOWN，无原理图/照片 |
| 5 | RTL 端口名是否完全一致 | ✅ **通过** | L57/L58/L61：`i2c_scl_pin` / `i2c_sda_pin` / `oe_n`，与 XDC `get_ports` 完全一致 |
| 6 | 开漏 I²C 电气实现 | ⚠️ **部分** | RTL 只"拉低/释放"（`i2c_master.v` L10-11），无推挽拉高；但 **IOBUF 推断未验证** |
| 7 | OE_N 安全极性 | ✅ **通过** | `oe_n=1` = OE# 高 = 输出关闭；复位默认 1；外部上拉保证未配置时关断 |
| 8 | XDC 是否误约束其他接口 | ✅ **通过** | 仅 3 个端口，无其他 `PACKAGE_PIN`/时钟/IO 约束 |
| 9 | AX7020 原工程是否完全未变 | ✅ **通过** | HEAD `287e2ee3365a3c0d0ed151d1363a72841b22d6b7`，`git status` 39 行（与前几轮一致） |
| 10 | 本轮是否产生非授权硬件操作 | ✅ **通过** | 工程内 bitstream **0 个**；未 `loadapp`、未 JTAG、未真实 I²C、未接执行器、未改供电 |

---

## 11. 未解决问题及其风险

| # | 问题 | 风险等级 | 影响 |
|---|---|---|---|
| 1 | **J2 针号 ↔ package pin 映射 UNKNOWN** | **高** | 无法接线；接错可能短路 VCC/GND 烧毁 KV260 |
| 2 | **J2 Pin 1 方向 UNKNOWN** | **高** | 同上 |
| 3 | **Bank45 实际 VCCO UNKNOWN** | **高** | 若为 1.8 V，LVCMOS33 方案与 3.3 V 上拉全部作废 |
| 4 | **J2 VCC 电压 UNKNOWN** | 高 | 上拉电压选择依据缺失 |
| 5 | 板上拉/串阻 UNKNOWN | 中 | 可能与外部 4.7 kΩ 并联导致过强上拉 |
| 6 | **IOBUF 推断未验证** | 中 | OOC 已把 tricell 转 logic；需真实 XDC 综合后确认 3 个 IOB |
| 7 | **OE 时钟停振缺口** | **高**（安全） | PL 停振时输出可能持续使能，无硬件兜底 |
| 8 | AXI 0x3000 未映射区挂起 | 中 | PS 误访问会挂死 |
| 9 | PS 侧 BD 集成与 AXI 驱动未做 | 高 | 即使引脚确认也无法运行 |
| 10 | 单执行器驱动级参数 UNKNOWN | 高 | 无法确定 V+ 与极性 |

---

## 12. 下一阶段上板前置条件

**必须全部满足：**

1. ✅（已完成）package pin 存在性/Bank/IOSTANDARD 合法性
2. ❗ **取得 KV260 载板原理图或 XTP682 的 J2 页**，确认针号 ↔ package pin 映射
3. ❗ **实物确认 J2 Pin 1 方向**（照片或现场丝印 + 万用表）
4. ❗ **实测 Bank45 VCCO 与 J2 VCC 电压**（万用表，单位 V）
5. ❗ **确认板上拉/串阻**（目视 + 通断）
6. ❗ 外接 4.7 kΩ×2（I²C）+ 4.7–10 kΩ（OE#）上拉
7. ❗ 选定并落实 OE 时钟停振硬件兜底方案（§7.4）
8. ❗ 完成 BD 集成（PS + AXI + pl_clk0/pl_resetn0）
9. ❗ 用本 XDC 重新综合，确认出现 **3 个 IOB**（SCL/SDA/OE）
10. ❗ 驱动级额定电压/电流/极性确认，V+ 独立限流电源准备

---

## 13. 最终回答

### A. J2 引脚映射是否真正确认？
**否。** 只有"器件层"确认（8 个 package pin 存在、属 Bank 45、LVCMOS33 合法）。
"连接器层"（哪个 package pin 对应 J2 第几针、Pin1 方向）**完全未确认**。

### B. 三个信号能否完成可靠分配？
**只能完成"候选分配"，不能称"可靠"。**
合法 package pin 已选定（H12/B10/B11，均 Bank45/LVCMOS33 已验），
但"可靠"依赖 J2 映射 → 当前为 **CANDIDATE**。

### C. 是否可以进入 Vivado 完整工程约束集成？
**可以进入，但必须限制用途**：
- ✅ 可以集成该 XDC 做**综合/实现/时序/资源验证**
- ✅ 可以用它验证 IOBUF 是否被正确推断
- ❌ **不得**据此生成部署 bitstream
- ❌ **不得**据此接线

### D. 是否具备单 PCA9685 上板条件？
**不具备。** 缺：J2 针号映射、Pin1 方向、VCCO 实测、外部上拉、OE 硬件兜底、BD 集成、部署 bitstream。

### E. 距离单执行器振动验证还缺什么？
1. J2 映射与 Pin1 方向（原理图/照片/实测）
2. Bank45 VCCO 与 J2 VCC 实测
3. 外部 4.7 kΩ 上拉 ×2 + OE# 上拉
4. OE 时钟停振硬件兜底
5. BD 集成 + PS 侧 AXI 软件
6. 用真实 XDC 综合确认 3 个 IOB
7. 驱动级型号与额定参数、V+ 独立限流电源
8. 执行器极性确认
9. 现场 12 项检查表（`KV260_HAPTIC_FIELD_CHECKLIST.md`）

---

## 14. 结论声明

**引脚映射"器件层"确认 ≠ 硬件可部署。**
**候选 XDC ≠ 已验证约束。**

本报告确认的是：*所选 3 个 package pin 在器件中存在、属 Bank45、LVCMOS33 合法、
且 RTL 端口名匹配*。
本报告**没有**确认：*它们真的连到 J2 的哪一针、J2 Pin1 在哪、载板给的是几伏*。

在取得载板原理图或现场实测之前，任何接线与 bitstream 部署都是**禁止**的。
