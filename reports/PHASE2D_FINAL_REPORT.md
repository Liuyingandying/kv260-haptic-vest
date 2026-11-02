# Phase 2D 最终报告

**日期**：2026-11-02  **结果**：**找到真实已验证基线，定位并修复了导致全部历史挂死的地址窗口根因，
首次实现 Linux 用户态 → AXI → 我们的 FPGA 触觉设计 的真实读写**

---

## 0. 验收层级（严格区分）

| 层级 | 状态 |
|---|---|
| RTL 仿真通过 | ✅ 既有（21 项 / 1E / 1F / 1G-U / 1G-V / AXI 交错 T1–T7） |
| Vivado 实现通过 | ✅ **本轮新增：BD 验证 + 综合 + 实现 + 时序全部通过** |
| **Linux AXI 真实访问通过** | ✅ **本轮新增：读/写/读回全部成功，系统不挂死** |
| I²C 波形通过 | ❌ 未验证（未接示波器/逻辑分析仪） |
| PCA9685 实际响应通过 | ❌ 未验证（未连接，J2 引脚未实测确认） |
| 完整触觉链 | ❌ 未达成 |

---

## 1. 找到的真实已验证基线（关键）

位置在**工作区之外**：`E:\01_Projects\FPGA_Embedded\KV260_Basic_Preboard\kv260_i2c_bringup_20261019T090440Z\`

`DIGITAL_ACCEPTANCE_RESULT.json` 记录：
```
IMPLEMENTATION_PASS: PASS        BITSTREAM_GENERATED: PASS
BOARD_LOAD_PASS: PASS            LEGACY_AXI_REGRESSION: PASS
AXI_IIC_REGISTER_ACCESS: PASS    IIC_INTERNAL_TEST: PASS
BOARD_HEALTH: PASS
AFI: 0x200 (128-bit)   PL0: 99,999,999 Hz
setup_slack +1.892 ns   hold_slack +0.010 ns
active_application: kv260-iic-bringup   shell_type: PL_FLAT
external_i2c_ack: NOT_VERIFIED   external_waveform: NOT_VERIFIED
```

### 1.1 基线地址映射（`i2c_address_map.rpt` 实测）
| 基址 | 大小 | 从设备 | UIO |
|---|---|---|---|
| 0x9D000000 | 64K | `iic_probe` (Xilinx AXI IIC) | uio4 |
| 0x9E000000 | 64K | `diag_gpio` | uio5 |
| 0x9F000000 | 64K | `M_AXI_PERCEPTION` | uio6 |

---

## 2. 根因定位：地址窗口（决定性证据，非猜测）

### 2.1 直接证据

第一次把我们的 haptic 挂到 `0xA0000000` 时，Vivado BD 直接报错：

```
ERROR: [BD 41-1075] Cannot assign slave segment '/haptic_inst/s_axi/reg0' ...
The proposed address '0xA000_0000 [ 64K ]' must fit an available aperture through
master interface '/zynq_ultra_ps_e_0/M_AXI_HPM0_LPD' ...
Valid apertures are {<0x8000_0000 [ 512M ]>}.
```

> **基线的 PS 只把 `0x8000_0000 – 0x9FFF_FFFF`（512 MB）这一个窗口通过
> `M_AXI_HPM0_LPD` 路由到 PL。`0xA0000000` 在这个窗口之外。**

### 2.2 这解释了此前**全部**挂死

之前 Phase 2B-Lite / 2B-HW / PL_DIAG ×2 / 2C 的所有尝试都用 `0xA0000000`。
该地址**根本没有被 PS 路由到 PL**，因此没有任何从设备会响应，ZynqMP 又未使能
AXI 超时 → CPU 永久等待 → 整机挂死，只能断电。

**这不是时钟问题、不是复位问题、不是 AXI 协议问题、不是 dtbo 问题 —— 是地址窗口问题。**
（此前已逐项排除时钟门控 CLKACT=1、uio 映射正确、POR 复位、读串扰修复，本轮得到最终答案。）

### 2.3 附带发现：AFI 配置差异

| 项 | 基线（已验证） | 我们的 `p2b_fix.xsa` |
|---|---|---|
| `mask_write 0xFF419000` | **`0x00000300 0x00000200`**（AFI **128-bit**） | **完全没有这条写入** |
| `PSU__MAXIGP2__DATA_WIDTH` | **128** | 未配置 |
| PL0_REF_CTRL | DIVISOR0=0xb, **SRCSEL=0x2** | DIVISOR0=0xb, SRCSEL=0x0 |

任务书要求检查的 `0xFF419000 bits[9:8]` = `0x200 >> 8 = 0b10` = **128-bit AFI 模式**，已确认。

---

## 3. 最小集成实施（实际修改文件）

### 3.1 方法：克隆已验证基线，只加不重建

```
E:\KVI2C_1019A  (已验证基线工程)
   └─ 复制为 → E:\KVHAP_2D
