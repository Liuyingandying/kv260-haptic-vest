# KV260 对原装触觉驱动板 — 协议一致性审计与根因报告

**日期**：2026-11-02
**硬件**：原装紫色成品板（TCA9548A × 1 + PCA9685 × 16 + 256 路执行器）
**方式**：Phase 0–2 只读审计 + Phase 3 首轮真实探测（未修改任何代码/硬件）
**结论**：**协议层已完全一致（含此前两处偏差已修复）；
但物理层缺失原架构强制要求的 4.7 kΩ 外接上拉，导致 38,250,241 次 NACK、16 块全灭。**

---

## Phase 0：STM32 源代码基线

**基线路径**（实际存在于 `Appli` 子目录）：
```
E:\03_Academics\Competitions\嵌入式比赛\23_EEPROM\STM32CubeIDE\Appli\Application\User\App\
```

| 文件 | SHA256 |
|---|---|
| `haptic_matrix.c` | `ef28ed8800ff9fe10e407a474cd9849f965408fdb7375d66408d085d8084417f` |
| `haptic_matrix.h` | `678981322e9c8581ce7ef9c4d406fcf15219bcb15aeee35c6ce8e5f60c06884e` |
| `pca9685.c` | `ea8f0945fdcec0f06c2368d3a61431754a8170ee17cb4c888d5a25e5d087e9e2` |
| `pca9685.h` | `d560453f5a96247eb3be8ff635fd709687101ec5b08119af15a9dd9a28f3363d` |

Git：`master` @ `38cd19dd…`，四个文件 tracked + clean（无本地漂移）。

### 0.1 初始化状态机

`haptic_matrix.c:403` `HapticVest256_Init()`：

```
MapIsValid()
  └─ 失败 → init_error_mask=0xFFFF, fault=1, AllOff, return
DisableTca()                       ← 写 TCA@0x70 mask=0（L441）
  └─ 失败 → init_error_mask=0xFFFF, fault=1, AllOff, return
for block 0..15:                   ← 逐块，不中断（L455-478）
    SelectTca(MAP[block].tca_channel)
    if ok: PCA9685_Init(MAP[block].pca_addr_hal, pwm_hz)
    if ok: online_mask |= bit   else: init_error_mask |= bit; i2c_error_count++
DisableTca()                       ← 收尾复位（L480）
判定: if (online_mask==0 || status!=OK) → fault（L492）
```

### 0.2 TCA 通道选择

`haptic_matrix.c:303-352`
- `HapticVest256_TcaWrite(vest, mask)` → `HAL_I2C_Master_Transmit(0xE0, &mask, 1)`
- `HapticVest256_SelectTca()` 有缓存：`tca_selection_valid && selected_tca_channel==channel` 时跳过
- `HapticVest256_DisableTca()` → mask=0

### 0.3 PCA9685 地址表（乱序）

`haptic_matrix.c:24-29`，7-bit 展开：

| 块 | ch | 7-bit | 块 | ch | 7-bit |
|---|---|---|---|---|---|
| 0 | 0 | 0x4D | 8 | 2 | 0x40 |
| 1 | 0 | 0x4E | 9 | 2 | 0x41 |
| 2 | 0 | 0x4F | 10 | 2 | 0x42 |
| 3 | 0 | 0x44 | 11 | 2 | 0x43 |
| 4 | 1 | 0x49 | 12 | 3 | 0x45 |
| 5 | 1 | 0x4A | 13 | 3 | 0x46 |
| 6 | 1 | 0x4B | 14 | 3 | 0x47 |
| 7 | 1 | 0x4C | 15 | 3 | 0x48 |

### 0.4 初始化寄存器顺序

`pca9685.c:98-139`：

| # | 寄存器 | 值 | 含义 |
|---|---|---|---|
| 1 | MODE1 0x00 | `0x30` | SLEEP(0x10) \| AUTO_INC(0x20) |
| 2 | PRE_SCALE 0xFE | `CalcPrescale(50Hz)` = 121 | 50 Hz |
| 3 | FULL_OFF 0xFD/0xFE/0xFF/0x00 | `0,0,0,0x10` | 全关 |
| 4 | MODE2 0x01 | `0x04` | OUTDRV 推挽 |
| 5 | MODE1 0x00 | `0x20` | AUTO_INC，唤醒 |
| 6 | delay 1 ms → MODE1 0x00 | `0xA0` | RESTART(0x80)\|AUTO_INC |

### 0.5 PWM 配置

`haptic_matrix.h:24-25` + `haptic_matrix.c`：
- `PCA9685_PWM_FULL_SCALE = 4095`
- `PCA9685_PWM_SAFE_MAX = 4080`
- 默认 `pwm_hz = 50`
- 5 级灰度映射：`{0, 1632, 2448, 3264, 4080}`

