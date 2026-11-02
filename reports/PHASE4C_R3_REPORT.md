# Phase 4C-R3 — PWM 定位与修复（未完成）

**日期**：2026-11-02
**结果**：❌ **未达到端到端验收。真实 RTL 仍未产生正确 PWM 波形。**

---

## 1. 本轮确定的事实（周期级证据）

### 1.1 决定性观测

```
OUT_TGT t=104496409668  blk=8 ch=0  lvl=0  pwm=0x000 foff=1  rd_data=4  dd=0
```

**同一个 `out_valid` 周期内：`scan_rd_data = 4`（总线数据正确），但 `duty_level_bits = 0`。**

这是强度 4 丢失的**确切位置**：在 `scheduler_fsm` 把 `level_data` 锁存进 `duty_level` 的那一拍。

### 1.2 各级实测

| 级 | 观测 | 值 | 判定 |
|---|---|---|---|
| AXI → pending | `PEND_W_0x10C` | `0x04000000` | ✅ 写入成功 |
| Commit → active | `ACT_W_0x20C` | `0x04000000` | ✅ swap 成功 |
| 读地址仲裁 | `scan_rd_sel/mb_rd_addr` | `1 / 15` | ✅ 正确 |
| Buffer 输出 | `scan_rd_data` | **4** | ✅ 数据正确 |
| **Scheduler 锁存** | `duty_level_bits` | **0** | ❌ **丢失点** |
| Refresh Buffer | `buf_ch_duty` | `0x000` | ❌ 已被 0 污染 |
| PCA 0x40 LED0 | `reg 0x06~0x09` | `00/00/00/10` | ❌ FULL_OFF |

### 1.3 PCA 写事件日志（区分了初始化与刷新）

```
PCA_WR t=40854437000  addr=0x40 reg=0x06 data=0x00    ← 初始化
PCA_WR t=40901965000  addr=0x40 reg=0x07 data=0x00
PCA_WR t=40949493000  addr=0x40 reg=0x08 data=0x00
PCA_WR t=40997021000  addr=0x40 reg=0x09 data=0x10    ← FULL_OFF

PCA_WR t=108311024000 addr=0x40 reg=0x06 data=0x00    ← 刷新（仍全 0）
PCA_WR t=108358552000 addr=0x40 reg=0x07 data=0x00
PCA_WR t=108406080000 addr=0x40 reg=0x08 data=0x00
PCA_WR t=108453608000 addr=0x40 reg=0x09 data=0x10    ← 仍 FULL_OFF
```

**刷新事务确实发生了**（第二组），但写入的是 `0x00/0x00/0x00/0x10` —— 与初始化完全相同。
这证明丢失发生在 scheduler，不是 I²C 或 burst builder。

---

## 2. 尝试的最小修复

### 2.1 假设

`matrix_buffer.v:101` 是**同步读**（`rd_data` 在 `rd_addr` 后 1 clk 才有效）。
原 `scheduler_fsm.v` 在 `S_BUF_WAIT` 拍末采样 `level_data`，
此时 `rd_data` 尚未更新 → 采到旧值。

### 2.2 改动（仅在隔离副本，未动生产 RTL）

文件：`E:\KVHAP_4A_INTERNAL_PULLUP\rtl_4a\scheduler_fsm.v`

```verilog
// 修改前
S_BUF_WAIT: begin
    duty_level <= force_off_q ? 3'd0 : level_data[2:0];
    state      <= S_DUTY_WAIT;
end
S_DUTY_WAIT: begin
    state <= S_OUTPUT;
end

// 修改后
S_BUF_WAIT: begin
    state <= S_DUTY_WAIT;          // 采样点后移一拍
end
S_DUTY_WAIT: begin
    duty_level <= force_off_q ? 3'd0 : level_data[2:0];
    state      <= S_OUTPUT;
end
```

| | SHA256 |
|---|---|
| 基线 `scheduler_fsm.v` | `2c2557057f2a846a2814a81a4ec6ca1d2f6bf0806df9685cfda36ef66090d8eb` |
| 修改后 | `72c2853b03319cebd4536dae99c360540e73b8210aa1188d58856be50ec4cd6e` |

### 2.3 回归结果：**无改善**

```
修复前: OUT_TGT t=104496409668 blk=8 ch=0 lvl=0 pwm=0x000 foff=1 rd_data=4
修复后: OUT_TGT t=104496409668 blk=8 ch=0 lvl=0 pwm=0x000 foff=1 rd_data=4
```

**时间戳逐条完全一致**，包括全部 `BUF_WR` 与 `PCA_WR` 事件。

### 2.4 对"无改善"的两种解释

| 解释 | 说明 | 置信 |
|---|---|---|
| **(a) 补丁未被 elaboration 采用** | `xvlog` 只编译了 `rtl_4a/scheduler_fsm.v`，但 `work` 库中同名模块可能未被替换（先前已从 `rtl_r2` 编译过旧版）。**时间戳完全一致强烈支持这一解释** | **高** |
| (b) 采样点不是根因 | 若补丁确实生效仍无变化，则数据到达比 `S_DUTY_WAIT` 更晚 | 中 |

**本轮未能区分这两者** —— 这是下一步必须首先排除的。

---

## 3. 下一轮的一步到位做法

1. **强制使用补丁**：新建独立编译目录 + 全新 `work` 库，只编译补丁版；
   或在 `xvlog` 前删除 `xsim.dir/work`
2. **验证补丁生效**：在 `S_DUTY_WAIT` 加一次性 `$display` 打印 `level_data`，
   确认新代码路径被执行
3. **若补丁生效仍无改善**：把采样点继续后移到 `S_OUTPUT`，或直接打印
   `S_LOAD_MATRIX / S_BUF_WAIT / S_DUTY_WAIT / S_OUTPUT` 四拍的
   `scan_rd_addr` 与 `scan_rd_data`，用四拍表精确定位数据到达拍
4. **备选怀疑点**：`pipeline.v:269` 的 `scan_rd_addr` 选择器
   `dd_active ? dd_new_addr : map_rd_addr` 与 `scan_rd_sel` 的相位关系 ——
   若 `scan_rd_sel` 比 `scan_rd_addr` 晚一拍置位，buffer 会晚一拍采样

---

## 4. 验收结论

| 层级 | 状态 |
|---|---|
| 仿真 | ❌ **未通过**（PWM 未产生） |
| 综合 | 未执行 |
| 真实硬件 | 未执行（禁止上板） |

**本轮优先级"真实 PWM 波形"未达成。**

---

## 5. 边界

- ✅ 生产 RTL **零修改**（改动仅在 `rtl_4a/scheduler_fsm.v` 隔离副本）
- ✅ Phase 4A/4B/4C 产物未覆盖
- ✅ STM32 原始工程只读
- ✅ 未上板、未烧录、未执行 xmutil/UIO/MMIO
- ⚠️ 波形 .wdb 未保存（探针以带时间戳文本输出）
