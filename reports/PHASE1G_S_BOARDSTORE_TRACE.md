# Phase 1G-S：官方 BoardStore 引脚闭环追踪报告

**日期**：2026-11-01  **数据源**：Vivado 2025.2 随安装分发的 AMD XilinxBoardStore
**结论**：BoardStore **无法**闭合成 J2 针号映射；但**推翻了原候选中的 6 个引脚**，并**排除了 HDA IIC Switch 网络冲突**。

---

## 1. 已安装版本（实测）

| 板卡 | 版本 | board.xml 大小 |
|---|---|---:|
| `kv260_carrier` | **1.2, 1.3** | 32,798 / 32,799 B |
| `kv260_som` | 1.2, 1.3, 1.4 | 20,318 / 20,325 / 20,328 B |
| `k26c`（K26 SOM 商用） | 1.2, 1.3, 1.4 | 63,033 / 62,848 / 62,849 B |

`kv260_carrier` 仅安装 **1.2 与 1.3** 两个版本，两者组件与接口完全一致（仅 1 字节差异）。

---

## 2. `kv260_carrier` 全部组件与接口（完整解析，非只看 hda_iic_switch）

**9 个组件**：`som240_1_connector`、`ps8_fixedio`、**`hda_iic_switch`**、`mipi_csi_raspi`、
`mipi_csi_ias`、`mipi_csi_isp`、**`bank45_gpio`**、`i2s_in`、`i2s_out`

**8 个接口**：`ps8_fixedio`、`hda_iic_switch`、`mipi_csi_raspi/ias/isp`、`bank45_gpio`、`i2s_in`、`i2s_out`

### 2.1 关键否定结论：BoardStore 中**不存在** Pmod/J2 相关定义

| 搜索目标 | 结果 |
|---|---|
| `pmod1_pin1` … `pmod1_pin12` | **0 命中**（整个 BoardStore 309 个 XML 中，含 Pmod 的仅 kd240/kr260/scu35，**无 kv260**） |
| `HDA11`…`HDA18` 网络名 | **0 命中**（kv260 相关文件中无任何 `HDA\d+` 形式网络名） |
| `J2` / `Pmod` 接口 | **0 命中** |
| `som240_1_a17` / `b20` / `d20` / `b21` / `d21` / `b22` / `d22` / `c22` | 在 `som240_1_connector` 的 240 针定义中**全部存在**（但只是连接器针名，不含 J2 信息） |

**因此：BoardStore 只提供"板级接口抽象"（供 BD 集成用），不提供连接器级原理图网表。
它无法把 SOM240 针号翻译成 J2 针号。**

---

## 3. 索引体系澄清（关键方法学）

最初误判为"索引不可互译"，实际原因是两套编号：

| 文件 | 索引含义 |
|---|---|
| `kv260_carrier/1.3/board.xml` | `<pin index="N" name="som240_1_aN">` —— **index 即连接器针号 1..240**，name 为针标签 |
| `k26c/1.4/part0_pins.xml` | `index` 是**跨 SOM240_1/SOM240_2 的全局序号**（1..206，仅 IO），**不能**与连接器针号直接对齐 |

**正确的桥接方式是"按网络名匹配"，不是按 index 匹配。**
用 `som240_1_<row><num>` 名称去 `part0_pins` 查 `loc`，结果与我们的 8 个候选**完全吻合**（sanity check 8/8 通过）。

---

## 4. `hda_iic_switch` 核实结果 —— 网络冲突 **CLOSED**

### 4.1 实际 XML 连接关系（证据）

`kv260_carrier/1.3/board.xml`：
```xml
<connection name="som240_connector_1_hda_iic_switch_connection"
            component1="som240_1_connector" component2="hda_iic_switch">
  <connection_map name="som240_1_connector_hda_iic_switch_connection_1"
                  c1_st_index="196" c1_end_index="197" c2_st_index="0" c2_end_index="1"/>
</connection>
<component name="hda_iic_switch" display_name="HDA IIC SWITCH">
  <description>AXI IIC connected to HDA IIC SWITCH on AI Vision Starter Kit carrier card</description>
```

### 4.2 索引解析（按网络名桥接）

| SOM240 针号 | 连接器针名 | **Package Pin** |
|---|---|---|
| 196 | `som240_1_d16` | **G11** |
| 197 | `som240_1_d17` | **F10** |

而 `bank45_gpio` 连接到：`d18`(**J11**)、`b17`(**K13**)、`b18`(**K12**)、`a15`(**F11**)、`c24`(**A12**)。

### 4.3 判定

- `hda_iic_switch` 上游仅 **2 根信号**（G11、F10），符合"SCL/SDA 一对"的特征。
- **与我们的 8 个候选 pin（H12/B10/E10/E12/D10/D11/C11/B11）没有任何交集。**
- 因此：**HDA IIC Switch 与 J2 的 HDA11–HDA18 在官方板级网表层面是两组独立网络。**

> **HDA IIC Switch 冲突风险：CLOSED（仅限网络级）**
> 局限：BoardStore 给出的是板级接口，不是原理图；"网络不重叠"已证，"物理走线是否共用连接器"仍需 XTP682。

---

## 5. 八条映射闭环结果

`Package Pin ↔ SOM240 针名` 由 AMD 官方 `k26c/part0_pins.xml` 验证（8/8 通过）；
`SOM240 针名 ↔ J2 针号` **无法从 BoardStore 取得**。