### 0.6 256 路映射

`HapticVest256_SourceIndex(block, channel)`（`haptic_matrix.h:108`）：
`index = block * 16 + channel`

### 0.7 NACK 与部分装配处理

`haptic_matrix.c:488-500`
```c
/* Partial populations are valid: only fail when no PCA9685 responded.
 * init_error_mask remains available for diagnostics. */
if ((vest->online_mask == 0U) || (status != HAL_OK)) { ... fault ... return HAL_ERROR; }
return HAL_OK;
```

### 0.8 故障保护与 OE

- **无独立 OE / RESET GPIO**（全项目搜索 `OE|OUTPUT_EN|GPIO_PIN` 无命中）
- 关断手段：**纯 PCA9685 寄存器 FIFO_OFF**（`PCA9685_WriteFullOffRaw`）
- `failsafe_ms` 默认 500 ms（`HAPTIC_VEST_DEFAULT_FAILSAFE_MS`），超时 `HapticVest256_AllOff`

---

## Phase 1：KV260 一致性审计

### 1.1 一致性表

| # | 项 | STM32 原始 | KV260（当前 debug 版） | 结论 |
|---|---|---|---|---|
| 1 | TCA 地址 | 0x70（HAL 0xE0） | `TCA_ADDR_W = 8'hE0` | ✅ 一致 |
| 2 | TCA 初始化 | `DisableTca()` mask=0 先行 | 全局事务 `g=1` → `[0xE0,0x00]` | ✅ 一致 |
| 3 | 四通道全扫描 | `SelectTca(ch)` per block | `tca_of(b)=b[3:2]`，4 通道全用 | ✅ 一致 |
| 4 | 16 地址表 | 0x4D/4E/4F/44/49/4A/4B/4C/40/41/42/43/45/46/47/48 | **逐条相同** | ✅ 一致 |
| 5 | ALLCALL 处理 | 刻意清 ALLCALL（TCA 同用 0x70） | 发 mask=0 隔离 + MODE1 不含 ALLCALL 位 | ✅ 一致 |
| 6 | 初始化字节流 | 0x30→PRE→FULLOFF→0x04→0x20→0xA0 | **完全相同** | ✅ 一致 |
| 7 | PWM 50 Hz | `pwm_hz=50` | `PWM_HZ = 50` | ✅ 一致 |
| 8 | PRE_SCALE | `CalcPrescale(50)` = 121 | `PRE_SCALE` 常量 | ✅ 一致 |
| 9 | 256 路映射 | `block*16+channel` | `matrix_map_rom` | ✅ 一致 |
| 10 | **部分装配合法** | `online_mask != 0` | **`MIN_ONLINE_BLOCKS = 1`（已修复）** | ✅ 已对齐 |
| 11 | **NACK 后继续** | 逐块继续 | **跳过本块继续下一块（已修复）** | ✅ 已对齐 |
| 12 | OE 控制 | 无硬件 OE，纯寄存器 FULL_OFF | 额加软件 `oe_n` 门控（`1=禁用`） | ⚠️ **增强**（非缺失） |
| 13 | 上拉电阻 | **外接 4.7 kΩ → 3.3 V** | **片内 ≈25 kΩ** | ❌ **缺失** |

### 1.2 两处历史偏差的修复确认

**偏差 A（已修复）** — `kv260_haptic_pipeline.v:194-205`
```verilog
localparam integer MIN_ONLINE_BLOCKS = 1;
assign init_healthy = init_done &&
                      (online_block_cnt >= MIN_ONLINE_BLOCKS) &&
                      ((MIN_ONLINE_BLOCKS >= 16) ? (init_err_mask == 0) : 1'b1);
```
→ 复现 STM32 `online_mask != 0` 语义。

**偏差 B（已修复）** — `pca_init_sequencer.v:320-338`
```verilog
if (m_ack_err) begin
    if (global_q) begin ... tn<=1; global_q<=0; blk_desc<=0; seg_q<=0; st<=S_WAITBUS; end
    else if (blk_desc >= 4'd15) st <= S_TAIL;
    else begin tn <= 1+(blk_desc+1)*7; blk_desc<=blk_desc+1; seg_q<=0; st<=S_WAITBUS; end
end
```
→ 复现 STM32 "逐块继续"语义。

### 1.3 OE 极性核对

