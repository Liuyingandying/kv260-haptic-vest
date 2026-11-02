# KV260 Haptic Phase 5 — I2C 动态波形验证报告

**日期**：2026-11-02
**方式**：全程只读（**未写入任何寄存器**）
**结论**：**无需任何软件触发 —— I²C master 此刻正以约 1400 事务/秒 持续输出真实 I²C 帧。
START/地址/NACK/STOP 全部在 J2.1/J2.2 上实时发生，可立即用逻辑分析仪捕获。**

---

## Phase A：只读检查

### A.1 寄存器状态（修正后的正确偏移，基址 0x1000）

> **重要修正**：本阶段第一轮我误用了 0x0000 基址（读到全 0），
> 实际状态寄存器基址是 **0x1000**。以下为修正后的实测值。

| 寄存器 | 偏移 | 实测值 | 判读 |
|---|---|---|---|
| FRAME_CNT | 0x1000 | `0x00005A53` = 23,123 | 帧计数在增长 |
| BURST_CNT | 0x1004 | `0x0000A530` = 42,288 | **burst 在增长** |
| FLAGS | 0x1008 | `0x00000001` | **bit0 = i2c_ack_err = 1** |
| INIT_DONE | 0x1028 | `0x00000000` | 初始化未完成 |
| ONLINE_MASK | 0x102C | `0x00000000` | 0/16 在线 |
| INIT_ERR_MASK | 0x1030 | `0x0000FFFF` | **16 块全部初始化失败** |
| RUN_ERR_MASK | 0x1034 | `0x0000FFFF` | 16 块全部运行错误 |
| OFF_PEND_MASK | 0x1038 | `0x0000FFFF` | 16 块全部待关闭 |
| FAULT_LATCHED | 0x103C | `0x00000001` | 故障已锁存 |
| **I2C_ERR_CNT** | 0x1040 | **`0x0005A531` = 370,993 → 416,992（增长中）** | **I²C 事务在持续失败** |
| OFF_RETRY_CNT | 0x1044 | `0` | — |
| ACTIVE_GEN | 0x1048 | `0` | — |
| PENDING_GEN | 0x104C | `0` | — |
| I2C_TIMEOUT_CNT | 0x1050 | `0` | **无超时**（NACK 是正常应答缺失，不是总线卡死） |
| RECOVERY_STATUS | 0x1054 | `0x44` / `0x5B`（交替） | 见 A.3 |
| OE_CTRL | 0x2000 | `0x00000002` | oe_n=1，输出关断（未改动） |

### A.2 FLAGS 位定义（来自 `axi_status_regs.v` L184-187）

```verilog
wire [15:0] flags_val = {10'h000, block_ready_i, i2c_busy_i,
                         bld_busy_i, buf_busy_i, sched_busy_i,
                         i2c_ack_err_i};
// bit0=ack, bit1=sched, bit2=buf, bit3=bld, bit4=i2c, bit5=ready
```

`FLAGS = 0x1` → **bit0 = i2c_ack_err = 1**，与持续 NACK 完全吻合。

### A.3 I2C/RECOVERY FSM 当前状态

`fault_recovery_ctrl.v` 状态机共 7 态：`WAIT_INIT / RUN / QUIESCE / OFF_SCAN / BACKOFF / HOLD / REINIT`。

`RECOVERY_STATUS = 0x44`（观测到与 `0x5B` 交替）：
```
RECOVERY_STATUS = {25'd0, 1'b1, init_restart, safety_off, runtime_run, state[2:0]}
bit6 = 1  (常量)
bit5 = init_restart = 0
bit4 = safety_off   = 1
bit3 = runtime_run  = 0
bit2..0 = 0b100 = 4 = BACKOFF
```

**判定：FSM 在 `QUIESCE → OFF_SCAN → BACKOFF → QUIESCE` 之间循环。**

