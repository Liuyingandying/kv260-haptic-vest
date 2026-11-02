# Phase 2F-XSA-REPRO + 首次上板阶段报告

**日期**：2026-11-02
**结论**：**XSA 复现失败（ECO 钩子阶段不兼容，精确错误已定位）；
但已改用已验证 ECO bitstream 打包 runtime app 上板成功，
SCL/SDA 空闲电平恢复为高，I²C master 已开始发起事务。距首次振动只差 PCA9685 接入。**

---

## 0. 阶段状态

| 阶段 | 状态 |
|---|---|
| ECO 钩入正常构建流程生成 XSA | ❌ **失败**（见 §1） |
| 已验证 ECO bitstream 保留 | ✅ 完整保留（只读） |
| 打包为 Kria runtime app | ✅ 成功 |
| 上板加载 | ✅ `Loaded with slot_handle 0` |
| **焊盘空闲电平** | ✅ **SCL/SDA 均恢复为 HIGH** |
| **I²C master 发起事务** | ✅ 有证据（采样中出现 SCL 拉低） |
| PCA9685 ACK / 初始化 | ⬜ 未接入，无法验证 |
| **首次真实振动** | ⬜ 待 PCA9685 接入 |

---

## 1. XSA 复现失败：精确错误与根因

### 1.1 执行方式（按要求未做手动 DCP ECO）
在独立副本 `E:\KVHAP_2F_XSA_REPRO` 中，把 ECO 钩子挂到
`STEPS.OPT_DESIGN.TCL.PRE`，跑完整 `synth → opt → place → route → DRC → timing → bitstream`。

钩子内容（单入口）：
```tcl
source .../i2c_pre_opt.tcl          # 基线时钟/调试配置，原样
source .../fix_pad_readback_eco.tcl # 焊盘回读边界修正
```

### 1.2 实际结果
```
PREFLIGHT_NUM_MI=4
PREFLIGHT_HAPTIC_CELL=/haptic_fix
PREFLIGHT_HAPTIC_RANGE=0x00010000
PREFLIGHT_PS_WIDTH=128
HOOK_SET=E:/KVHAP_2F_XSA_REPRO/integrated_preopt.tcl

SYNTH_PROGRESS=100%  STATUS=synth_design Complete!   ← 综合成功
SYNTH_OK

ERROR: [Coretcl 2-1656] could not connect net
       'u_ps_bd/kv260_ps_bd_i/haptic_fix/inst/scl_sense' to requested pins.
ERROR: [Common 17-39] 'connect_net' failed due to earlier errors.
ERROR: [Vivado 12-13638] Failed runs(s) : 'impl_1'
```

### 1.3 根因判定
`connect_net -hierarchical` 在 **`OPT_DESIGN.TCL.PRE`（尚未 opt/place 的网表）** 阶段，
无法把 BD 深层层次内的 `scl_sense` 连接到顶层 `scl_pad_sync_reg[0]/D`。
同一操作在**布局后检查点**中是成功的（已验证）。

**结论：该 ECO 只能在 opt/place 之后执行，不能放在 opt 之前。假设已被证伪，未盲目重试同一阶段。**

### 1.4 钩子脚本自身是健壮的（4 处断言全部就绪）
`fix_pad_readback_eco.tcl` 已升级为带完整断言与轨迹日志的版本：
1. IOBUF 必须恰好 2 个
2. 焊盘侧负载只允许 `FDRE/FDSE/FDCE`（遇到其他类型立即 error）
3. 必须恰好迁移 4 个采样点
4. 迁移后复查焊盘侧只能有 `INBUF`，否则 error

日志写入 `eco_hook_trace.txt`，可定位到底哪一步失败。

**下一阶段只需把钩子改挂 `PLACE_DESIGN.TCL.PRE`（opt 之后、place 之前），即可复现同一 ECO。**

---

## 2. 已验证产物（完整保留，未被覆盖）

目录：`E:\KVHAP_2F_ECO_KEEP_20261102\`

| 文件 | 大小 | SHA256 |
|---|---|---|
| `kv260_pad_eco.bit` | 7,797,833 B | `35957a6527d6cdf5adc90aa2d41a7ac60f6add29cc878afa1863c140f55c95db` |
| `corrected_routed.dcp` | 10,151,896 B | `59c03831f115a4bce9db8622664985783e408532e10f512994d73a49e0a0fa01` |
| `corrected_placed.dcp` | 8,327,963 B | — |
| `timing.rpt` / `drc.rpt` / `route_status.rpt` / `final_netlist_evidence.txt` | — | — |

该 bitstream **Route 0 失败、DRC 0 错误、WNS=1.397 / TNS=0 / WHS=0.010**，
IOBUF 四引脚连接已逐条核验（`.I=const0`、`.T=~drv`、`.O=sense`、`.IO=端口`，焊盘侧仅 INBUF）。

---

## 3. 转向：打包为 Kria runtime app 并上板

XSA 只影响 Vitis platform / 完整启动镜像；**runtime app 只需 `.bit.bin` + `.dtbo`**。
因此直接用已验证 bitstream 打包上板。

### 3.1 打包
```
kv260_haptic_eco.bit.bin   7,797,692 B   (bootgen -process_bitstream bin)
kv260_haptic_eco.dtbo      1,075 B
shell.json                 45 B          {"shell_type":"PL_FLAT","num_pl_slots":"1"}
pkg.tar                    7,802,880 B   sha256 b21fe5f931b65e0e8afbf3e82faa742de746a693676f9794a9647a224eb2ffc7
```

### 3.2 上板加载（真实成功，三重确认）
```
板端 sha256 = b21fe5f9...eb2ffc7   （与构建端一致）
sudo xmutil unloadapp                -> remove from slot 0 returns: 0 (Ok)
sudo xmutil loadapp kv260-haptic-eco -> kv260-haptic-eco: Loaded with slot_handle 0
fpga_manager/fpga0/state             -> operating
/proc/device-tree/fpga-region/firmware-name = xilinx/kv260-haptic-eco/kv260_haptic_eco.bit.bin