| 目标链路 | Package Pin | SOM240 针名 | SOM240 针号 | Bank | 载板接口占用 | J2 针号 |
|---|---|---|---|---:|---|---|
| H12 → A17 → HDA11 → J2.1 | **H12** | `som240_1_a17` | 11 | 45 | **无（FREE）** | **UNKNOWN** |
| B10 → B20 → HDA15 → J2.2 | **B10** | `som240_1_b20` | 23 | 45 | **无（FREE）** | **UNKNOWN** |
| E10 → D20 → HDA12 → J2.3 | **E10** | `som240_1_d20` | 51 | 45 | **`i2s_out`** | **UNKNOWN** |
| E12 → B21 → HDA16_CC → J2.4 | **E12** | `som240_1_b21` | 24 | 45 | **`i2s_in`** | **UNKNOWN** |
| D10 → D21 → HDA13 → J2.5 | **D10** | `som240_1_d21` | 52 | 45 | **`i2s_out`** | **UNKNOWN** |
| D11 → B22 → HDA17 → J2.6 | **D11** | `som240_1_b22` | 25 | 45 | **`i2s_in`** | **UNKNOWN** |
| C11 → D22 → HDA14 → J2.7 | **C11** | `som240_1_d22` | 53 | 45 | **`i2s_out`** | **UNKNOWN** |
| B11 → C22 → HDA18 → J2.8 | **B11** | `som240_1_c22` | 37 | 45 | **`i2s_in`** | **UNKNOWN** |

**8/8 的"Package Pin ↔ SOM240"闭环成功；"SOM240 ↔ J2"0/8 闭环。**

---

## 6. 本轮最重要发现：6 个候选引脚被官方分配给 I2S

### 6.1 Bank 45 全部 24 个引脚的载板占用状态（实测）

**被载板接口占用（13 个）**：
`A12, B11, C11, D10, D11, E10, E12, F10, F11, G11, J11, K12, K13`

**未被任何载板接口引用（11 个）**：
`A13, A14, B10, B13, B14, C14, D14, G10, H11, H12, J10`

### 6.2 对候选分配的影响

| 候选 | 状态 |
|---|---|
| `i2c_scl_pin` = **H12** | ✅ **FREE**（未被任何载板接口引用）→ 可保留 |
| `i2c_sda_pin` = **B10** | ✅ **FREE** → 可保留 |
| `oe_n` = **B11** | ❌ **官方分配给 `i2s_in`** → **必须重新决策** |

### 6.3 由此得出的工程判断

- `i2s_in`(E12,D11,B11) + `i2s_out`(E10,D10,C11) = **6 根**，加上 H12、B10 = 正好 **8 根**，
  与第三方"J2 有 8 个 IO"的说法数量吻合。
- 强烈提示：**AMD 官方把 J2（Pmod）的 8 个 IO 中的 6 个建模为 I2S 接口**（Pmod 可作音频口）。
  这是"板级接口分配"，**不等于物理上禁止他用**，但也**不等于**这 6 根就是 J2 的 3–8 针 ——
  BoardStore 没有给出针号。
- 若 6 根确实属于 I2S，则 **J2 的全部 8 个 IO 已被 H12、B10、I2S×6 占满，没有第 9 根 IO 给 OE_N**。
  这意味着 **OE_N 无法与 I2C 同时从 J2 引出**，除非占用一个 I2S 脚（即放弃音频用途）。

**这是 Phase 1G-S 最重要的结论，且它无法在没有 XTP682 的情况下定案。**

---

## 7. 板卡版本核验

| 项 | 结果 |
|---|---|
| BoardStore `kv260_carrier` 版本 | 1.2、1.3（**无 1.0/2.0 概念**，BoardStore 版本号 ≠ 载板 Rev） |
| BoardStore 是否记录载板 Rev 1.0 / Rev 2.0 | **否**。board.xml 中无 rev1.0/rev2.0 字段 |
| 实物板卡版本 | 照片中**未见** rev 丝印（可见条码 `2DC026012215`、`R73`、`MH4`、`J8`、`JAS 1`、`J15`） |
| 设备树实测（H0 审计） | `/sys/firmware/devicetree/base/model = "ZynqMP KV260 revB"` → **revB** |
| **Rev 1.0 vs Rev 2.0 电路差异** | **UNKNOWN** —— 不能默认两个修订版电路完全相同 |

---

## 8. 电源核验（区分三类证据）

| 项 | 官方设计资料 | 工具配置 | 实物测量 |
|---|---|---|---|
| Bank45 VCCO 配置 | ❌ 需 XTP682 | ⚠️ BoardStore 未记录 VCCO 值；`place_design` 仅证明"LVCMOS33 与 bank45 不冲突" | ❌ 未测 |
| PL_3V3 是否供 Bank45 | ❌ 未知 | — | ❌ 未测 |
| J2 3.3V 供电能力 | ❌ 未知 | — | ❌ 未测 |
| Pmod 100 mA 电流限制 | ❌ **未在任何已取得资料中找到该数字**（用户提及，但我未验证到出处，**不采信为事实**） | — | ❌ 未测 |
| 外部 I²C 上拉要求 | ✅ 开漏总线必需（通用电气要求，非板卡特定） | — | ❌ 未实施 |

**三类证据严格分开：工具"不报错" ≠ 板卡"电压正确"。**

---

## 9. 结论

| 问题 | 答案 |
|---|---|
| **J2 映射是否完整闭环？** | **否**。BoardStore 只能到 SOM240 层；J2 针号与 HDA 网络名完全不在其中 |
| **HDA I2C Switch 是否与 J2 信号冲突？** | **网络级已排除**（G11/F10 vs 我们的 8 根，无交集）。物理走线仍 UNKNOWN |
| **三个候选能否升级为 CONFIRMED？** | **否**。H12/B10 为 FREE（可保留）；**B11 被官方分配给 i2s_in，需重新决策** |
| **哪些电气条件仍需实测？** | Bank45 VCCO、J2 VCC 电压、板上拉/串阻、Pmod 供电能力 |

**BoardStore 是有价值的旁证，但它替代不了载板原理图。**
