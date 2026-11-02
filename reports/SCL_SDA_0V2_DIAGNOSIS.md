# SCL/SDA 约 0.2V —— 只读硬件诊断报告

**日期**：2026-11-02  **方式**：只读（未改 RTL/XDC/BD/TCL，未启动 Vivado，未重载 bitstream，未做 MMIO 写）

---

## 结论先行（回答五个问题）

| # | 问题 | 结论 | 依据 |
|---|---|---|---|
| 1 | SCL 当前应处什么状态 | **高阻（Hi-Z / 已释放）**，不是 FPGA 拉低 | `i2c_master.v` L92/L118-120/L111-112 + `pipeline` L553 |
| 2 | SDA 当前应处什么状态 | **高阻（Hi-Z / 已释放）** | `i2c_master.v` L92/L119-120 + `pipeline` L554 |
| 3 | 是否存在外部上拉 | **无证据存在；工程内也完全没有上拉配置** | 21 个 RTL 零 PULLUP；XDC 无 PULLUP |
| 4 | **0.2V 最可能原因** | **FPGA 已释放 + 无任何上拉 → 总线悬空**；次要但必须排除：**当前 PL 未加载本设计** | 见 §4 |
| 5 | 下一步最小验证 | 先加载 PL，再外接 4.7k 上拉复测 | 见 §6 |

---

## A. RTL 驱动链（逐级追踪，含行号）

### A.1 顶层 → 焊盘
```
kv260_haptic_top.v
  L59  inout wire i2c_scl_pin,
  L60  inout wire i2c_sda_pin,
  L237 .i2c_scl_pin (i2c_scl_pin),
  L238 .i2c_sda_pin (i2c_sda_pin)      -> kv260_haptic_pipeline
```

### A.2 pipeline 内的开漏实现
```verilog
kv260_haptic_pipeline.v
  L551  // 开漏三态: drv=1 拉低, drv=0 释放（上拉为高）; 顶层引脚处由 Vivado
  L552  // 推断 IOBUF, 综合语义与 IOBUF 原语等价
  L553  assign i2c_scl_pin = scl_drv ? 1'b0 : 1'bz;
  L554  assign i2c_sda_pin = sda_drv ? 1'b0 : 1'bz;
```
综合后等价于：
```
IOBUF .I(1'b0)  .T(~scl_drv)  .O(回读)  .IO(焊盘)
```
即 **`T=1` 时为高阻（释放总线），`T=0` 时只驱动 0**。
**完全符合开漏规范：只主动拉低或释放，从不主动推高。**

### A.3 **关键：`T=1` 代表什么**
- `T=1` = 三态关闭 = **高阻 = 释放总线**
- 释放后总线电平**完全由外部（或片内）上拉决定**
- **本设计没有片内上拉**（见 §C）→ 若无外部上拉，释放后就是**悬空**

### A.4 pipeline 自身注释已经明确警告这一点
```
kv260_haptic_pipeline.v L77-L79:
  // ---- v1.3: I2C 开漏总线引出（真实 IOBUF 在顶层引脚推断; 板上需上拉,
  //      XDC PULLUP 兜底。实际约束见
  //      constraints/ax7020_i2c.xdc）----
```
**设计作者已经写明："板上需上拉，XDC PULLUP 兜底"。**

### A.5 Phase 2E 的焊盘包装与 tran
```
E:\KVHAP_2E\rtl_pad\kv260_haptic_pad_wrapper.v
  -> 直接实例化冻结的 kv260_haptic_top，把 i2c_scl_pin / i2c_sda_pin 引为 inout 端口

E:\KVHAP_2E\rtl_pad\kv260_i2c_bringup_top.v（Phase 2E 版）
  L91  assign iic_scl_i = i2c_scl_io;          // 基线只读回焊盘，不再驱动
  L92  assign iic_sda_i = i2c_sda_io;
  L96  tran (i2c_scl_io, haptic_scl);          // 双向移交
  L97  tran (i2c_sda_io, haptic_sda);
  // 基线两个 IOBUF 已删除（消除双驱动，DRC MDRV-1 已复现并修复）
```
**结论：焊盘唯一驱动者是 haptic 的 IOBUF，无第二个驱动源。**

