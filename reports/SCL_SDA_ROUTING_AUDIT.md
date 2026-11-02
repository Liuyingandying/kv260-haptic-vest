# KV260 SCL/SDA 物理路由专项审计报告

**日期**：2026-11-02
**方式**：纯只读（未启动构建、未改 RTL/XDC、未 loadapp/unloadapp、未写 MMIO、未接 PCA9685）
**结论**：**工程侧（XDC + DCP）完全一致且全部通过；但板卡当前未加载我们的 bitstream，
此刻 J2 电压不能用于判定 IOBUF 是否正常。
另：`SOM240 ↔ J2` 这一环在仓库中仍无任何已确认证据。**

---

## 第一部分：Phase 2F ECO 工程的实际生效 XDC

### 1.1 实际生效的 XDC —— 无歧义

工程 `.xpr` 中登记的约束文件**只有一个**：

```
$PPRDIR/rtl_r2/candidate_pmod.xdc
```

> 这一条很重要：此前 Phase 2E 曾因"工程实际用的是基线目录里的 XDC 而不是我们的副本"
> 导致 PULLUP 根本没生效。本次确认只有一份，不存在选错文件的问题。

- 路径：`E:\KVHAP_2F_IOBUF_FIX_R2_MI4\rtl_r2\candidate_pmod.xdc`
- 大小：877 B
- SHA256：`aeadcd0747747f1f5a2dce1ec706d7cc68cfbb928e22b6982204a94abfd7b88e`

### 1.2 约束内容逐条核对

| 检查项 | 要求 | XDC 实际内容 | 结果 |
|---|---|---|---|
| SCL PACKAGE_PIN | H12 | `set_property PACKAGE_PIN H12 [get_ports i2c_scl_io]` | ✅ |
| SDA PACKAGE_PIN | B10 | `set_property PACKAGE_PIN B10 [get_ports i2c_sda_io]` | ✅ |
| IOSTANDARD | LVCMOS33 | `set_property IOSTANDARD LVCMOS33 [get_ports {i2c_scl_io i2c_sda_io}]` | ✅ |
| PULLUP | TRUE | `PULLUP TRUE [get_ports {i2c_scl_io}]` + `[get_ports {i2c_sda_io}]` | ✅ |
| DRIVE | — | `DRIVE 4` | ✅ |
| SLEW | — | `SLEW SLOW` | ✅ |
| CFGBVS | — | `CFGBVS VCCO` | ✅ |
| CONFIG_VOLTAGE | — | `3.3` | ✅ |

---

## 第二部分：已验证 `corrected_routed.dcp` 的网表检查

检查对象：`E:\KVHAP_2F_ECO_KEEP_20261102\corrected_routed.dcp`
（原始产物只读打开，未修改）

### 2.1 顶层端口属性（DCP 实测）

```
PORT i2c_scl_io PACKAGE_PIN=H12 IOSTANDARD=LVCMOS33 PULLTYPE=PULLUP PULLUP=1 DIR=INOUT
PORT i2c_sda_io PACKAGE_PIN=B10 IOSTANDARD=LVCMOS33 PULLTYPE=PULLUP PULLUP=1 DIR=INOUT
```

### 2.2 单元统计

```
IOBUF_COUNT  = 2
OBUF_COUNT   = 0
OBUFT_COUNT  = 2     ← 两个都在 IOBUF 内部
```

### 2.3 两个 IOBUF 的四引脚连接网络（逐条导出）

**SCL — `u_ps_bd/kv260_ps_bd_i/haptic_fix/inst/u_scl_iobuf`**

| 引脚 | 网络名 | 驱动/负载 |
|---|---|---|
| `.IO` | `.../inst/i2c_scl_io` → 顶层端口 `i2c_scl_io` | **H12** |
| `.I` | `.../inst/<const0>` | 驱动 = `.../inst/GND/G`，REF=**GND** |
| `.T` | `.../inst/T0` | 驱动 = `.../u_i2c/u_scl_iobuf_i_1/O`，REF=**LUT1**（单输入 = 反相器） |
| `.O` | `.../inst/scl_sense` | 18 个负载（见下） |

`.IO` 焊盘侧负载：
```
u_scl_iobuf/INBUF_INST/PAD   REF=INBUF      ← 唯一，合法
```