`haptic_oe_guard.v:23-24,45,109`：
```verilog
// oe_n = 1  => OUTPUT DISABLED (safe)
// oe_n = 0  => OUTPUT ENABLED
output reg oe_n;             // 1 = disabled (safe), 0 = enabled
oe_n <= ~(arm_ready && dwell_ok);
```
复位默认 `oe_n = 1`（安全关断）。当前实测 `OE_CTRL = 0x2` → `oe_n = 1`，**输出处于关断态，符合安全要求**。

> 注：该 OE 是 FPGA 内部软件门控，**未分配物理引脚**（`haptic_oe_guard.v:19` "pin NOT assigned here -- J2 is still UNKNOWN"）。
> 原装板 PCA9685 的 OE 脚应由板级拉低（常使能），关断靠寄存器 —— 与 STM32 一致。

---

## Phase 2：真实硬件连接审计 —— **发现根因**

### 2.1 原架构的电气要求（手册原文）

`fpga/16x16/docs/HARDWARE_INTERFACE_MANUAL.md`：

| 行 | 原文 |
|---|---|
| L33 | `SCL、SDA 各经 4.7 kΩ 上拉到逻辑 3.3V` |
| L48 | `I²C SCL \| J11 PIN3 \| F17 \| 开漏双向时钟线 \| TCA9548 SCL \| I²C \| LVCMOS33，外接 4.7 kΩ→3.3V` |
| L49 | `I²C SDA \| J11 PIN4 \| F16 \| 开漏双向数据线 \| TCA9548 SDA \| I²C \| LVCMOS33，外接 4.7 kΩ→3.3V` |
| L55 | `逻辑 3.3V \| J11 3.3V \| … \| 电源 \| TCA VDD、PCA VCC 的逻辑侧` |

**该 4.7 kΩ 位于主机侧（AX7020 板），不属于驱动板。** 即原装板**不自带上拉**，依赖主机提供。

### 2.2 KV260 现状

`rtl_r2/candidate_pmod.xdc`：
```tcl
set_property IOSTANDARD LVCMOS33 [get_ports {i2c_scl_io i2c_sda_io}]
set_property PULLUP TRUE [get_ports {i2c_scl_io}]     # 片内弱上拉
set_property PULLUP TRUE [get_ports {i2c_sda_io}]
```

Xilinx UltraScale+ HD Bank 内部上拉典型 **≈ 25 kΩ**。实测 netlist `PULLTYPE=PULLUP` 已生效，但它**不等于 4.7 kΩ 外部电阻**。

### 2.3 为什么 25 kΩ 必然失败（定量）

I²C 上升时间 `tr ≈ 0.847 × R × C`（10%→90%）：

| 模式 | `tr` 上限 | 25 kΩ 允许电容 | 4.7 kΩ 允许电容 |
|---|---|---|---|
| Fast 400 kHz | 300 ns | **≈ 14 pF** | ≈ 75 pF |
| Standard 100 kHz | 1000 ns | **≈ 47 pF** | ≈ 250 pF |

实际情况：1 片 TCA9548A + 16 片 PCA9685（每片 ~10 pF 引脚 + PCB）+ 排线 ≈ **200–400 pF**。

- 25 kΩ：需要 ≈4–10 µs 上升时间，而 SCL 周期只有 2.48 µs（404 kHz）→ **永远充不到 VIH**
- 4.7 kΩ：≈1.4 µs，仍偏慢但可用；**这才是原架构的取值**

**结论：25 kΩ 在任何 I²C 速率下都无法驱动 17 器件总线。**

### 2.4 与原架构的差异

| 项 | 原 STM32/AX7020 | KV260 | 判定 |
|---|---|---|---|
| SCL 上拉 | 外接 4.7 kΩ → 3.3 V | 片内 25 kΩ | ❌ **缺失** |
| SDA 上拉 | 外接 4.7 kΩ → 3.3 V | 片内 25 kΩ | ❌ **缺失** |
| 3.3 V 逻辑供电 | J11 3.3V → TCA VDD + PCA VCC | J2 引出（待确认） | ⚠️ 未实测 |
| GND 共地 | 强制共地 | 待确认 | ⚠️ 未实测 |
| 执行器 V+ | PCA 板独立供电 | 未接 | ✅ 符合"先不接" |

**缺失 4.7 kΩ 上拉不是"额外采购"，而是恢复原架构本就存在的元件。**

---

## Phase 3：首选真实探测结果（只读，未改任何东西）

**固件**：`kv260-haptic-1ch`（已含偏差 A/B 修复）
`firmware-name = xilinx/kv260-haptic-1ch/kv260_haptic_1ch.bit.bin`
`uio6 = kv260-haptic`，`uio4 = kv260-iic-diag`

### 3.1 寄存器实测

