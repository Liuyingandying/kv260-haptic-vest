# KV260 触觉控制 板级连接引脚报告

**日期**：2026-11-01  **板卡**：Kria KV260 Vision AI Starter Kit，载板 **revB**
（`/sys/firmware/devicetree/base/model = "ZynqMP KV260 revB"`，H0 审计实测）
**状态**：**不具备上板条件**（J2 针号映射与电压未确认，详见 §6）

> 本报告**不新增任何 PACKAGE_PIN**，不修改 XDC。所有引脚仅作调查记录。

## 1. 资料清单

### 1.1 官方资料（AMD/Xilinx）
| 资料 | 用途 | 获取状态 |
|---|---|---|
| Kria SOM Carrier Card Design Guide **UG1091** | 载板设计规范、连接器参考 | 已检索到公开 PDF（[ug1091](https://www.mikrocontroller.net/attachment/573192/ug1091-carrier-card-design.pdf)） |
| Kria KV260 Vision AI Starter Kit 用户手册 | 板级连接器与电源 | 公开 PDF（[amd-sk-kv260-g-um1](https://www.element14.cn/site/binaries/content/assets/common/product-family-documents/amd-sk-kv260-g/amd-sk-kv260-g-um1.pdf)） |
| KV260 数据手册 | 板卡规格 | 公开 PDF（[c2s datasheet](https://www.c2s.gov.in/Technical_Data_sheet/Kria_KV_data_sheet.pdf)） |
| AMD 支持社区：从原理图/SoM XDC 取约束 | 获取 Kria 约束方法 | [AMD 社区帖](https://adaptivesupport.amd.com/s/question/0D54U00008qiXeHSAU/-info-post-getting-constraint-of-kria-boards-from-its-schematic-and-som-xdc-file?language=zh_CN) |
| **XTP682** | 项目文档中提及的官方参考页 | ❌ **本轮未取得原文**，无法引用页码 |

### 1.2 第三方资料（**非官方，仅作旁证**）
| 资料 | 内容 | 可信度 |
|---|---|---|
| [t-ngtn.com Pmod 引脚对照表](https://t-ngtn.com/posts/5/#kv260) | KV260 Pmod(J2) 8 个 PL IO 的 package pin 列表 | 第三方实测，**需与官方互证** |

### 1.3 项目内已有资料
| 文件 | 内容 |
|---|---|
| `KV260_Basic_Preboard/control/INTERFACE.md` L88 附近 | "KV260 Pmod uses HDIO bank45, official reference LVCMOS33. **J2 physical revision/orientation, line-path/pullups/voltage ... remain CONDITIONAL**" |
| `KV260_Basic_Preboard/kv260_i2c_bringup_20261019T090440Z/candidate_pmod.xdc` | **候选** H12/B10，文件头自述 "Candidate package assignments only ... must be confirmed" |
| `.../README_I2C_BRINGUP.md` L22 | "J2 实际针号、方向、接线与电压仍须实物确认，`candidate_pmod.xdc` 不作为最终接线图" |
| `.../PERIPHERAL_PREP_RESULT.json` L66 | 要求 "clear J2 pin-1/orientation; matching official XTP682 page or verified continuity" |

## 2. J2 连接器逐针核对

### 2.1 已确认部分
| 项 | 值 | 依据 | 状态 |
|---|---|---|---|
| KV260 上 Pmod 连接器数量 | **1 个** | 第三方资料 + 项目文档一致 | **CONFIRMED** |
| 连接器名称 | **J2** | 同上 | **CONFIRMED** |
| PL IO 数量 | **8 个** | 第三方对照表 | **CONFIRMED（旁证）** |
| 所属 PL Bank | **HDIO Bank 45** | 项目 `INTERFACE.md`：official reference | **INFERRED**（项目文档，未取官方页） |
| I/O 标准 | **LVCMOS33** | 项目文档引用 "official BIST" | **INFERRED** |
| 8 个 package pin | **H12, E10, D10, C11, B10, E12, D11, B11** | 第三方表；其中 H12/B10 与项目 `candidate_pmod.xdc` 的 "Official BIST: HDA11=H12 and HDA15=B10" 互证 | **INFERRED（双源互证，仍非官方原理图）** |

### 2.2 8 个 package pin（第三方表给出的排列顺序）

| 序 | Package Pin | Bank | I/O 标准 | 状态 |
|---|---|---|---:|---|
| 1 | **H12** | 45 | LVCMOS33 | INFERRED |
| 2 | **E10** | 45 | LVCMOS33 | INFERRED |
| 3 | **D10** | 45 | LVCMOS33 | INFERRED |
| 4 | **C11** | 45 | LVCMOS33 | INFERRED |
| 5 | **B10** | 45 | LVCMOS33 | INFERRED |
| 6 | **E12** | 45 | LVCMOS33 | INFERRED |
| 7 | **D11** | 45 | LVCMOS33 | INFERRED |
| 8 | **B11** | 45 | LVCMOS33 | INFERRED |

### 2.3 仍未确认（**UNKNOWN，禁止猜测**）

| 项 | 状态 | 说明 |
|---|---|---|
| **J2 Pin 1 物理位置/方向** | **UNKNOWN** | 资料未给出丝印/键位方向；插反会短路 VCC↔GND |
| **package pin ↔ J2 针号映射** | **UNKNOWN** | 第三方表只给 8 个 package pin 的**顺序**，未声明第 1 个对应 J2 第几针 |
| **J2 Pin 1/2/39/40 等电源与地针定义** | **UNKNOWN** | 标准 Digilent Pmod 为 12 针（6+6），但 KV260 是否完全遵循未核实 |
| **Bank 45 实际 VCCO** | **UNKNOWN** | 若为 1.8 V，则 LVCMOS33 与 3.3 V 上拉均不成立 |
| **Pmod VCC 针电压** | **UNKNOWN** | 必须万用表实测 |
| **板载上拉/串联电阻/ESD 器件** | **UNKNOWN** | `candidate_pmod.xdc` 无 `PULLUP`，暗示无片内兜底；板上是否有物理上拉未知 |

## 3. 本项目实际需要的信号（按现有 RTL 核对，不按名称推断）

从 `rtl/kv260_haptic_top.v` 端口声明逐条核对：

| 逻辑信号 | **实际 RTL 端口名** | 方向 | 类型 | OE/极性 | 依据 |
|---|---|---|---|---|---|
| I²C 时钟 | **`i2c_scl_pin`** | `inout` | 开漏（`scl_drv` 1=拉低，0=释放） | — | `kv260_haptic_top.v` L39；`i2c_master.v` L46-49 |
| I²C 数据 | **`i2c_sda_pin`** | `inout` | 开漏（`sda_drv` 1=拉低，0=释放） | — | `kv260_haptic_top.v` L40 |
| 输出使能 | **`oe_n`** | `output wire` | 推挽输出 | **1 = 关闭（OE# 高）**，**0 = 使能（OE# 低）** | `haptic_oe_guard.v` L14-15, L146 |

### 3.1 逐项审查

| 审查项 | 结论 | 依据 |
|---|---|---|
| SCL/SDA 是否真正用 IOBUF | ⚠️ **未验证**。RTL 只声明 `inout`，由综合推断；**OOC 综合把 tricell 转成了 logic**（2 条 `Synth 8-5799` critical warning） | `phase1f/utilization.rpt` / 综合日志 |
| 是否可能主动驱动高电平 | ✅ **不可能**。`scl_drv`/`sda_drv` 只控制「拉低/释放」，从不驱动高 | `i2c_master.v` L10-11 注释 + L46-47 |
| 外部 4.7 kΩ 上拉是否必要 | ✅ **必要**。RTL 无内部上拉，`candidate_pmod.xdc` 也未加 `PULLUP` | `candidate_pmod.xdc` 全文 |
| 上拉电压是否与 Bank 兼容 | ❌ **UNKNOWN**。依赖 Bank45 VCCO 实测值（3.3 V 则兼容 3.3 V 上拉；1.8 V 则不兼容） | — |
| OE_N 复位默认是否能确保关闭 | ✅ **是**。`oe_n` 复位值 `1'b1`（关闭），且 `arm_ready` 需 `init_healthy` + 软件请求 + 延时 | `haptic_oe_guard.v` L140-150 |
| **PL 未配置/掉电时 OE 是否有可靠硬件默认关断** | ⚠️ **部分**。未配置时 FPGA 引脚高阻，**必须靠外部 3.3 V 上拉**把 OE# 拉高；RTL 本身无法保证 | `haptic_oe_guard.v` L20-23 注释 |
| **PL 时钟停振时 OE 状态** | ❌ **未覆盖**。`oe_n` 是寄存器输出，时钟停振会保持最后值（若此前为使能则持续使能） | 红队审计结论 |

## 4. 外围电路架构与电源域划分

```
KV260 FPGA ──I²C──▶ TCA9548A ──▶ PCA9685 ──▶ 振动驱动级 ──▶ 振动执行器
              │                      │              │              │
           逻辑 3.3V              逻辑 3.3V      驱动级电源      执行器电源
```

### 4.1 电源域（严格区分，不得混为一谈）

| # | 电源域 | 供电对象 | 额定值 | 状态 |
|---|---|---|---:|---|
| 1 | **KV260 逻辑电源** | FPGA Bank45 IO | 3.3 V（?） | **UNKNOWN（须实测 VCCO）** |
| 2 | **TCA9548A 逻辑电源 VCC** | TCA9548A 内部逻辑 | 3.3 V | **UNKNOWN**（设计值 3.3 V，未实测） |
| 3 | **PCA9685 逻辑电源 VDD** | PCA9685 内部逻辑/振荡器 | 3.3 V | **UNKNOWN** |
| 4 | **PCA9685 执行器电源 V+** | PWM 输出级上拉 | **独立供电，额定值 UNKNOWN** | **未确认，禁止试供电** |
| 5 | **GND 参考** | 全部模块共地 | 0 V | **必须共地；是否已共地 UNKNOWN** |
| 6 | **驱动级与执行器** | 振动马达 | **UNKNOWN** | **未确认** |

**⚠️ VDD（逻辑）与 V+（执行器）是两个独立电源域，绝不可并接。**
PCA9685 的 V+ 决定 LED 输出的高电平；若把执行器电源接到 VDD 会烧毁芯片。
**执行器独立供电、FPGA 只提供控制信号** —— 与既定方案一致。

## 5. 最终接线表

| 模块 | 引脚 | 连接对象 | 电压 | 方向 | 依据 | 状态 |
|---|---|---|---|---|---|---|
| KV260 | J2 某针 | I²C_SCL → TCA9548A SCx | 3.3 V (?V) | 开漏双向 | RTL `i2c_scl_pin` | **BLOCKED** |
| KV260 | J2 某针 | I²C_SDA → TCA9548A SDx | 3.3 V (?V) | 开漏双向 | RTL `i2c_sda_pin` | **BLOCKED** |
| KV260 | J2 某针 | HAPTIC_OE_N → PCA9685 OE# | 3.3 V | FPGA 输出，低有效 | RTL `oe_n` | **BLOCKED** |
| KV260 | J2 GND 针 | 公共地 | 0 V | 电源参考 | 共地要求 | **UNKNOWN（针号未定）** |
| KV260 | J2 VCC 针 | 逻辑 3.3 V 参考（仅上拉用） | **UNKNOWN** | 电源 | — | **UNKNOWN** |
| 外部 | 4.7 kΩ ×2 | SCL/SDA → 3.3 V | 3.3 V | 上拉 | 开漏总线必需 | **INFERRED（必需但未实施）** |
| TCA9548A | VCC | 3.3 V 逻辑电源 | 3.3 V | 电源 | 芯片规格 | **UNKNOWN（未实测）** |
| TCA9548A | GND | 公共地 | 0 V | 电源 | 共地 | **UNKNOWN** |
| TCA9548A | A0/A1/A2 | 地址选择 → 0x70 | — | 输入 | `pca_init_sequencer.v` L78 `TCA_ADDR_W=0xE0` | **INFERRED** |
| TCA9548A | RESET | 上拉至 3.3 V | 3.3 V | 输入 | 未使用硬复位 | **UNKNOWN** |
| PCA9685 ×16 | VDD | 3.3 V 逻辑电源 | 3.3 V | 电源 | 芯片规格 | **UNKNOWN** |
| PCA9685 ×16 | V+ | **独立执行器电源** | **UNKNOWN** | 电源 | 与 VDD 分离 | **BLOCKED** |
| PCA9685 ×16 | OE# | FPGA `oe_n` | 3.3 V | 输入 | RTL | **BLOCKED** |
| PCA9685 ×16 | SCL/SDA | TCA9548A 通道 ch0~3 | 3.3 V | 开漏 | `pca_init_sequencer.v` L11 | **UNKNOWN（走线未定）** |
| 驱动级 | — | PCA9685 PWM → 马达 | **UNKNOWN** | — | — | **UNKNOWN** |
| 执行器 | — | 驱动级输出 | **UNKNOWN** | — | — | **BLOCKED** |

## 6. 结论

### 6.1 已确认的引脚
- **KV260 唯一 Pmod 连接器为 J2**（第三方 + 项目文档一致）
- **8 个可用 PL IO 的 package pin**：H12 / E10 / D10 / C11 / B10 / E12 / D11 / B11（第三方表 + 项目 BIST 参考双源互证，**仍非官方原理图**）
- **所属 Bank：45（HDIO）**，I/O 标准 **LVCMOS33**（项目文档引用官方 BIST）
- RTL 端口名：`i2c_scl_pin` / `i2c_sda_pin`（开漏 inout）、`oe_n`（输出，1=关断）

### 6.2 尚未确认的引脚
- **J2 Pin 1 的物理位置与方向**（UNKNOWN）
- **package pin → J2 物理针号的映射**（UNKNOWN）
- **Bank 45 实际 VCCO / Pmod VCC 电压**（UNKNOWN）
- **OE_N 落到哪个 package pin**（UNKNOWN，且 `candidate_pmod.xdc` 里根本没有 OE 约束）
- 板上是否有上拉/串阻（UNKNOWN）

### 6.3 必须现场测量的项目
见 `KV260_HAPTIC_FIELD_CHECKLIST.md` 共 12 项。

### 6.4 当前是否具备条件
| 目标 | 是否具备 | 缺什么 |
|---|---|---|
| **单 PCA9685 上板** | ❌ **不具备** | J2 针号映射 + VCCO + OE 引脚 + 外部上拉 + XDC + bitstream |
| **单执行器振动验证** | ❌ **不具备** | 上述全部 + 驱动级 + 执行器 V+ 额定值 + OE 硬件关断电路 |
