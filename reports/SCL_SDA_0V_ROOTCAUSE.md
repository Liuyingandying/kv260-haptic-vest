# SCL/SDA 双引脚 0V 故障诊断报告（只读）

**日期**：2026-11-02  **方式**：只读（未改 RTL/XDC/BD，未重建，未做 PL MMIO 写，未接外设）

---

## 0. 结论速览

**决定性发现：Phase 2F 的已实现设计中，两个 I²C 引脚根本没有三态缓冲 —— 只有普通 `OBUF`。**

```
NET i2c_scl_io : CELL=i2c_scl_io_OBUF_inst   REF=OBUF
NET i2c_sda_io : CELL=i2c_sda_io_OBUF_inst   REF=OBUF
IOBUF count=0    OBUF count=2    OBUFT count=0    IBUF count=0
```

**开漏（tri-state）在综合/优化阶段丢失，变成了单向常驱动。这是双引脚同时为低的直接硬件原因。**

---

## 1. 板端实际加载状态（真实，非仅看 operating）

```
xmutil listapps:
  kv260-haptic-2f   PL_FLAT   ...   0->0,          <- 我们的设计正在 slot 0
  kv260-haptic-2e/d/debug          -1

fpga_manager/fpga0/state = operating
/proc/device-tree/fpga-region/firmware-name = xilinx/kv260-haptic-2f/kv260_haptic_2f.bit.bin

uio4 kv260-iic-diag      0x9E000000
uio5 kv260-pspl-mailbox  0x9F000000
uio6 kv260-haptic        0x9C000000
```
**加载状态确认无误：确实加载的是 Phase 2F 设计。**

---

## 2. 部署 bitstream 与构建产物 SHA256 核对

| 文件 | 板端 SHA256 | 本地构建产物 | 一致 |
|---|---|---|---|
| `kv260_haptic_2f.bit.bin` | `51349fdfc37960d2e80fe45c041f9b6ba103c85698296b0f5376c04af21f14e1` | 同 | ✅ |
| `kv260_haptic_2f.dtbo` | `eb526255a54bd13e4caa4bbc639f9919625ff28c3564afde6e3ca781dddc0545` | 同 | ✅ |
| `shell.json` | `ed0f0900b65293ab42e1110640230b6608aea7bce17af81d3c762c4920cddb4f` | 同 | ✅ |

**排除"加载了旧/错位流"的可能。**

---

## 3. Phase 2F 实际 XDC

```
set_property PACKAGE_PIN H12 [get_ports i2c_scl_io]        # SCL = H12   ✅
set_property PACKAGE_PIN B10 [get_ports i2c_sda_io]        # SDA = B10   ✅
set_property IOSTANDARD LVCMOS33 [get_ports {...}]          # LVCMOS33   ✅
set_property PULLUP TRUE [get_ports {i2c_scl_io}]           # PULLUP      ✅
set_property PULLUP TRUE [get_ports {i2c_sda_io}]           # PULLUP      ✅
```
**约束本身完全正确。**

> ⚠️ **但 XDC 注释写的是：「Official BIST: HDA11=H12 and HDA15=B10」**
> 即这两个引脚对应的是 **HDA11/HDA15**，**不是 J2**。见 §6。

---

## 4. 已实现设计检查（**核心发现**）

### 4.1 端口属性（正确）
```
PORT i2c_scl_io  PULLTYPE=PULLUP  PULLUP=1  PIN=H12  IOSTANDARD=LVCMOS33
PORT i2c_sda_io  PULLTYPE=PULLUP  PULLUP=1  PIN=B10  IOSTANDARD=LVCMOS33
```

### 4.2 **IO 原语类型（严重异常）**
```
NET i2c_scl_io : CELL=i2c_scl_io_OBUF_inst   REF=OBUF
NET i2c_sda_io : CELL=i2c_sda_io_OBUF_inst   REF=OBUF

IOBUF  count = 0     <-- 应为 2
OBUF   count = 2     <-- 不应存在
OBUFT  count = 0     <-- 三态缓冲完全没有
IBUF   count = 0     <-- 回读缓冲也没有
```

### 4.3 含义
| 期望 | 实际 | 后果 |
|---|---|---|
| `IOBUF`（T 控制三态，开漏） | **`OBUF`（单向常驱动）** | **引脚被永久驱动，不能释放** |
| 空闲时 `T=1` → 高阻 → 上拉为高 | 无 T 输入 | **上拉无法把线拉高** |
| `IBUF` 回读总线电平 | 无 IBUF | **master 读不到总线状态** |

**`PULLUP=TRUE` 虽然设置成功，但 IO 被 OBUF 常驱动时，上拉被驱动源压倒 —— 引脚电平由 OBUF 决定，不由上拉决定。**
**这正是"不能仅凭 PULLUP=TRUE 就认定物理引脚一定有 3.3V"的原因，本次已被实测证实。**

### 4.4 是否存在其他驱动源
`report_io` 显示全设计 **Bonded IOB = 2**，只有 H12/B10 两个双向引脚，**没有第三个驱动源**。
所以 0V 不是被别的东西拉低，而是这两个 OBUF 自己在驱动。

### 4.5 为什么 OBUF 会输出低
`pipeline` 的开漏写法是 `assign pin = drv ? 1'b0 : 1'bz`。
当三态被优化成单向 `OBUF` 后，`1'bz` 无法表示，工具按最保守方式解析为常量 → **输出恒为 0 → 引脚 0V**。
这与实测"两脚同时约 0V"完全吻合。

---

## 5. RTL 当前 `scl_drv` / `sda_drv` 状态

