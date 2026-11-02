# Phase 4C — 单通道端到端控制链验证报告

**日期**：2026-11-02
**结论**：**❌ 未通过（按本阶段自定标准）。**
初始化链路与 OE 安全属性验证通过，PCA9685 收到正确的 MODE1/MODE2/PRE_SCALE(50Hz)；
但提交的触觉强度**没有转成 PWM duty 写入**（`LED0_OFF_H` 停留在 FULL_OFF=0x10），
因此**真实 RTL 链路尚未产生正确的 PWM 波形**。

---

## 0. 三层验收

| 层级 | 状态 |
|---|---|
| **仿真** | ⚠️ **部分通过**：链路跑通、初始化 16/16、OE 安全；**PWM 输出未验证通过** |
| **综合** | ❌ **未执行**（本阶段未到该步） |
| **真实硬件** | ❌ **未验证**（本阶段禁止上板） |

---

## 1. 已通过的部分

测试平台实例化**真实生产 RTL 顶层** `kv260_haptic_top`（含完整 pipeline +
`haptic_oe_guard` + AXI 地址译码），外部接 Phase 4B 已验证的 TCA9548A + 16×PCA9685 模型。

### 仿真输出

```
INIT_HEALTHY   = 1
ONLINE_MASK    = 0xFFFF      ← 16/16 全部在线
FAULT_LATCHED  = 0
FORCE_OFF      = 0
OE_N           = 1           ← OE 安全关断（1=disabled）
OE_ARMED       = 0
OE_CTRL        = 0x00000002
BURST_CNT      = 14
TCA_LAST_MASK  = 0x08        ← 最后选通 CH3

BLK8_MODE1     = 0x20        ← AUTO_INC（已唤醒）
BLK8_MODE2     = 0x04        ← OUTDRV 推挽
BLK8_PRE_SCALE = 0x79 = 121  ← 25MHz/(50×4096)-1 → PWM 50 Hz ✅
BLK8_LED0_ON_L = 0x00
BLK8_LED0_ON_H = 0x00
BLK8_LED0_OFFL = 0x00
BLK8_LED0_OFFH = 0x10        ← FULL_OFF 位=1（全断，非 PWM）
OE_N_AFTER     = 1           ← 提交后 OE 仍关断 ✅
```

### 逐项判定

| # | 验收项 | 结果 | 证据 |
|---|---|---|---|
| 1 | 系统复位 | ✅ | `pl_resetn0` 拉低 100 clk |
| 2 | 16 片 PCA 初始化 | ✅ | `ONLINE_MASK=0xFFFF` |
| 3 | ONLINE_MASK=0xFFFF | ✅ | 同上 |
| 4 | INIT_DONE=1 | ✅ | `INIT_HEALTHY=1` |
| 5 | OE 安全关断 | ✅ | `oe_n=1`、`oe_armed=0`、`OE_CTRL=0x2` |
| 6 | AXI 写入非零强度 | ✅ 写入完成 | 64 字写 0x100–0x1FC |
| 7 | Commit | ✅ | `MB_STATUS_POST=0x0E`（bit1 commit_ack=1） |
| 8 | ACK | ✅ | `FAULT_LATCHED=0`、`I2C` 无 NACK 计数增长 |
| 9 | Scheduler 调度 | ⚠️ **部分** | `BURST_CNT=14`，但 `FRAME_CNT=0` → **帧未完成** |
| 10 | TCA 选中 CH2 | ⚠️ 最后是 CH3 | `TCA_LAST_MASK=0x08`；CH2 曾被选中但非最后一次 |
| 11 | PCA 0x40 收到写入 | ✅ | `MODE1/MODE2/PRE_SCALE` 均正确 |
| 12 | PWM 频率 50 Hz | ✅（寄存器层） | `PRE_SCALE=121` |
| 13 | **占空比与 Duty ROM 一致** | ❌ **未达成** | `LED0_OFF_H=0x10` 是 FULL_OFF，**不是** `0xFF0`/`0x990` 等 duty 值 |
| 14 | 其他 255 通道未被误改 | ⚠️ 未逐通道核验 | — |
| 15 | 输出关闭指令有效 | ✅ | 全部通道 FULL_OFF |

