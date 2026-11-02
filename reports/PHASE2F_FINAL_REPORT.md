# Phase 2F 报告

**日期**：2026-11-02  **结论**：**加载失败已定位并解决；内部上拉已在 Vivado 实现中确认生效并成功上板；
空闲电平/波形/ACK/PCA9685 需用户配合测量，本轮 NOT TESTED**

---

## 0. 最终验收表

| 验收项目 | 要求 | 状态 | 依据 |
|---|---|---|---|
| **Phase 2E 加载** | 真实成功 | ✅ **PASS** | `Loaded with slot_handle 0`、`firmware-name` 指向本设计、uio6 出现 |
| **内部上拉** | Vivado 实现证据 | ✅ **PASS** | 实现后 `PULLUP=1  PULLTYPE=PULLUP`（H12/B10） |
| SCL 空闲电平 | 实测 | ⬜ **NOT TESTED** | 需万用表（见 §5 唯一请求） |
| SDA 空闲电平 | 实测 | ⬜ **NOT TESTED** | 同上 |
| START/STOP | 逻辑分析仪证据 | ⬜ **NOT TESTED** | 需逻辑分析仪 |
| I²C ACK | 真实设备响应 | ⬜ **NOT TESTED** | 需 PCA9685 |
| PCA9685 初始化 | 寄存器读写验证 | ⬜ **NOT TESTED** | 需 PCA9685 |
| PWM 输出 | 实际波形验证 | ⬜ **NOT TESTED** | 需逻辑分析仪 + 执行器 |

---

## 1. Phase A：Load Error -1 根因与解决

### 1.1 只读诊断证据（修复前）
```
xmutil listapps:
  k26-starter-kits   XRT_FLAT   ...   0->0,     <- 仍占用 slot 0（有 handle）
  kv260-haptic-2e    PL_FLAT    ...   -1       <- 未加载

overlays/: k26-starter-kits_image_1   status = applied
/proc/device-tree/fpga-region/firmware-name = k26-starter-kits.bit.bin
fpga_manager = operating
uio0..3 = axi-pmon（原厂 shell 的 UIO）
```

### 1.2 根因
> **原厂 `k26-starter-kits` 仍占着 FPGA slot 0，且其 DT overlay 仍 applied。
> 在未执行 `xmutil unloadapp` 的情况下直接 `loadapp`，slot 被占用 → 返回 `Load Error: -1`。**

同时说明：**`fpga_manager` 的 `operating` 状态只表示"上一次加载成功且未卸载"，
不表示当前加载的是我们的设计** —— 与任务书的提醒一致，本次已用
`firmware-name` + UIO 名称双重确认。

### 1.3 解决（可逆操作）
```
sudo xmutil unloadapp                -> remove from slot 0 returns: 0 (Ok)
sudo xmutil loadapp kv260-haptic-2e  -> kv260-haptic-2e: Loaded with slot_handle 0
LOAD_EXIT=0
fpga_manager                         -> operating
overlays                             -> kv260-haptic-2e_image_1
/proc/device-tree/fpga-region/firmware-name = xilinx/kv260-haptic-2e/kv260_haptic_2e.bit.bin
uio4 kv260-iic-diag      0x9E000000
uio5 kv260-pspl-mailbox  0x9F000000
uio6 kv260-haptic        0x9C000000
```

### 1.4 部署完整性核对
| 文件 | 板端 SHA256 | 与本地构建产物 |
|---|---|---|
| `kv260_haptic_2e.bit.bin` | `1520a41d…c682a272` | ✅ 一致 |
| `kv260_haptic_2e.dtbo` | `ea5de4e0…92897df0` | ✅ 一致 |
| `shell.json` | `ed0f0900…20cddb4f` | ✅ 一致 |

### 1.5 应用列表（修复后确认无冲突）
`kv260-haptic-2e` 可被识别，`k26-starter-kits` 已释放为 `-1`。

---

## 2. Phase B：内部上拉实现

### 2.1 关键发现：Phase 2E 实际用的是**基线原始 XDC**

构建日志显示 Phase 2E 工程的活动约束文件是：
```
E:/01_Projects/FPGA_Embedded/KV260_Basic_Preboard/kv260_i2c_bringup_20261019T090440Z/candidate_pmod.xdc
```
**而不是** `E:/KVHAP_2E/rtl_pad/candidate_pmod.xdc`。
该基线文件**没有 PULLUP 约束** → **这就是 Phase 2E 上 SCL/SDA 悬空（0.2V）的直接原因之一。**

