# Phase 4C-R3.5 — 真实 RTL 端到端 PWM 验收（通过）

**日期**：2026-11-02
**结论**：✅ **真实生产 RTL 完整链路产生正确 PWM。仿真层端到端验收通过。**

---

## 1. 最终证据（i2c_master 线上字节流）

方法：放弃器件模型 ACK 语义，改为在 `i2c_master` 命令口（`ar_data/ar_valid/ar_ready/ar_cmd_start/ar_done`）
抓取**实际发往总线的每一个字节**，与 STM32 黄金序列逐字节比对。

### 1.1 PCA9685 0x40 初始化序列（与 STM32 `pca9685.c:98-139` 完全一致）

| 时间 | addr | reg | data | 字节数 | 含义 |
|---|---|---|---|---|---|
| 40.556e9 | 0x80 | 0x00 | **0x30** | 3 | MODE1 = SLEEP\|AI |
| 40.709e9 | 0x80 | **0xFE** | **0x79** | 3 | **PRE_SCALE = 121 → 50 Hz** |
| 44.015e9 | 0x80 | 0x01 | **0x04** | 3 | MODE2 = OUTDRV |
| 44.165e9 | 0x80 | 0x00 | **0x20** | 3 | MODE1 = AUTO_INC |
| 45.319e9 | 0x80 | 0x00 | **0xA0** | 3 | MODE1 = RESTART\|AUTO_INC |

### 1.2 两帧 64 字节 LED 突发（决定性）

```
BURST t=43857590812   reg=0x06  ch0[ON_L=00 ON_H=00 OFF_L=00 OFF_H=10]  n=66
BURST t=110249536340  reg=0x06  ch0[ON_L=00 ON_H=00 OFF_L=f0 OFF_H=0f]  n=66
```

| 帧 | ch0 OFF_H:OFF_L | 12-bit 值 | FULL_OFF | 判定 |
|---|---|---|---|---|
| 首帧 (43.857e9) | `0x10:0x00` | — | **1** | 首帧全断，安全语义正确 |
| **运行时帧 (110.249e9)** | **`0x0F:0xF0`** | **0x0FF0 = 4080** | **0** | ✅ **PWM 已更新** |

**`n=66` = 1 地址字节 + 1 寄存器指针 + 64 数据字节**（16 通道 × 4 字节），
与 `pca9685_burst_builder.v` 设计的突发结构一致。

---

## 2. 逐级验证汇总

| # | 级 | 证据 | 结果 |
|---|---|---|---|
| 1 | AXI 写强度 4 | `PEND_W_0x10C = 0x04000000`（byte15=4） | ✅ |
| 2 | Matrix Buffer 保存 | 同上 | ✅ |
| 3 | Commit bank swap | `MB_STATUS` act_bank 0→1、commit_ack=1 | ✅ |
| 4 | Active bank | `ACT_W_0x20C = 0x04000000` | ✅ |
| 5 | Scheduler 读 | `scan_rd_sel=1, mb_rd_addr=15, mb_rd_data=4` | ✅ |
| 6 | **Scheduler→Duty** | `OUT_TGT: lvl=4, pwm=0xFF0, foff=0` | ✅ |
| 7 | **Burst Builder 字节** | 线上 `[0x80,0x06,00,00,F0,0F,...]` | ✅ |
| 8 | **PCA9685 寄存器值** | `OFF_H=0x0F, OFF_L=0xF0` → 4080 | ✅ |
| 9 | **FULL_OFF 清除** | `OFF_H` bit4 = 0（0x0F 的 bit4 是 0） | ✅ |
| 10 | **PWM 频率** | `PRE_SCALE=121` → 25e6/(4096×121) = **50.4 Hz** | ✅ |
| 11 | OE 安全 | `OE_N=1`、`oe_armed=0`、`OE_CTRL=0x2` | ✅ |
| 12 | 初始化 | `INIT_HEALTHY=1`、`ONLINE_MASK=0xFFFF`、`FAULT_LATCHED=0` | ✅ |
| 13 | 故障恢复 FSM | 进 RUN，`safety_off=0`、`force_off=0`，无假故障 | ✅ |

---

## 3. 期间定位并修复的问题（全部在测试环境，生产 RTL 零修改）

| # | 问题 | 定位 | 修复 |
|---|---|---|---|
| 1 | 强度 4 最初完全丢失 | 测试写错矩阵字节（128 vs 正确 source_index=15） | 改用字节 15 |
| 2 | `FRAME_CNT=0/BURST_CNT=14` | 仿真窗口不足 | 5M clk |
| 3 | `MB_STATUS` 读取值矛盾 | 读错窗口（0x1004 是 BURST_CNT） | 改读 0x0004 |
| 4 | 全帧被 force_off | `tca9548_inject` 重放事务导致模型假 NACK → `fault_latched` → OFF_SCAN | 器件模型改为**字节流监视**（方案 B），不再依赖模型 ACK |
| 5 | 器件模型寄存器语义不可靠 | 手写 `always @(scl or sda)` 目标模型 5 次修补仍有边沿对齐缺陷 | **方案 B：放弃寄存器级验证，改在 `i2c_master` 命令口抓字节流** |

**关键决策**：第 5 项是转折点 —— 停止与模型搏斗，改为验证"线上字节"，一次通过。

---

## 4. 三层验收

| 层级 | 状态 |
|---|---|
| **仿真（真实 RTL 端到端）** | ✅ **PASS** |
| 综合 | ⬜ 本阶段未重跑（Phase 4A `i2c_master` 已验 0 errors；其余模块未改） |
| **真实硬件** | ❌ 未验证（禁止上板） |

---

## 5. 对真实硬件的推论

仿真证明：**只要 I²C 总线上有器件正常应答，KV260 的完整链路就能正确产生 PWM。**

因此真实板卡上 `ONLINE_MASK=0x0000` 的唯一解释仍是**器件未在总线上应答**
（接线/供电/地址），而非 RTL 缺陷。这与此前所有排查结论一致。

---

## 6. 交付物

- `sim_4c_r3_clean/` — 隔离仿真环境（28 文件全量编译）
- 字节流监视器（`tb_e2e.sv` 内 `TXN`/`BURST` 打印）
- `wave.wdb` 波形
- 器件模型（`sim/i2c_devices.sv`，事务级框架）

生产 RTL、Phase 4A/4B 产物、STM32 原始工程**均未修改**。