`.O` 的负载（前两条为关键采样点）：
```
scl_pad_sync_reg[0]/D                                    REF=FDRE
iic_probe/.../SCL_DEBOUNCE/INPUT_DOUBLE_REGS/syncstages_ff_reg[0]/D  REF=FDRE
u_i2c/FSM_sequential_mst[2]_i_13/I0 ... （共 18 条，全部为 FSM/LUT 输入）
```

**SDA — `u_ps_bd/kv260_ps_bd_i/haptic_fix/inst/u_sda_iobuf`**

| 引脚 | 网络名 | 驱动/负载 |
|---|---|---|
| `.IO` | `.../inst/i2c_sda_io` → 顶层端口 `i2c_sda_io` | **B10** |
| `.I` | `.../inst/<const0>` | 驱动 = `.../inst/GND/G`，REF=**GND** |
| `.T` | `.../inst/u_haptic_n_1` | 驱动 = `.../u_i2c/u_sda_iobuf_i_1/O`，REF=**LUT1** |
| `.O` | `.../inst/sda_sense` | 14 个负载 |

`.IO` 焊盘侧负载：
```
u_sda_iobuf/INBUF_INST/PAD   REF=INBUF      ← 唯一，合法
```

`.O` 的负载（前两条为关键采样点）：
```
sda_pad_sync_reg[0]/D                                    REF=FDRE
iic_probe/.../SDA_DEBOUNCE/INPUT_DOUBLE_REGS/syncstages_ff_reg[0]/D  REF=FDRE
u_i2c/ack_error_i_2/I0 ... （共 14 条）
```

### 2.4 `.T` 反相器逐级溯源（证明受 drv 控制）

**SCL：**
```
.T (IOBUF)
  ← T0
  ← u_scl_iobuf_i_1/O          REF=LUT1
     ← u_scl_iobuf_i_1/I0      NET=.../u_i2c/scl_drv
        ← scl_drv_reg/Q        REF=FDCE
```
**SDA：**
```
.T (IOBUF)
  ← u_haptic_n_1
  ← u_sda_iobuf_i_1/O          REF=LUT1
     ← u_sda_iobuf_i_1/I0      NET=.../u_i2c/sda_drv
        ← sda_drv_reg/Q        REF=FDCE
```

LUT1 是单输入查找表，只能实现反相/缓冲 → 对应 RTL 的 `~scl_drv` / `~sda_drv`。
**两个 drv 都溯源到 `i2c_master` 里的 FDCE 触发器，未改动。**

### 2.5 四处 ECO 迁移仍然存在（逐条确认）

| # | 采样点 | 当前网络 | 结果 |
|---|---|---|---|
| 1 | `scl_pad_sync_reg[0]/D` | `scl_sense` | ✅ 已迁移 |
| 2 | `iic_probe/.../SCL_DEBOUNCE/.../syncstages_ff_reg[0]/D` | `.../SCL_DEBOUNCE/INPUT_DOUBLE_REGS/scl_sense` | ✅ 已迁移 |
| 3 | `sda_pad_sync_reg[0]/D` | `sda_sense` | ✅ 已迁移 |
| 4 | `iic_probe/.../SDA_DEBOUNCE/.../syncstages_ff_reg[0]/D` | `.../SDA_DEBOUNCE/INPUT_DOUBLE_REGS/sda_sense` | ✅ 已迁移 |

**四处全部挂在 sense 网上，焊盘侧只剩 INBUF —— ECO 在最终布线网表中完整保留。**

### 2.6 布线状态

```
# of nets with routing errors ... : 0
```

---

## 第三部分：部署状态

### 3.1 文件 SHA256（板端 vs 构建端）

| 文件 | 板端 SHA256 | 构建端 | 一致 |
|---|---|---|---|
| `kv260_haptic_eco.bit.bin` | `924f752b4661e9f4500e8bb7b5f4acded901e2854af89593d38b8b2e5ac7f3d6` | 同 | ✅ |
| `kv260_haptic_eco.dtbo` | `baa44c836ffb0870d7447b187ef6337a1402e37a6d72fc553ad5075c3adacf19` | 同 | ✅ |
| `shell.json` | `ed0f0900b65293ab42e1110640230b6608aea7bce17af81d3c762c4920cddb4f` | 同 | ✅ |

### 3.2 `xmutil listapps` 现状

```
kv260-haptic-eco     PL_FLAT     id_ok     (0+0+0)    -1        ← 我们的，未加载
k26-starter-kits    XRT_FLAT     id_ok     (0+0+0)    0->0,     ← 原厂，占用 slot 0
```

### 3.3 当前 `firmware-name`