### 2.2 独立副本（原工程未动）
```
E:\KVHAP_2E_PULLUP_TEST   <- 本轮新建
   rtl_pullup\candidate_pmod.xdc   <- 带 PULLUP 的副本
   rtl_pullup\*.v                  <- 全部 RTL 副本
E:\KVHAP_2E                 <- 保留原样（已核验无 PULLUP，未修改）
E:\01_Projects\...         <- 基线，未修改
```

### 2.3 XDC 修改（仅追加两行）
```tcl
# Phase 2F: internal pull-ups (~25k) so the open-drain bus has a defined idle level
set_property PULLUP TRUE [get_ports {i2c_scl_io}]
set_property PULLUP TRUE [get_ports {i2c_sda_io}]
```

### 2.4 约束切换（关键）
脚本显式把活动的 `candidate_pmod.xdc` 从基线路径移除，替换为副本路径：
```
REMOVING_OLD_XDC=E:/01_Projects/FPGA_Embedded/KV260_Basic_Preboard/kv260_i2c_bringup_20261019T090440Z/candidate_pmod.xdc
ACTIVE_XDC=E:/KVHAP_2E_PULLUP_TEST/rtl_pullup/candidate_pmod.xdc
```

### 2.5 约束合规性（对照任务要求）

| 要求 | 状态 |
|---|---|
| 保留现有 Open-Drain RTL | ✅ 未改（`pipeline` L553-554 原样） |
| 禁止改推挽 | ✅ 仍是 `assign pin = drv ? 1'b0 : 1'bz` |
| 不改 H12/B10 映射 | ✅ PACKAGE_PIN 仍 H12 / B10 |
| 不改 AXI 地址 0x9C000000 | ✅ 未改 |
| 不重建 PS | ✅ 未动 BD 的 PS |
| 不改已验证其他 RTL | ✅ **冻结 RTL 改动 0/21** |
| 不覆盖 Phase 2E 工程 | ✅ 已核验 |

---

## 3. Phase C：构建与上板

### 3.1 Vivado 结果

| 项 | 值 |
|---|---|
| 综合 | ✅ 通过 |
| 实现 | ✅ 通过 |
| DRC | ✅ 通过 |
| 时序 | ✅ **All user specified timing constraints are met**（WNS 3.084 / WHS 0.010） |
| CLB LUTs / Registers | 8,512（7.27%）/ 14,372（6.14%） |
| Block RAM | 7 |
| Bonded IOB | 2 |

### 3.2 **内部上拉的实现证据（决定性的 Vivado 输出）**

对**已实现设计**（`open_run impl_1`）查询端口属性：
```
PORT i2c_scl_io : PULLUP=1  PULLTYPE=PULLUP  IOSTANDARD=LVCMOS33  PACKAGE_PIN=H12
PORT i2c_sda_io : PULLUP=1  PULLTYPE=PULLUP  IOSTANDARD=LVCMOS33  PACKAGE_PIN=B10
```

> **`PULLUP=1` 与 `PULLTYPE=PULLUP` 均已生效，且引脚与电平标准未变。**
> 说明：`report_io` 的表格不含 PULLUP 列，因此以 `get_property` 查询实现后设计为准。

### 3.3 产物
| 文件 | 说明 |
|---|---|
| `phase2f/kv260_haptic_2f.xsa` | sha256 **`8d04524663dac4ec7db95eeca9648f0c22a9a4a66498c108d9aa1d7ec8104be6`** |
| `phase2f/kv260_haptic_2f.bit.bin` | sha256 **`51349fdfc37960d2e80fe45c041f9b6ba103c85698296b0f5376c04af21f14e1`** |
| `phase2f/kv260_haptic_2f.dtbo` | 设备树 overlay |
| `phase2f/candidate_pmod_PULLUP.xdc` | 带 PULLUP 的约束文件 |

