# PCA9685 × 16 器件模型报告

**日期**：2026-11-02  **文件**：`sim/i2c_devices.sv` → `pca9685_model`

---

## 1. 地址表（与 STM32 原始逐条一致）

| block | TCA ch | 7-bit | 来源 |
|---|---|---|---|
| 0 | 0 | **0x4D** | `pca_init_sequencer.v:91` / `haptic_matrix.c:24` |
| 1 | 0 | 0x4E | 同上 |
| 2 | 0 | 0x4F | 同上 |
| 3 | 0 | 0x44 | 同上 |
| 4 | 1 | 0x49 | `:93` |
| 5 | 1 | 0x4A | 同上 |
| 6 | 1 | 0x4B | 同上 |
| 7 | 1 | 0x4C | 同上 |
| 8 | 2 | **0x40** | `:95` |
| 9 | 2 | 0x41 | 同上 |
| 10 | 2 | 0x42 | 同上 |
| 11 | 2 | 0x43 | 同上 |
| 12 | 3 | 0x45 | `:97` |
| 13 | 3 | 0x46 | 同上 |
| 14 | 3 | 0x47 | 同上 |
| 15 | 3 | 0x48 | `:98` |

> **注意乱序**：地址并非顺序排列，STM32 与 KV260 RTL 都不允许顺序遍历。

## 2. 寄存器模型

- 完整 256 字节寄存器文件，每片独立
- 上电默认值（按数据手册）：`MODE1=0x11`（SLEEP|ALLCALL）、`PRE_SCALE=0x1E`
- 写指针自动递增（对应 `MODE1.AI`）
- `FULL_OFF` 判据：`regs[0xFF][4]`（ALL_LED_OFF_H bit4）

## 3. 初始化序列（从 RTL `descriptor_byte()` 提取，未编造）

| 段 | 字节 | 含义 | STM32 对应 |
|---|---|---|---|
| s=1 | `0x30` | MODE1 = SLEEP(0x10) \| AUTO_INC(0x20) | `PCA9685_MODE1_SLEEP\|AUTO_INC` |
| s=2 | `0x79`（121） | PRE_SCALE = 25MHz/(50×4096)−1 | `CalcPrescale(50)` |
| s=3 | `0xFD/0xFE/0xFF/0x00` → `0,0,0,0x10` | ALL_LED_ON_L/H、ALL_LED_OFF_L/H → FULL_OFF | `WriteFullOffRaw` |
| s=4 | `0x04` | MODE2 = OUTDRV | `PCA9685_MODE2_OUTDRV` |
| s=5 | `0x20` | MODE1 = AUTO_INC（唤醒） | `AUTO_INC` |
| s=6 | `0xA0` | MODE1 = RESTART(0x80)\|AUTO_INC | `RESTART\|AUTO_INC` |

**与 STM32 `pca9685.c:98-139` 逐项一致。**

## 4. 每片独立性与隔离

```verilog
localparam integer PRES = (PRESENT_MASK >> g) & 1;
pca9685_model #(.ADDR(A), .PRESENT(PRES)) u_pca (
    .ch_enable(ch_mask[CH]), ... );
```

- 各自独立 `regs[0:255]`、独立 `wr_cnt`、独立 ACK 逻辑
- 仅当 `PRESENT && ch_enable` 时才响应（`active`）

## 5. 仿真结果

| 场景 | 在线数 | 说明 |
|---|---|---|
| 全在线 | **16** | `ONLINE_MASK=0xFFFF`，`I2C_ERROR_COUNT=0` |
| 仅 block0 | 1 | `0x0001` |
| block0–7 | 8 | `0x00FF` |
| CH2（block8–11）全缺 | 12 | `0xF0FF` —— 其他通道不受影响 |
| block0 NACK | 15 | `0xFFFE` —— 继续扫描成功 |
| 全部缺 | 0 | `0x0000` |

## 6. 模型局限

- 未建模 ACK 建立/保持时间窗口
- 未建模 PCA9685 的振荡器启动时间（真实器件从 SLEEP 唤醒后需 ~500 µs）
- 未建模 PWM 输出的物理波形（只验证寄存器层面）