```
k26-starter-kits.bit.bin
```

### 3.4 原厂 app 与 haptic app 的明确区分

| | 原厂 `k26-starter-kits` | 我们的 `kv260-haptic-eco` |
|---|---|---|
| 类型 | XRT_FLAT | **PL_FLAT** |
| slot | **0->0,（已加载）** | -1（未加载） |
| firmware-name | **当前生效** | 未生效 |
| UIO | `uio3 axi-pmon` | 不存在 |
| 是否我们已验证的 bitstream | ❌ 不是 | ✅ 是（SHA256 一致） |

`fpga_manager/fpga0/state = operating` —— 但 **operating 只说明"有东西在跑"，不说明跑的是谁**。

---

## 第四部分：四项结论

### 4.1 SCL 完整信号路径

```
软件写 I2C 寄存器
  → i2c_master 内部 scl_drv（FDCE: scl_drv_reg/Q）
  → scl_drv
  → LUT1 反相（u_scl_iobuf_i_1）→ ~scl_drv
  → IOBUF.T （net: T0）
  → IOBUF.OBUFT，I 端接 GND（<const0>）
  → IOBUF.IO （net: .../inst/i2c_scl_io）
  → 顶层端口 i2c_scl_io
  → PACKAGE_PIN H12          ← 工程侧已确认
  → 封装/PCB → SOM240_1 A17  ← 官方 k26c 板卡文件已确认（历史 Phase 1G 审计）
  → HDA11 → J2.1             ← ⚠️ 无文件证据（见 4.4）

回读：
  H12 → INBUF（唯一焊盘侧负载）→ IOBUF.O
  → scl_sense → {scl_pad_sync_reg[0], iic_probe SCL_DEBOUNCE, i2c FSM 共 18 个负载}
```

### 4.2 SDA 完整信号路径

```
sda_drv（FDCE: sda_drv_reg/Q）
  → LUT1 反相（u_sda_iobuf_i_1）→ ~sda_drv
  → IOBUF.T （net: u_haptic_n_1）
  → IOBUF.OBUFT，I 接 GND
  → IOBUF.IO （net: .../inst/i2c_sda_io）
  → 顶层端口 i2c_sda_io
  → PACKAGE_PIN B10          ← 工程侧已确认
  → SOM240_1 B20             ← 官方已确认
  → HDA15 → J2.2             ← ⚠️ 无文件证据

回读：B10 → INBUF → IOBUF.O → sda_sense → 14 个负载
```

### 4.3 XDC 与 DCP 是否一致

✅ **完全一致。**

| 项 | XDC | DCP 实测 | 一致 |
|---|---|---|---|
| SCL PIN | H12 | H12 | ✅ |
| SDA PIN | B10 | B10 | ✅ |
| IOSTANDARD | LVCMOS33 | LVCMOS33 | ✅ |
| PULLUP | TRUE | PULLTYPE=PULLUP, PULLUP=1 | ✅ |

### 4.4 ⚠️ 映射链条中「未闭合的一环」

仓库内已有官方记录（`PHASE1G_R_AUDIT.md`）：

| 环节 | 状态 | 依据 |
|---|---|---|
| Package Pin ↔ SOM240 net | ✅ **CONFIRMED** | AMD k26c 板卡文件 |
| **SOM240 net ↔ 载板 J2 针号（HDA 网络名）** | ❌ **UNKNOWN** | **需 XTP682 载板原理图 —— 至今未取得** |

我搜索了工作区内所有 `.md/.json/.txt/.csv/.xdc`：**没有任何 SOM240→J2 的引脚表**，
也没有 XTP682/XTP685 原理图文件。

因此你给出的 `H12→SOM240_1 A17→HDA11→J2.1` 与 `B10→SOM240_1 B20→HDA15→J2.2`：

| 分段 | 我能否核实 |
|---|---|
| H12 / B10 是 package pin | ✅ 已核实（DCP 实测） |
| H12→SOM240_1 A17、B10→SOM240_1 B20 | ✅ 与你一致，且有历史官方记录支撑 |
| **SOM240→HDA11/HDA15→J2.1/J2.2** | ❌ **我无法从任何文件核实，这是你的板级发现** |

**另有一个未排风险**（仓库 TODO 原文）：
> 「TODO 7: whether HDA11/HDA15 pass through the carrier "HDA IIC SWITCH"」

若这两根网络经过载板的 I²C 开关，则即使接对 J2 针，信号也未必直通到 H12/B10。