```

**未重建 PS、未重写 AXI 驱动、未改时钟复位结构、未改 UIO 访问方式。**

### 3.2 修改内容（全部）

| 文件 | 修改 |
|---|---|
| `E:\KVHAP_2D\build_2d.tcl` | **新建**：集成脚本 |
| `E:\KVHAP_2D\rtl_haptic\*` | **新增**：复制 21 个冻结触觉 RTL + 1 个 BD 包装 |
| `E:\KVHAP_2D\rtl_haptic\kv260_haptic_bd_wrapper.v` | **新建**：BD 用包装，`CLK_FREQ_HZ=100_000_000`，I²C 焊盘留置内部 |

BD 内改动（仅 4 处）：
1. `smartconnect_0` 的 `NUM_MI` 3 → **4**
2. 新增 BD module cell `haptic_bd`（引用 `kv260_haptic_bd_wrapper`）
3. `M03_AXI` → `haptic_bd/s_axi`；`pl_clk0` → `aclk`；`diag_bus_resetn` → `aresetn`
4. 地址分配 **`0x9C000000 / 64K`**（窗口内、当前空闲）

**未修改任何冻结 RTL。未改动基线的 PS / AXI IIC / diag_gpio / mailbox。**

### 3.3 遇到并解决的两个集成问题

1. **BD 缓存了模块接口的 FREQ_HZ=90908180**，与 PS 的 96968727 冲突，`set_property` 无效。
   **解法**：换新模块名 `kv260_haptic_bd_wrapper` 强制 Vivado 重新解析接口定义。
2. **地址必须在 0x8000_0000–0x9FFF_FFFF 内** → 改用 `0x9C000000`。

---

## 4. 新产物

| 文件 | SHA256 |
|---|---|
| XSA `kv260_haptic_2d.xsa` | `47df051a5affe3405a70f71f61e8813c1cd6b095a5527dafcc1bbe27ec6c23f2` |
| `kv260_haptic_2d.bit.bin`（7,797,692 B） | `4044b31055fba9ff0ee1d6a97002ef1c0fd795c6261827ccfed40a4387010c37` |
| `kv260_haptic_2d.dtbo`（1,199 B） | `10bf791e98d43e5f4c22e7ea3ee37c03a93bdf82bf157e49609054c177a1dc8a` |

### 4.1 Vivado 实现结果
```
BD Validated: 是
Synthesis: 通过
Implementation: 通过
Timing: All user specified timing constraints are met
CLB LUTs:       8,541  (7.29%)
CLB Registers: 14,390  (6.14%)
Block RAM:          7  (4.86%)
DSP:                0
Bonded IOB:         2  (基线 I2C 焊盘 H12/B10)
```

### 4.2 DTBO 结构（沿用基线模式）
```
fragment@0 -> &fpga_full : firmware-name = "xilinx/kv260-haptic-2d/kv260_haptic_2d.bit.bin"
                           resets = <&zynqmp_reset 116 117 118 119>
fragment@1 -> /axi       : iic-probe@9d000000 / iic-diag@9e000000 /
                           perception@9f000000 / haptic@9c000000 (全部 generic-uio)