原因（`fault_recovery_ctrl.v` 原文）：
```verilog
OFF_SCAN: begin
    if(runtime_busy) scan_started<=1;
    if(scan_started && pipeline_idle) begin
        if(off_pending==0) state<=HOLD;
        else begin delay_count<=RETRY_CYCLES; state<=BACKOFF; end
    end
end
```
`off_pending = 0xFFFF ≠ 0` → 每轮都会尝试发送 FULL_OFF → 全部 NACK → 退避 → 重来。

### A.4 唯一的「初始化重启」路径 —— 被本轮约束禁止

```verilog
HOLD: if(fault_clear && off_pending==0 && pipeline_idle) begin
    init_restart<=1;  ...  state<=REINIT;    // 唯一入口
end
```

**`init_restart` 必须 `fault_clear`。本轮明令禁止清除 FAULT_LATCHED，因此不能走这条路。**

---

## Phase B：安全性审核

| 约束 | 执行情况 |
|---|---|
| 不允许猜测 MMIO 地址 | ✅ 所有偏移均从 `axi_status_regs.v` / `kv260_haptic_pipeline.v` 源码核对 |
| 不允许清除 FAULT_LATCHED | ✅ 未写任何寄存器 |
| 不允许改变 OE_CTRL | ✅ `OE_CTRL` 读回仍为 `0x2`（与加载后一致） |
| 不允许启用 PWM | ✅ 未写 |
| 不允许提交矩阵帧 | ✅ 未写 |
| 不允许修改 RTL/XDC/BD | ✅ 未修改 |
| 不允许重建或重新加载 bitstream | ✅ 未执行 |

### B.1 可写寄存器范围（源码确认）

`matrix_buffer_axi.v`：
```verilog
assign w_is_pend   = (awaddr_r[9:8] == 2'b01);   // 0x100..0x1FF 矩阵数据区
assign w_is_reg    = (awaddr_r[9:3] == 7'h00);   // 0x000..0x007
assign w_is_ctrl0  = w_is_reg & ~awaddr_r[2];    // 仅 0x000 CTRL 可写
```
→ **软件唯一能写的只有 0x000 CTRL（EN/COMMIT）和 0x100–0x1FF 矩阵区。**
两者都触及「提交矩阵帧」红线，本轮禁止。

### B.2 结论：不写任何寄存器

---

## Phase C：单次事务 —— **不需要触发**

### C.1 关键发现

总线**此刻已经在跑**，不需要任何触发。证据：高速采样 + 计数器增长。

**高速采样**（20,000 次 × 3 轮，约 184 kHz 采样率）：

| 轮次 | SCL 拉低 | SDA 拉低 | 状态跳变 |
|---|---|---|---|
| 0 | 611 | 541 | 624 |
| 1 | 662 | 599 | 665 |
| 2 | 593 | 557 | 612 |

**计数器增长**（决定性证据）：

```
I2C_ERR_CNT: 409708 → 416992   (+7284 / 6.0 s ≈ 1400 /s)
BURST_CNT  : 23792  → 26352    (+2560 / 2.0 s = 1280 /s)
FRAME_CNT  : 26063  → 26223    (+160  / 2.0 s =   80 /s)
```

### C.2 判读

**I²C master 正以约 1400 事务/秒 持续向总线发送真实 I²C 帧，全部收到 NACK。**

这不是故障，是设计的 fail-closed + 重试行为：
- 16 个 PCA9685 全部不在线（`INIT_ERR_MASK=0xFFFF`）
- 16 块全部待关闭（`OFF_PEND_MASK=0xFFFF`）
- FSM 循环尝试发送 FULL_OFF → NACK → 退避 → 重试

### C.3 因此

**Phase C 的「单次受控事务」在本轮约束下不可实现，但也不需要实现 —— 总线已有连续事务供波形验证。**

未执行任何写操作。

---

## Phase D：波形验收指引（需你用逻辑分析仪）

### D.1 设计标称参数（源码核对）

```verilog
parameter integer CLK_FREQ_HZ = 96_968_727,   // KV260 PL0
parameter integer I2C_FREQ_HZ = 400_000,
```

`i2c_master.v`：
```verilog
localparam integer TICKS = CLK_FREQ_HZ / (4 * I2C_FREQ_HZ);   // = 60
```