### 4.5 实际 bitstream 是否为已验证版本

✅ **是。**

- 板端 `bit.bin` SHA256 = `924f752b…ac7f3d6`
- 该 bit.bin 由已验证 `kv260_pad_eco.bit`（SHA256 `35957a65…f55c95db`）经 `bootgen -process_bitstream bin` 生成
- 已验证 `corrected_routed.dcp` 中 IOBUF=2 / OBUF=0 / OBUFT=2 / 四引脚连接全部核验通过

### 4.6 当前板卡是否加载该版本

❌ **未加载。**

- `firmware-name = k26-starter-kits.bit.bin`（原厂）
- `xmutil listapps` 显示 haptic-eco 为 `-1`
- UIO 中**没有** `kv260-haptic`，只有 `uio3 axi-pmon`

---

## 第五部分：关于「J2 电压读数」的关键警告

**你指出的这一点完全正确，且是当前最重要的一条：**

> 当前 haptic app 未加载时，不能拿 J2 的电压读数判定 IOBUF 是否正常。

原因（工程侧证据）：

1. 现在 FPGA 里跑的是原厂 `k26-starter-kits`，**H12/B10 由原厂设计控制**，
   我们那两个 IOBUF 根本不在当前 FPGA 配置里。
2. 原厂设计对这两个脚可能是未使用/高阻/别的功能，测得的电平**与我们的开漏设计无关**。
3. `fpga_manager = operating` 只能说明"某设计在运行"，**不能说明是在运行我们的设计**。

**因此：在重新 `xmutil loadapp kv260-haptic-eco` 之前，任何 J2 电平测量都不具备判据价值。**
（本轮按约束未执行 loadapp。）

---

## 第六部分：逐项证据汇总

| # | 审计项 | 结果 | 证据来源 |
|---|---|---|---|
| 1 | 实际生效 XDC 唯一 | ✅ | `.xpr` 仅登记 `rtl_r2/candidate_pmod.xdc` |
| 2 | i2c_scl_io PACKAGE_PIN=H12 | ✅ | XDC + DCP 双重 |
| 3 | i2c_sda_io PACKAGE_PIN=B10 | ✅ | XDC + DCP 双重 |
| 4 | IOSTANDARD=LVCMOS33 | ✅ | XDC + DCP 双重 |
| 5 | PULLUP 生效 | ✅ | DCP `PULLTYPE=PULLUP PULLUP=1` |
| 6 | 两个 IOBUF 实例 | ✅ | `IOBUF_COUNT=2` |
| 7 | I/T/O/IO 网络已导出 | ✅ | 见 2.3 |
| 8 | IO 连 H12/B10 | ✅ | DCP 端口属性 |
| 9 | O 连 scl_sense/sda_sense | ✅ | 见 2.3 |
| 10 | T 受 scl_drv/sda_drv 控制 | ✅ | 逐级溯源到 FDCE（见 2.4） |
| 11 | 四处 ECO 迁移仍在 | ✅ | 见 2.5 |
| 12 | 布线 0 错误 | ✅ | `report_route_status` |
| 13 | 板端文件 SHA256 与构建端一致 | ✅ | 见 3.1 |
| 14 | 当前加载的是我们的 app | ❌ **否（原厂 app）** | 见 3.2–3.4 |
| 15 | SOM240 ↔ J2 映射 | ❌ **无文件证据** | 见 4.4 |

---

## 第七部分：本轮边界

- ✅ 未启动 Vivado 综合/实现/bitstream 生成
- ✅ 未修改任何 RTL / XDC / BD / TCL（仅新增只读审计脚本）
- ✅ 未执行 `xmutil loadapp` / `unloadapp`
- ✅ 未写任何 MMIO 寄存器
- ✅ 未接 PCA9685
- ✅ 原始产物（bit / dcp / rpt）只读打开，未覆盖
- ✅ 板端仅执行 `sha256sum` / `cat` / `ls` 类只读命令

---

## 第八部分：下一步最小验证

要把"工程侧已闭环"变成"硬件侧已闭环"，还差两步：

1. **`xmutil loadapp kv260-haptic-eco`**（需你授权，本轮禁止）
   → 之后才能测 J2 电平，且只有这时 IOBUF 才真正在 FPGA 里
2. **补上 SOM240→J2 的证据**：XTP682 载板原理图，或
   用万用表通断档直接从 J2.1/J2.2 测到 H12/B10
   → 这是唯一能闭合最后一环的方法
