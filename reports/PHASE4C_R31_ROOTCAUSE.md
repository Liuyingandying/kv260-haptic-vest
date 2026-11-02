# Phase 4C-R3.1 — 根因定位（PWM 仍未产生）

**结论**：**根因已定位到 `fault_recovery_ctrl` FSM 停在 OFF_SCAN 导致 `safety_off=1`；
但两次修复尝试均未改变行为，PWM 波形仍未产生。未达验收。**

---

## 1. 确切根因（有周期级证据）

### 1.1 隔离环境已建立并验证

- 目录：`sim_4c_r3_clean`（全新，删除旧 `xsim.dir`）
- 编译：**28 个源文件一次性全量重编**，无旧缓存复用
- **补丁生效已证明**：`R3_PATCH_ACTIVE` 标识在日志中出现 **3 次**

### 1.2 四拍追踪（目标 block=8 / ch=0）

```
R3_PATCH_ACTIVE LOAD_MATRIX blk=8 ch=0 rd_addr=15 level_data=0
R3_PATCH_ACTIVE BUF_WAIT    blk=8 ch=0 rd_addr=15 level_data=4   ← 数据正确到达
R3_PATCH_ACTIVE DUTY_WAIT   blk=8 ch=0 level_data=4 latch=0      ← 锁存却为 0
OUT_TGT t=104496409668 blk=8 ch=0 lvl=0 pwm=0x000 foff=1 rd_data=4 dd=0
```

`latch = force_off_q ? 3'd0 : level_data[2:0]`，`level_data=4` 而 `latch=0`
→ **`force_off_q = 1`**。

### 1.3 force_off 来源分解（决定性）

```
FOFF t=80629409412  safety=1  fs=0  sw=0  eff=1  run=1  state=3
```

| 信号 | 值 | 含义 |
|---|---|---|
| `fs_force_off` | **0** | failsafe 未超时 |
| `sw_force_off` | **0** | 软件未强制 |
| **`safety_off`** | **1** | ← **唯一来源** |
| `runtime_run` | 1 | 因 OFF_SCAN 时 runtime_run 强制为 1 |
| `recovery_state` | **3** | **OFF_SCAN** |

`fault_recovery_ctrl.v:21-22`：
```verilog
assign runtime_run = ((state==RUN) && !fault_latched) || (state==OFF_SCAN);
assign safety_off = (state==OFF_SCAN);
```

**FSM 停在 OFF_SCAN → `safety_off=1` → `fs_force_eff=1` → scheduler 每帧锁存
`force_off_q=1` → 所有通道 `duty_level` 强制 0 → Duty LUT 输出 FULL_OFF。**

**这就是强度 4 丢失的确切机制。** 数据链本身无缺陷。

### 1.4 与真实板卡现象一致

板端实测 `RECOVERY_STATUS = 0x5B/0x44`（OFF_SCAN / BACKOFF）、`safety_off=1`、
`OE_CTRL=0x2`、所有块 FULL_OFF —— **与仿真完全吻合**。
这说明板卡上的"全 NACK"之外，还存在**同一 FSM 卡死问题**。

---

## 2. 修复尝试与结果

### 尝试 1：scheduler 采样点后移（**已判定为误判，已回退**）

- 改动：`S_BUF_WAIT` → `S_DUTY_WAIT` 采样 `level_data`
- 结果：无变化
- **判定**：**不必要**。四拍追踪证明 `BUF_WAIT` 时 `level_data` 已为 4，
  原采样点无问题。已把 `rtl_4a/scheduler_fsm.v` 恢复为基线。

### 尝试 2：`fault_recovery_ctrl` 的 init_done 稳定等待

- 改动：`WAIT_INIT` 判定改用 `init_ready`（`init_done` 延迟 4 拍）
- 文件：`rtl_4a/fault_recovery_ctrl.v`
  - 基线 `c622752364dd60a493b731c7aa54ba6cfe04bedec52148bbcb1428cf5586fe15`
  - 修改 `b8e2e096b05dd42a6bc3c6704145cf850d0b132568de358644af3de0c070e541`
- **结果：无任何变化**，`FOFF t=80629409412` 时间戳逐条相同，仍 `state=3`

### 尝试 2 失败的推论

**FSM 不是从 `WAIT_INIT → QUIESCE` 进入 OFF_SCAN 的**，否则延迟判定会改变时序。
更可能是走 **`RUN → QUIESCE`**（`fault_latched` 脉冲）路径，或另有路径。

---

## 3. 下一轮必须做的一件事

**直接打印 FSM 的每一次状态跳转**，定位进入 OFF_SCAN 的确切拍与条件：

```verilog
always @(posedge clk)
  if (u_rec.state !== state_prev)
    $display("FSM %0d -> %0d  init_done=%b init_healthy=%b fault=%b init_busy=%b idle=%b",
             state_prev, u_rec.state, init_done, init_healthy, fault_latched, init_busy, pipeline_idle);
```

这样一次即可确定是 `WAIT_INIT→QUIESCE` 还是 `RUN→QUIESCE`，
进而定位是 `init_healthy` 判据还是 `fault_latched` 脉冲。

**不做这一步，继续调参数都是盲试。**

---

## 4. 验收结论

| 层级 | 状态 |
|---|---|
| 仿真 | ❌ **未通过**（PWM 未产生） |
| 综合 | 未执行 |
| 真实硬件 | 未执行（禁止上板） |

| 验收项 | 结果 |
|---|---|
| ONLINE_MASK=FFFF | ✅ |
| FAULT_LATCHED=0 | ✅ |
| level_data=4 | ✅（BUF_WAIT 处为 4） |
| Duty LUT 输出正确 | ❌（被 force_off 强制为 0） |
| PCA9685 LED0 正确 | ❌（`00/00/00/10`） |
| FULL_OFF 清除 | ❌ |
| 50Hz PWM 波形 | ❌ **未产生** |

---

## 5. 边界

- ✅ 生产 RTL 零修改（改动仅在 `rtl_4a/` 隔离副本）
- ✅ 历史产物未删除/覆盖（新建 `sim_4c_r3_clean`）
- ✅ STM32 工程只读
- ✅ 未上板、未烧录、未执行 MMIO
- ⚠️ .wdb 未保存