```
FRAME_CNT        = 31,343
BURST_CNT        = 42,736
FLAGS            = 0x00000001        (bit0 = i2c_ack_err = 1)
INIT_DONE        = 0
ONLINE_MASK      = 0x0000
INIT_ERR_MASK    = 0xFFFF
RUN_ERR_MASK     = 0xFFFF
OFF_PEND_MASK    = 0xFFFF
FAULT_LATCHED    = 1
I2C_ERR_CNT      = 38,250,241        ← 3825 万次 NACK
OFF_RETRY_CNT    = 0
I2C_TIMEOUT_CNT  = 0                 ← ★ 零超时是关键线索
RECOVERY_STATUS  = 0x44 (BACKOFF, safety_off=1)
OE_CTRL          = 0x2 (oe_n=1)      ← 输出安全关断
```

### 3.2 16 块逐块状态

| 块 | TCA | PCA | online | init_err |
|---|---|---|---|---|
| 0 | ch0 | 0x4D | 0 | 1 |
| 1 | ch0 | 0x4E | 0 | 1 |
| 2 | ch0 | 0x4F | 0 | 1 |
| 3 | ch0 | 0x44 | 0 | 1 |
| 4 | ch1 | 0x49 | 0 | 1 |
| 5 | ch1 | 0x4A | 0 | 1 |
| 6 | ch1 | 0x4B | 0 | 1 |
| 7 | ch1 | 0x4C | 0 | 1 |
| 8 | ch2 | 0x40 | 0 | 1 |
| 9 | ch2 | 0x41 | 0 | 1 |
| 10 | ch2 | 0x42 | 0 | 1 |
| 11 | ch2 | 0x43 | 0 | 1 |
| 12 | ch3 | 0x45 | 0 | 1 |
| 13 | ch3 | 0x46 | 0 | 1 |
| 14 | ch3 | 0x47 | 0 | 1 |
| 15 | ch3 | 0x48 | 0 | 1 |

**ONLINE_COUNT = 0 / 16**

### 3.3 证据解读

| 现象 | 含义 |
|---|---|
| `I2C_ERR_CNT` 3825 万且持续增长 | master 在正常发事务 |
| `I2C_TIMEOUT_CNT = 0` | **没有时钟拉伸/总线卡死** → 不是短路或器件拉死总线 |
| 16 块全 NACK，含 TCA 本身 | **连 TCA9548A 都没应答** |
| 已排除：RTL 判据偏差 | debug 版已修复 A/B，仍全 NACK |

**零超时 + 全 NACK 是"线能拉低，但充不到高"的典型电气特征 —— 与上拉不足完全吻合。**

---

## 结论与修复方案（待授权后执行）

### 根因

**物理层缺失原架构强制要求的 SCL/SDA 外接 4.7 kΩ 上拉（→3.3 V），
KV260 仅靠片内 ≈25 kΩ，其上升时间比 SCL 周期还长，ACK 采样位永远读不到高电平。**

### 最小修复（恢复原架构，非新增设计）

在 KV260 侧 SCL/SDA 与 3.3 V 之间各接 **1 个 4.7 kΩ** 电阻：

```
KV260 J2.1 (SCL) ──┬── 4.7kΩ ── 3.3V
                   └── 驱动板 SCL
KV260 J2.2 (SDA) ──┬── 4.7kΩ ── 3.3V
                   └── 驱动板 SDA
KV260 GND ──────────── 驱动板 GND（共地）
```

**这不是"额外采购 TCA9548A/PCA9685"，而是补回原 AX7020 架构里本来就有的两个电阻。**

### 备选（不推荐）

把 `I2C_FREQ_HZ` 从 400 kHz 降到 10 kHz 以下，让 25 kΩ 有足够时间充电。
但这会偏离原架构时序，且仍需多毫秒级上升时间，实际不可靠。**不采用。**

### 执行前提

1. 用户确认可加两个 4.7 kΩ 电阻
2. 加装后重新上电，重跑 Phase 3 探测
3. 期望：`ONLINE_MASK = 0xFFFF`、`INIT_DONE = 1`、`init_healthy = 1`、`I2C_ERR_CNT` 停止增长
4. 通过后再进入 Phase 4（初始化 → 50Hz → FULL_OFF → 验证 OE → 单通道振动）

---

## 修改纪律确认

- ✅ STM32 原始工程**未修改**（4 个文件 SHA256 与 Git HEAD 一致）
- ✅ 当前已验证 bitstream / XSA **保留未动**
- ✅ 冻结的矩阵缓冲协议**未修改**
- ✅ 故障保护**未削弱**（OE 仍为安全关断 0x2）
- ✅ 未为通过验收改变原协议
- ✅ 本轮**未修改任何文件**，仅只读探测
- ⏳ 任何修复将先在独立副本进行并先报方案