---

## B. I²C Master 状态（三处均释放总线）

```verilog
kv260_haptic_pipeline.v L186: wire scl_drv, sda_drv;   // 由 i2c_master 驱动

kv260_haptic_pipeline.v L543-544:
  .scl_drv (scl_drv), .sda_drv (sda_drv)
```

`i2c_master.v` 的 `scl_drv/sda_drv` 产生点：

| 场景 | 行号 | 赋值 | 对总线的影响 |
|---|---|---|---|
| **复位** | L92-93 | `scl_drv <= 1'b0; sda_drv <= 1'b0;` | **释放（高阻）** |
| **空闲 M_IDLE** | L118-120 | `scl_drv <= 1'b0;  // 释放总线（上拉为高）` `sda_drv <= 1'b0;` | **释放（高阻）** |
| **超时错误** | L102-112 | `mst <= M_IDLE;` `scl_drv <= 1'b0; sda_drv <= 1'b0;` | **释放（高阻）** |
| 拉低 SCL | L169 / L204 / L226 | `scl_drv <= 1'b1;` | 拉低 |
| 拉低 SDA | L155 / L185 | `sda_drv <= 1'b1;` | 拉低 |

### B.1 当前板端状态推断
Phase 2E 上板实测（上一轮，Linux 运行时）：
```
INIT_DONE    = 0     -> 初始化未完成（无 PCA9685，I2C 无 ACK）
FAULT_LATCHED= 1     -> 故障已锁存
ONLINE_MASK  = 0
```
初始化失败后 master 走超时错误分支（L102-112）或回到 M_IDLE（L118-120），
**两条路径都把 `scl_drv` / `sda_drv` 置 0 → 焊盘高阻。**

### B.2 故障锁存是否影响 SCL/SDA 输出？
**不影响线驱动。** `fault_latched` 只作用于：
- `matrix_buffer_axi` 的 commit 门控（Phase 2E 第一阶段已查明）
- OE# 守卫（`oe_n` 保持关断）

它**不参与 I²C 引脚的 `scl_drv`/`sda_drv` 逻辑**。
所以故障状态下 I²C 总线是**释放**的，不是被拉低。

### B.3 复位状态
`rst_n = s_axi_aresetn`（pipeline L98）。Phase 2E 中由 `diag_bus_resetn` 驱动。
无论复位与否，`scl_drv`/`sda_drv` 初始值都是 0（L92-93）→ 释放。

---

## C. 上拉电阻（关键缺口）

### C.1 工程内是否实例化 PULLUP
```
全 21 个 RTL 文件检索 PULLUP / PULLDOWN / pullup：
  仅 2 处注释命中（kv260_haptic_pipeline.v L78、tactile_render_top.v L78）
  实际原语实例数 = 0
```
**>>> 没有任何 PULLUP 原语。**

### C.2 Phase 2E 的 XDC 是否有 PULLUP
`E:\KVHAP_2E\rtl_pad\candidate_pmod.xdc` 全文：
```tcl
set_property PACKAGE_PIN H12 [get_ports i2c_scl_io]
set_property PACKAGE_PIN B10 [get_ports i2c_sda_io]
set_property IOSTANDARD LVCMOS33 [get_ports {i2c_scl_io i2c_sda_io}]
set_property DRIVE 4 [get_ports {i2c_scl_io i2c_sda_io}]
set_property SLEW SLOW [get_ports {i2c_scl_io i2c_sda_io}]
```
**>>> 没有 `set_property PULLUP`。**
（仅有 PACKAGE_PIN / IOSTANDARD / DRIVE / SLEW）