```

---

## 5. 上板结果

### 5.1 部署与加载
```
包 sha256 = f548736f3ef73727cf2e2081b3e15edab4843f25d07f70f154b95b39a163acfc  （两端一致）
sudo xmutil unloadapp                -> remove from slot 0 returns: 0 (Ok)
sudo xmutil loadapp kv260-haptic-2d  -> kv260-haptic-2d: Loaded with slot_handle 0
fpga_manager/fpga0/state             -> operating
```

### 5.2 UIO 设备（**四个全部就位**）
```
uio4: kv260-iic-probe       (0x9D000000)   基线保留
uio5: kv260-iic-diag        (0x9E000000)   基线保留
uio6: kv260-pspl-mailbox    (0x9F000000)   基线保留
uio7: kv260-haptic          (0x9C000000)   ← 我们的触觉设计
```

### 5.3 **Linux 用户态 AXI 真实访问结果**

**读取（全部真实数据，系统存活）**
| 偏移 | 值 | 含义 |
|---|---|---|
| 0x0000 | `0x00000000` | MB_CTRL |
| 0x0004 | `0x00000000` | MB_STATUS |
| 0x1028 | `0x00000000` | ST_INIT_DONE（未初始化，符合预期：无 PCA9685） |
| 0x102C | `0x00000000` | ST_ONLINE_MASK |
| **0x103C** | **`0x00000001`** | **ST_FAULT_LATCHED = 1（故障已锁存，fail-closed 生效）** |
| **0x1054** | **`0x0000005B`** | **ST_RECOVERY_STATUS = 0x5B（真实位域数据）** |
| **0x2000** | **`0x00000002`** | **OE_CTRL：oe_n=1 → 输出关断 ✅ PASS** |

**写入 + 读回**
```
write 0x0000 <- 0x00000001   (MB_CTRL ENABLE)
readback     -> 0x00000001   ENABLE_bit=1        ✅ PASS
```

**矩阵写入 + COMMIT**
```
write 0x0100 <- 0x00000003   (pending cell0 = level 3)
write 0x0000 <- 0x00000003   (ENABLE|COMMIT)
MB_STATUS -> 0x00000004   COMMIT_ACK=0   iter=19999   ❌ 未观察到
```

**最终 OE 安全**
```
OE_CTRL -> 0x00000002   oe_n=1  sw_req=0   ✅ PASS
```

**关键：`SYSTEM_ALIVE` —— 整个 AXI 过程系统未挂死。**

### 5.4 基线回归
`uio4`（iic-probe）对齐读 `0x00/0x04/0x08/0x0C` 全部返回 `0x00000000`，
基线 UIO 设备仍然可用，未被破坏。

---

## 6. 已通过 vs 未通过

### ✅ 已通过（本轮真实硬件）
| 项 | 证据 |
|---|---|
| Vivado 集成（BD/综合/实现/时序） | 全部 PASS |
| 固件加载 | `Loaded with slot_handle 0`，`operating` |
| **PS→PL AXI 读** | 7 个寄存器读到真实值，系统存活 |
| **PS→PL AXI 写 + 读回** | `MB_CTRL` 写 1 读回 1 |
| **OE 安全关断** | `oe_n=1`，`sw_req=0` |
| 基线平台未被破坏 | uio4/5/6 仍在 |

### ❌ 未通过 / 未验证
| 项 | 说明 |
|---|---|
| **COMMIT_ACK 未观察到** | `MB_STATUS=0x00000004`，bit1 始终 0（iter 19999 用尽）。需下轮定位：可能是 `matrix_buffer_axi` 的 COMMIT 需特定前置条件，或 `ST_FAULT=1` 状态下 COMMIT 被拒 |
| I²C 波形 | 未接示波器；且本轮 haptic 的 I²C 焊盘**刻意留置内部**（避免与基线 AXI IIC 抢焊盘） |
| PCA9685 实际响应 | 未连接（J2 引脚映射仍未实测确认） |
| 256 路矩阵 / 双缓冲 / Scheduler / 灰度 | 未验证 |
| J2 引脚实测 | 基线 XDC 用 H12/B10（**官方 BIST 佐证**：HDA11=H12、HDA15=B10，bank45 LVCMOS33），但**未做实物电平/VCCO 测量** |

---

## 7. 重要更正与经验

| 项 | 说明 |
|---|---|
| **地址窗口** | KV260 上 PL 可访问范围 = `0x8000_0000–0x9FFF_FFFF`。**0xA0000000 不可用** |
| **AFI** | 基线为 128-bit（`0xFF419000=0x200`），任务书提示的 bits[9:8] 已核对 |
| **PL0 实际频率** | 硬件实测 99,999,999 Hz（BD 模型值 96,968,727 Hz）；本轮 wrapper 参数设为 100 MHz |
| **BD 模块缓存** | 改 HDL 的 X_INTERFACE_INFO 后必须换模块名，否则缓存不更新 |
| **非对齐 MMIO** | ARM 上对 UIO 做非 4 字节对齐访问会 **SIGBUS**（已修正探测程序） |
| 此前 5 次挂死 | **全部由 0xA0000000 未路由导致**，已定位，非随机故障 |

---

## 8. 下一步（按顺序）

1. **定位 COMMIT_ACK 未置位**（当前唯一功能缺口）——检查 `ST_FAULT=1` 时 COMMIT 是否被门控
2. **把 haptic 的 I²C 焊盘接到 H12/B10**（与基线 AXI IIC 二选一，避免抢总线），用逻辑分析仪验证 400 kHz 波形
3. **实测 J2 引脚与 Bank45 VCCO**（H12/B10 有官方 BIST 佐证，但仍需实物确认）
4. 接入 PCA9685，验证初始化与 ACK
5. 256 路矩阵帧 + 双缓冲 + Scheduler + 灰度 ROM
6. 真实执行器测试

---

## 9. 交付物

| 文件 | 说明 |
|---|---|
| `phase2d/kv260_haptic_2d.xsa` | 集成后 XSA |
| `phase2d/kv260_haptic_2d.bit.bin` | 可加载位流 |
| `phase2d/kv260_haptic_2d.dtbo` | 设备树 overlay |
| `phase2d/build_2d.tcl` | 集成脚本（可复现） |
| `phase2d/kv260_haptic_bd_wrapper.v` | BD 包装模块 |
| `E:\KVHAP_2D\` | 完整 Vivado 工程 |
| `kv260_2d/evidence/` | 全部原始输出（net / load / axi / axi2） |
| `reports/PHASE2D_KV260_KNOWN_GOOD_BASELINE.md` | Phase 0 基线检索（**需更新：基线在工作区外**） |

---

## 10. 边界

- 未覆盖 Image A、未写入 QSPI
- 未修改冻结 RTL（21 个文件 SHA 未变）
- 未修改 AX7020 原工程
- 未修改基线 PS / AXI IIC / diag_gpio / mailbox
- 未连接 PCA9685 / 执行器 / V+
- 未改变供电、未改 STM32
- 未使用未经确认的 J2 引脚（haptic 的 I²C 焊盘留置内部，未引出）