---

## 2. 失败点：强度未转成 PWM

### 现象
- 写入矩阵字节 128（block 8 / ch 0）为 `4`（第一次）或 `255`（第二次灰度假设）
- 期望：`LED0_OFF_H:L = 0xFF0`（level ≥4）
- 实际：`LED0_OFF_H = 0x10`（FULL_OFF 位），`LED0_OFF_L = 0x00`

### 已排除
| 假设 | 状态 |
|---|---|
| 初始化失败 | ❌ 排除（16/16 在线） |
| OE 阻断了写入 | ❌ 排除（OE 门控只影响 `oe_n`，不阻断 I2C 写寄存器；且 PCA 收到了 MODE1/MODE2/PRE_SCALE） |
| TCA/地址错 | ❌ 排除（block 8 = PCA 0x40 收到了写入） |
| 灰度 vs 等级语义 | ❌ 排除（4 和 255 结果相同） |

### 仍待定位的方向（未验证）
1. **矩阵写入未真正落到 buffer**：`mb_wr_en = w_exec & w_is_pend & wstrb_r[wcnt] & !cmt_pend_lock` —— 可能存在 `cmt_pend_lock` 或时序问题，写被吞掉
2. **CTRL 写入方式**：本 TB 先写 `0x0000=1`（仅 EN）再写 `0x0000=3`（EN|COMMIT）；若 0x0000 的位定义与假设不同，COMMIT 可能未生效
3. **Dirty Detector 路径**：首帧全脏 → 全刷新；但 `FRAME_CNT=0` 说明帧未最终完成，可能调度器仍在运行中（采样窗口 4M clk ≈ 41 ms 可能不足）
4. **`scan_rd_data` → `duty_level_bits` 的真实映射**：`tactile_v12_ctrl` 的 `level_data/duty_level` 内部逻辑未逐行核对
5. **`BURST_CNT=14` 而非 16**：可能有 2 个块尚未被处理，说明仿真窗口不足或流控阻塞

---

## 3. 本轮未完成项（如实声明）

| 项 | 状态 |
|---|---|
| 单通道正确 PWM 波形 | ❌ **未达成** |
| 其他 255 通道未被误改的逐通道核验 | ❌ 未做 |
| 故障注入（8 类） | ❌ 未做 |
| 离线综合检查 | ❌ 未做 |
| 仿真波形文件（.wdb） | ❌ 未保存 |

**本阶段按自定标准"只有真实 RTL 完整链路产生正确 PWM 波形后才算通过"——不通过。**

---

## 4. 建议的下一步（最小定位动作）

1. **先确认矩阵写入是否落地**：在 TB 中直接层次化读取 `dut.u_pipeline.u_mb`（matrix_buffer）内部存储，或读回 AXI 0x100+ 窗口，确认字节 128 是否为所写值
2. **确认 CTRL 位定义**：读 `matrix_buffer_axi.v` 中 `w_is_ctrl0` 的位分解，核对我用的 `bit0=EN, bit1=COMMIT` 是否正确
3. **延长仿真窗口**：把采样从 4M clk 提到 20M clk，确认 `FRAME_CNT` 是否会变为 1
4. **核对 `tactile_v12_ctrl` 的强度语义**：确认输入 8-bit 到 3-bit level 的映射规则（是否按阈值分级）
5. 以上定位完成后再重跑，不要盲目改 RTL

---

## 5. 未修改任何 RTL

本阶段**只新增测试平台**（`tb_e2e.sv`、`e2e_wrap.sv`），
未修改任何生产 RTL、未修改 Phase 4A/4B 产物、未触碰 STM32 原始工程。