### 3.4 上板加载（真实成功，非仅看 operating）
```
包 sha256 (板端) = e3b051f879f1b2fe621ebf525eeac525e0bfe05fa5682d9216a031a2dc0b3da9
sudo xmutil unloadapp              -> remove from slot 0 returns: 0 (Ok)
sudo xmutil loadapp kv260-haptic-2f -> kv260-haptic-2f: Loaded with slot_handle 0
LOAD_EXIT=0
fpga_manager                       -> operating
/proc/device-tree/fpga-region/firmware-name = xilinx/kv260-haptic-2f/kv260_haptic_2f.bit.bin
uio4 kv260-iic-diag      0x9E000000
uio5 kv260-pspl-mailbox  0x9F000000
uio6 kv260-haptic        0x9C000000
```
**三重确认加载成功**：`slot_handle 0` + `firmware-name` 指向本设计 + `uio6` 出现。

### 3.5 上板功能自检（用户态 AXI 真实读写）
```
r 0x0000 MB_CTRL            = 0x00000000
r 0x0004 MB_STATUS          = 0x00000000
r 0x1028 INIT_DONE          = 0x00000000
r 0x102C ONLINE_MASK        = 0x00000000
r 0x103C FAULT_LATCHED      = 0x00000001
r 0x1054 RECOVERY_STATUS    = 0x0000005B
r 0x0040 I2C_ERR_CNT        = 0x00000000
r 0x0050 I2C_TIMEOUT_CNT    = 0x00000000
r 0x2000 OE_CTRL            = 0x00000002   oe_n=1 -> PASS(OUTPUT OFF)

w 0x0000 <- 0x1  ->  readback 0x00000001  EN=1 -> PASS
SYSTEM_ALIVE
```
**设计存活、AXI 读写正常、OE 保持关断、系统不挂死。**

---

## 4. 当前状态判断

| 项 | 状态 |
|---|---|
| PL 已加载 Phase 2F | ✅ 是（firmware-name 确认） |
| I²C 引脚已由 haptic master 独占 | ✅ 是（Phase 2E 已做，Phase 2F 沿用） |
| 内部上拉已生效 | ✅ 是（PULLTYPE=PULLUP） |
| I²C master 处于空闲 | ✅ 推断（INIT_DONE=0、FAULT=1、drv=0） |
| **SCL/SDA 实际电平** | ⬜ **需测量** |

**按此状态推断：SCL 与 SDA 在空闲时应被片内上拉（约 25 kΩ）拉高至接近 3.3 V
（前提是 Bank45 的 VCCO = 3.3 V）。**

---

## 5. 需要用户配合的唯一操作

> **请用万用表测量 KV260 的 H12（SCL）与 B10（SDA）对 GND 的直流电压，并同时测量
> 这两个引脚所在 Bank45 的 VCCO（3.3 V 参考点）电压。**

预期与判据：

| 测量结果 | 含义 | 下一步 |
|---|---|---|
| SCL/SDA ≈ 3.3 V（或 ≥ 2.5 V） | ✅ 上拉生效，总线空闲正常 | 进入逻辑分析仪波形验证 |
| SCL/SDA 仍 ≈ 0.2 V | 上拉未生效或 Bank45 VCCO ≠ 3.3 V | 先查 VCCO，再查是否被拉低 |
| VCCO ≠ 3.3 V | LVCMOS33 不成立 | 需改用匹配的电平标准，属设计级问题 |

**这是继续推进的唯一前置条件。测量结果给我后，我再决定下一步。**

---

## 6. 未验证项（明确声明）

- ❌ **I²C 空闲电平**：未实测
- ❌ **START / 地址 / ACK / STOP 波形**：未用逻辑分析仪验证
- ❌ **SCL 实际频率是否 ≈400 kHz**：未实测
- ❌ **PCA9685 地址 ACK / MODE1 / MODE2 / PRE_SCALE / FULL_OFF**：未连接器件
- ❌ **PWM 输出波形**：未验证
- ❌ **低速→400 kHz 的频率递进验证**：未开始

**不把"AXI 通了""上拉生效"等同于"I²C 通了"或"PCA9685 通了"。**

---

## 7. 边界

- 未修改冻结 RTL（0/21）
- 未覆盖 Phase 2E 工程、未修改基线工程
- 未使用 0xA0000000（全程 0x9C000000）
- 未做未确认地址的 MMIO
- 未将开漏改为推挽
- 未写入 QSPI、未修改启动镜像
- 未连接任何外设