| 项 | 计算值 |
|---|---|
| TICKS | 60 |
| **SCL 实际频率** | **404 kHz**（96,968,727 / (4×60)） |
| SCL 周期 | **2.48 µs** |

### D.2 探测点

| 探头 | 接 |
|---|---|
| CH0 | **J2.1**（SCL） |
| CH1 | **J2.2**（SDA） |
| GND | **J2.9 或 J2.10** |

### D.3 采样设置

| 参数 | 建议值 |
|---|---|
| 采样率 | **≥ 10 MS/s**（404 kHz 需 ≥2 MS/s，10 MS/s 可看清细节） |
| 触发 | **SCL 下降沿**（或 SDA 在 SCL 高时下降 = START） |
| 窗口 | 500 µs – 2 ms（足够容纳多个事务） |
| 阈值 | 1.65 V（LVCMOS33） |

### D.4 预期波形（逐项对照）

| # | 验收点 | 预期 | 来源 |
|---|---|---|---|
| 1 | **START 出现** | SCL 高电平时 SDA 由高→低 | `i2c_master` M_START |
| 2 | **SCL 频率** | ~404 kHz（周期 2.48 µs），占空比 50% | TICKS=60 |
| 3 | **SDA 出现地址变化** | 首字节 **`0xE0`**（TCA9548A 地址 0x70 + W） | `i2c_transaction_builder.v` L63 `byte_at = 8'hE0`；`pca_init_sequencer.v` L81 `TCA_ADDR_W = 8'hE0` |
| 4 | **STOP 正常** | SCL 高电平时 SDA 由低→高 | `i2c_master` M_STOP |
| 5 | **预期 NACK** | 第 9 个 SCL 时钟线上 SDA 保持**高**（从设备不拉低） | 无 TCA9548A / PCA9685 |

### D.5 完整预期帧

```
START → 0xE0 (1 1 1 0 0 0 0 0) → NACK(高) → STOP     [TCA9548A 不存在，第 1 字节即 NACK]
```
若 TCA9548A 不存在，事务在地址字节的 ACK 位就终止，不会继续到 PCA 地址。

### D.6 预期事务间隔

实测 ~1400 事务/秒 → 平均间隔 ~714 µs。
单个事务耗时约 10 个 SCL 周期 ≈ 25 µs，其余为 FSM 退避/gap 时间。

**所以 1 ms 的捕获窗口内应能看到 1–2 个完整事务。**

---

## 验收判定表

| 结果 | 意义 | 下一步 |
|---|---|---|
| START + 0xE0 + NACK + STOP，SCL≈404kHz | **输出路径 PASS** —— FPGA 确实从 J2.1/J2.2 输出真实 I²C | 接 TCA9548A 或直接接 PCA9685 |
| 有 START，但 SCL 频率明显偏离 404kHz | 时钟参数问题 | 核对 PL0 实际频率 |
| 有波形但无 START/STOP 结构 | 时序或 drv 逻辑问题 | 查 `i2c_master` FSM |
| J2.1/J2.2 无任何波形 | **输出路径仍断** | 回到 Bank45 VCCO / 焊接 / R276 |

---

## 边界

- ✅ **未写入任何寄存器**（全程 mmap `PROT_READ`）
- ✅ 未清除 FAULT_LATCHED
- ✅ 未改变 OE_CTRL（读回仍 0x2）
- ✅ 未启用 PWM、未提交矩阵帧
- ✅ 未修改 RTL/XDC/BD
- ✅ 未重建或重新加载 bitstream
- ✅ 未连接 PCA9685、未接执行器

---

## 附：本轮一个重要修正

第一轮我用 0x0000 基址读状态寄存器，得到全 0，**若据此下结论就会误判为「计数器未动、无 I²C 活动」**。
实际基址是 0x1000，修正后立刻看到 `I2C_ERR_CNT` 已累计 37 万次并在持续增长。

**教训：寄存器偏移必须逐个对照 RTL 源码，不能凭记忆假设。**