### C.3 设计原本预期的约束（对照）
AX7020 参考 `constraints/ax7020_i2c.xdc` 明确写了：
```tcl
set_property PULLUP TRUE [get_ports i2c_scl]
set_property PULLUP TRUE [get_ports i2c_sda]
```
并附注释：
> 「上拉: J11 EX_IO 无板载上拉 -> PULLUP TRUE 片内 (~25k) 仅保证空闲电平与
> 从机缺位时的 ACK 采样; **真实挂 TCA9548/PCA9685 时必须外接 4.7k 上拉到
> 排针 3.3V**（勿接 5V，官方手册警告 5V 器件需电平转换）。」

**即：连 AX7020 那种"有片内 PULLUP"的场合都要求真实外接 4.7k；而我们连片内 PULLUP 都没有。**

### C.4 KV260 J2/PMOD 是否有板载上拉
- 基线 `candidate_pmod.xdc` 注释只写：
  「Official BIST: HDA11=H12 and HDA15=B10, bank45 LVCMOS33」——**未提及上拉**
- `rtl/kv260_haptic_pipeline.v` L77-79 注释写「板上需上拉」→ **暗示板上没有**
- 我们此前对 J2 的照片审计也只确认了机械位置，未发现上拉电阻
- **结论：没有证据表明 H12/B10 有板载上拉；按设计注释应视为"无上拉"。**

---

## D. 板端只读状态（本轮实测）

### D.1 ⚠️ 板卡当前不在 Linux，而在 U-Boot
```
串口回显：ZynqMP>
```
U-Boot 只读查询：
```
printenv bootcmd = ... run som_mmc_boot; run som_cc_boot
fpga info:
  Family: ZynqMP PL
  Device name: xck26
  Device Size: 1 bytes
  Cookie: 0x0
  PCAP status 0xa02
```
**`Device Size: 1 bytes` / `Cookie: 0x0` 表明 PL 描述符为空，
即当前没有加载我们的 Phase 2E 设计。**

### D.2 影响（必须指出）
**如果 0.2V 是在"我们设计未加载"状态下测的，那它反映的是未配置 IO 的默认状态，
与我们的 RTL 无关。** 这一点必须先排除，否则结论会被误导。
（Linux 下的 UIO 枚举本轮无法执行，因为 Linux 没运行。）

### D.3 Phase 2E 寄存器映射（供后续对照）
| 基址 | 大小 | UIO 名（Phase 2E） |
|---|---|---|
| 0x9C000000 | 64 KiB | `kv260-haptic` |
| 0x9E000000 | 64 KiB | `kv260-iic-diag` |
| 0x9F000000 | 64 KiB | `kv260-pspl-mailbox` |

可供安全读取的 I²C 状态寄存器（均在已确认的 haptic 窗口内，**不涉及任何未确认地址**）：
| 偏移 | 名称 | 用途 |
|---|---|---|
| 0x1028 | ST_INIT_DONE | 初始化是否完成 |
| 0x102C | ST_ONLINE_MASK | 在线块掩码 |
| 0x103C | ST_FAULT_LATCHED | 故障锁存 |
| 0x1054 | ST_RECOVERY_STATUS | 恢复状态 |
| 0x040 | ST_I2C_ERR_CNT | I²C 错误计数 |
| 0x050 | ST_I2C_TIMEOUT_CNT | **I²C 超时计数（可证明 master 确实尝试过事务）** |
| 0x2000 | OE_CTRL | OE 状态（bit1 = oe_n） |

> 读这些寄存器**不能**判断引脚电平（电平由 IO 决定），但能确认 master 处于 IDLE。

---

## 4. 0.2V 最可能的原因（分层判断）

### 主因（最可能）：**FPGA 已释放 + 完全没有上拉 → 总线悬空**
- RTL 三处逻辑（复位/空闲/超时）都置 `drv=0` → 焊盘 `1'bz`
- 工程内 **0 个 PULLUP 原语**
- Phase 2E XDC **无 PULLUP 约束**
- 无证据表明 KV260 侧有板载上拉
- 高阻节点上万用表读数由漏电/耦合决定，**0.2V 是典型值**