`kv260_haptic_pipeline.v`：
```
L553  assign i2c_scl_pin = scl_drv ? 1'b0 : 1'bz;
L554  assign i2c_sda_pin = sda_drv ? 1'b0 : 1'bz;
```
`i2c_master.v` 的三处赋值：
| 场景 | 行号 | 值 |
|---|---|---|
| 复位 | L92-93 | `scl_drv<=0; sda_drv<=0` |
| 空闲 M_IDLE | L118-120 | `scl_drv<=0; sda_drv<=0` |
| 超时错误 | L111-112 | `scl_drv<=0; sda_drv<=0` |

**RTL 侧逻辑正确：复位/空闲/超时都应释放总线。**
问题不在 RTL 的 drv 逻辑，而在**综合把 `1'bz` 的实现方式搞成了常驱动 OBUF**。

---

## 6. J2.1 / J2.2 物理映射审查（仍然 UNKNOWN）

### 6.1 工程内已有记录（历史审计）
```
KV260_HAPTIC_PHASE1G_PINOUT_REPORT.md L17:
  | J2 物理针号 ↔ package pin 映射 | UNKNOWN |
KV260_HAPTIC_PHASE1G_PINOUT_REPORT.md L54:
  | Package Pin 映射 | 待官方资料闭环（SOM240 侧已确认，J2 侧未确认） |
KV260_HAPTIC_ARCHITECTURE.md L41:
  J2 mapping is UNKNOWN (inherited from H0)
```

### 6.2 本次新证据
Phase 2F 的 XDC 注释：**「Official BIST: HDA11=H12 and HDA15=B10」**
→ H12/B10 的官方依据是 **HDA 连接器的 11/15 脚**，**不是 J2**。

### 6.3 结论
> **J2.1 / J2.2 是否等于 H12 / B10，至今没有任何已验证证据。**
> 无 Rev 1.0/Rev 2.0 载板原理图（需 XTP682 资料），**不得宣称映射已确认**。

因此存在**第二个独立的根因候选**：用户测量的 J2.1/J2.2 可能压根不是 H12/B10。

---

## 7. 根因候选排序

| 排序 | 候选 | 证据强度 | 说明 |
|---|---|---|---|
| **1** | **开漏被综合成常驱动 OBUF，引脚被驱低** | **强（Vivado 实现网表实测）** | `IOBUF=0 / OBUF=2 / OBUFT=0 / IBUF=0`；与"两脚同时 0V"完全吻合 |
| **2** | **J2.1/J2.2 ≠ H12/B10（测错引脚）** | 中（记录 + XDC 注释） | 工程记录多次标注 J2 映射 UNKNOWN；XDC 依据是 HDA11/HDA15 |
| 3 | `tran` 跨界桥接导致双向性丢失 | 强（Phase 2E 引入该结构，之后才出现异常） | 很可能就是候选 1 的成因 |
| 4 | Bank45 VCCO ≠ 3.3V | 弱（未测） | 若 VCCO 异常，LVCMOS33 不成立 |
| 已排除 | FPGA 侧 drv 逻辑错误 | — | RTL 三处均释放，L553/L554 正确 |
| 已排除 | 加载了错误 bitstream | — | SHA256 三项全一致 |
| 已排除 | 约束写错引脚/电平 | — | PACKAGE_PIN/IOSTANDARD/PULLUP 均正确 |
| 已排除 | 有其他驱动源 | — | 全设计仅 2 个 IOB |

---

## 8. 尚未验证的项目

| 项 | 说明 |
|---|---|
| J2.1/J2.2 ↔ H12/B10 的物理对应 | **UNKNOWN**，需 XTP682 载板原理图 |
| Bank45 实际 VCCO | 未实测 |
| 测量点是否真的是 FPGA 引脚 | 无 continuity 检查 |
| 用逻辑分析仪观察引脚 | 未做 |
| 上拉在 OBUF 常驱动下的实际效果 | 本轮已由网表证明被压倒，但未做电学实测 |

---

## 9. 下一步最小实验（按性价比排序）

### 实验 1（最快、零构建）：先确认测的是不是同一根线
**做 continuity / 电压对照**：在板卡已加载 Phase 2F、上电状态下，
用万用表量 **H12/B10 对应的实际物理位置**（SOM240 HDA 连接器第 11/15 脚）
与 **J2.1/J2.2** 是否为同一网络。
- 若不通 → 根因 2 成立，之前测的根本不是我们的引脚
- 若通 → 根因 1 成立，进入实验 2

### 实验 2（决定性，需一次构建）：把开漏实现改回顶层 IOBUF
**不采用 `tran` 桥接**，改为像基线那样在顶层显式例化 `IOBUF`，
由 `scl_drv`/`sda_drv` 直接驱动 `.T`：
```verilog
IOBUF u_scl (.I(1'b0), .T(~scl_drv_from_haptic), .O(scl_i_from_haptic), .IO(i2c_scl_io));
```
需要把 haptic 的 `drv` 信号暴露为 output（当前只有 inout 引脚）。
预期结果：`IOBUF count=2`，空闲时引脚被上拉拉高。

**这是唯一能真正修复开漏的改动**，属于功能修改，本轮只读约束下不做。

### 实验 3（与实验 2 并行）：实测 Bank45 VCCO
确认 3.3 V，否则 LVCMOS33 前提不成立。

---

## 10. 边界

- ✅ 未修改 RTL / XDC / BD
- ✅ 未启动 Vivado 重建（仅 `open_project` + `open_run impl_1` 只读查询）
- ✅ 未执行任何 PL MMIO 写（本轮只做 Linux 文件/属性读取）
- ✅ 未连接 PCA9685
- ✅ 未改变供电