UIO:
  uio3 axi-pmon            0xFFA10000
  uio4 kv260-iic-diag      0x9E000000
  uio5 kv260-pspl-mailbox  0x9F000000
  uio6 kv260-haptic        0x9C000000
```

---

## 4. 焊盘电平：软件可见证据（关键成果）

`diag_status` 寄存器（`0x9E000000 + 0x08`）包含焊盘电平的同步副本，位域来自 RTL：

```
diag_status = {diag_ticks[21:0], guard_expired, guard_active,
               sda_pad_sync,   scl_pad_sync,
               payload_resetn, diag_reset_override, 1'b1,
               diag_bus_resetn, ps_reset_sync, pl_resetn0}
              bit31..10        bit9   bit8   bit7   bit6   bit5 ...
```

实测 6 次采样：

| # | raw | SCL(bit6) | SDA(bit7) | 说明 |
|---|---|---|---|---|
| 1 | `0xB25134EF` | **1** | **1** | 空闲高 |
| 2 | `0x3CAA7CEF` | **1** | **1** | 空闲高 |
| 3 | `0xC740CCAF` | **0** | **1** | **SCL 被拉低 = master 正在发事务** |
| 4 | `0x524820EF` | **1** | **1** | 空闲高 |
| 5 | `0xDC9520EF` | **1** | **1** | 空闲高 |
| 6 | `0x673208EF` | **1** | **1** | 空闲高 |

**对比此前实测的 0 V / 0.2 V：焊盘已从"被常驱动拉低"恢复为"释放 + 上拉拉高"。**
第 3 次采样的 SCL=0 不是故障，而是 **I²C master 周期性发起事务**的旁证
（初始化失败后会重试）。

### 4.1 其他寄存器
```
MB_CTRL          = 0x00000000
MB_STATUS        = 0x00000000
INIT_DONE        = 0x00000000     <- 无 PCA9685，符合预期
ONLINE_MASK      = 0x00000000
FAULT_LATCHED    = 0x00000001     <- 无器件应答，fail-closed 生效
RECOVERY_STATUS  = 0x00000044
I2C_ERR_CNT      = 0x00000000
I2C_TIMEOUT_CNT  = 0x00000000
OE_CTRL          = 0x00000002     <- oe_n=1，输出关断 ✅
```

---

## 5. 距首次振动还差什么

唯一缺口：**PCA9685 未接入**。当前状态是"总线已就绪、master 在发事务、无器件应答"。

### 需要的单一硬件动作
接入**一块** PCA9685（16 通道 PWM 模块）：

| PCA9685 | 接到 |
|---|---|
| SCL | KV260 **H12** |
| SDA | KV260 **B10** |
| GND | KV260 GND（共地必须） |
| VCC (逻辑电源) | 3.3 V |
| V+ (LED/负载电源) | **先不接**，等 ACK 验证通过后再接 |
| SCL/SDA 上拉 | 片内已有 ~25 kΩ；若通信不稳再并 4.7 kΩ 到 3.3 V |

**注意**：H12/B10 的物理位置目前依据官方 BIST 的 HDA11/HDA15，**J2 针号映射仍未实测确认**。
接线前请先确认你要接的排针确实连到这两个封装引脚。

### 接入后我会立即执行
1. 读 `INIT_DONE` / `ONLINE_MASK` / `FAULT_LATCHED`，确认 ACK 与初始化成功
2. 读回 PCA9685 `MODE1/MODE2/PRE_SCALE` 验证寄存器写入
3. 写 FULL_OFF，确认所有通道关断
4. 对单通道下发 PWM → 接执行器 → **首次振动**

---

## 6. 剩余风险

| # | 项 | 说明 |
|---|---|---|
| 1 | XSA 未生成 | 钩子需改挂 `PLACE_DESIGN.TCL.PRE` 重跑 |
| 2 | J2 ↔ H12/B10 映射 UNKNOWN | 接线前必须确认 |
| 3 | Bank45 VCCO 未实测 | 本轮证明不是布线问题，但仍是电平前提 |
| 4 | I²C 速度 ~379 kHz | 首次接入建议观察波形，必要时降速 |
| 5 | 片内上拉仅 ~25 kΩ | 400 kHz 上升沿可能偏慢 |
| 6 | 无逻辑分析仪波形 | 目前只有寄存器级证据 |

---

## 7. 边界

- ✅ 未修改冻结 RTL（0/21）
- ✅ 未修改 PS / AFI / PL0 时钟
- ✅ 未改变三个基线从设备与 haptic 地址（0x9C000000 / 64K）
- ✅ 已验证 ECO 产物完整保留，未被覆盖
- ✅ 未接 PCA9685、未接执行器、未改供电
- ⚠️ 已上板加载 runtime app（本次目的即上板验证焊盘电平）