### 次因（必须先排除）：**PL 未加载本设计**
- 板卡当前在 U-Boot，`fpga info` 显示描述符为空
- 若测量时 PL 未配置，H12/B10 为未配置 IO 默认态，0.2V 与我们的设计无关

### 已排除：FPGA 主动拉低
- 若 IO 主动拉低，应读到 **VOL ≈ 0.0–0.05V**，而非 0.2V
- RTL 三个状态（复位/空闲/超时）全部 `drv=0`
- `fault_latched` 不参与线驱动

---

## 5. 三个状态的区别（判据表）

| 现象 | 万用表读数 | 外接 4.7k 上拉到 3.3V 后 | 判定 |
|---|---|---|---|
| FPGA 拉低 | 0.0–0.05 V | 仍 ~0.2 V 或更低 | 主动拉低（RTL/约束问题） |
| **Hi-Z + 无上拉** | **0.1–0.5 V（飘）** | **升到 ~3.3 V** | **✅ 本报告主因** |
| Hi-Z + 有上拉 | ~3.3 V | ~3.3 V | 正常 |

---

## 6. 下一步最小硬件验证方案（三步，不违反任何约束）

### 第 0 步（前置，必须先做）：让 PL 处于"已加载本设计"状态
```
启动 Linux -> sudo xmutil loadapp kv260-haptic-2e
确认：/sys/class/fpga_manager/fpga0/state = operating
      /sys/class/uio/uio6/name = kv260-haptic
```
**在 PL 未加载状态下测电压毫无意义。**

### 第 1 步（决定性）：外接 4.7 kΩ 上拉到 3.3 V
- SCL(H12) 与 SDA(B10) 各接一个 4.7 kΩ 到 **3.3 V**（不要接 5 V）
- 重新测量：
  - **升到 ~3.3 V → 确认是"Hi-Z + 缺上拉"，问题解决**
  - **仍 ~0.2 V → FPGA 确实在拉低**，需回查 RTL/约束（下一步）

### 第 2 步（若第 1 步仍低）：确认 Bank45 VCCO
- 实测 H12/B10 所在 Bank45 的 VCCO 是否为 3.3 V
- 若 VCCO ≠ 3.3 V，则 LVCMOS33 约束本身不成立，IO 行为不可预期

### 第 3 步（软件侧只读确认，无需改代码）
加载 app 后只读以下寄存器，确认 master 在 IDLE、且确实尝试过事务：
```
0x1028 ST_INIT_DONE     期望 0（无 PCA9685）
0x103C ST_FAULT_LATCHED 期望 1
0x050  ST_I2C_TIMEOUT_CNT 期望 >0（证明发生过超时，不是完全没动）
0x2000 OE_CTRL          期望 bit1=1（oe_n=1，输出关断）
```

### 建议的最小修正（等第 1 步结论后再做，本轮不实施）
若确认是缺上拉，最小修正是**在 XDC 加两行**（无需改 RTL）：
```tcl
set_property PULLUP TRUE [get_ports {i2c_scl_io i2c_sda_io}]
```
但注意：**片内 PULLUP 约 25 kΩ，对 400 kHz Fast-mode 的上升沿偏慢**，
AX7020 那份 XDC 自己也写了「真实挂 TCA9548/PCA9685 时必须外接 4.7k」。
**接真实器件时应以外接 4.7 kΩ 为主，片内 PULLUP 仅作兜底。**

---

## 7. 本轮边界

- ✅ 未修改任何 RTL / XDC / BD / TCL
- ✅ 未启动 Vivado 综合或实现
- ✅ 未重新加载 bitstream、未重启板卡
- ✅ 未进行任何 MMIO 写操作
- ✅ 板端仅做只读查询（串口 `printenv` / `fpga info`）
