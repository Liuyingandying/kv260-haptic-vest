# Phase 4C-R2 — Matrix Read Arbitration 定点根因验证

**日期**：2026-11-02
**方式**：周期级层次化探针仿真（只读，未修改任何生产 RTL）
**结论**：**读地址仲裁完全正确，候选 A 被排除。
强度 4 成功进入 `mb_rd_data`，丢失点位于其下游（Scheduler 输出 → Burst → PCA 寄存器），
且本轮探针未能截获 blk=8/ch=0 的 output 事件，需下一轮补测。**

---

## 1. 周期级信号表（决定性证据）

探针条件：`dut.u_pipe.sched_busy==1 && dut.u_pipe.dd_active==0 && map_rd_addr==15`

| # | 仿真时间 | `scan_rd_sel` | `mb_rd_addr` | `mb_rd_data` | `dd_active` |
|---|---|---|---|---|---|
| 1 | 104496368420 | **1** | **15** | **0** | 0 |
| 2 | 104496378732 | **1** | **15** | **4** | 0 |
| 3 | 104496389044 | **1** | **15** | **4** | 0 |
| 4 | 104496399356 | **1** | **15** | **4** | 0 |
| 5 | 104496409668 | **1** | **15** | **4** | 0 |
| 6 | 104496419980 | **1** | **15** | **4** | 0 |

### 逐项核验（对应任务要求的 6 条）

| # | 检查项 | 判定 | 证据 |
|---|---|---|---|
| 1 | Scheduler 是否发出 block8/ch0 读取请求 | ✅ **是** | `map_rd_addr==15` 命中 6 次 |
| 2 | `source_index` 是否为 15 | ✅ **是** | 与 `matrix_map_rom.v:42-53` 计算一致 |
| 3 | `scan_rd_sel` 是否在有效周期置 1 | ✅ **是** | 6 次采样全为 1 |
| 4 | 若 `scan_rd_sel=0`，R_BURST 与 act 是否同时成立 | ✅ **N/A** | 本轮 `scan_rd_sel` 从未为 0 |
| 5 | `mb_rd_addr` 是否意外退回 0 | ✅ **未退回** | 恒为 15 |
| 6 | `mb_rd_data` 是否最终得到 4 | ✅ **是** | 第 2 拍起稳定为 4 |

**第 1 拍读到 0、第 2 拍读到 4** —— 这是 `matrix_buffer_axi` 的**同步 ROM 读延迟（6 拍流水）**
造成的正常现象，不是错误。

---

## 2. 候选 A 排除

**Phase 4C-R1 的候选 A**（`matrix_buffer_axi.v:274` 读地址仲裁错配导致退回 `8'h00`）：

```verilog
assign mb_rd_addr = (scan_rd_sel===1'b1) ? scan_rd_addr :
                    ((rstate==R_BURST) && r_buf_sel_act ? r_byte_addr : 8'h00);
```

**实测 `scan_rd_sel` 恒为 1，`mb_rd_addr` 恒为 15，从未退回 0。**
→ **候选 A 不成立，已排除。**

`dd_active` 全程为 0，Dirty Detector **未抢占总线**（候选 B 中的"DD 干扰"分支亦排除）。

---

## 3. 数据逐级传递记录（已确证部分）

| 级 | 观测点 | 实测 | 判定 |
|---|---|---|---|
| 1 | AXI 写入 → pending bank | `PEND_W_0x10C = 0x04000000`（byte15=4） | ✅ |
| 2 | Commit → bank swap | `MB_STATUS` act_bank 0→1 | ✅ |
| 3 | active bank 数据 | `ACT_W_0x20C = 0x04000000` | ✅ |
| 4 | Scheduler 读取地址 | `map_rd_addr = 15` | ✅ |
| 5 | **Buffer 输出数据** | **`mb_rd_data = 4`** | ✅ **强度 4 在此仍存在** |
| 6 | Scheduler → `level_data`/`duty_level` | ❓ **未截获 blk8/ch0** | 待补测 |
| 7 | Duty ROM 输出 | ❓ 未观测 | 待补测 |
| 8 | Refresh Buffer | ❓ 未观测 | 待补测 |
| 9 | Burst Builder 字节 | ❓ 未观测 | 待补测 |
| 10 | PCA9685 LED0_OFF_H | `0x10`（FULL_OFF） | ❌ 强度 4 在此已丢失 |

---

## 4. 关键发现：本轮探针的观测覆盖不足

### 4.1 OUT 事件全部来自"第一帧"

```
OUT t=80521793380 blk=0 ch=0 lvl=0 pwm=0x000 foff=1
OUT t=80521865564 blk=0 ch=1 lvl=0 pwm=0x000 foff=1
...
OUT t=80523092692 blk=1 ch=2 lvl=0 pwm=0x000 foff=1
```

**全部 `lvl=0 / foff=1`** —— 说明第一帧扫描时矩阵仍为空。

### 4.2 时间关系

| 事件 | 时间 |
|---|---|
| 第一帧扫描（全 0） | t ≈ 80.52e9 |
| 矩阵写入 + commit | t 介于两者之间 |
| **`mb_rd_data = 4` 出现** | **t ≈ 104.50e9** |

**`mb_rd_data=4` 对应的是第二帧扫描。**
而 OUT 探针 `out_cnt < 20` 在打印完第一帧的 20 条后即停止，
**未能截获第二帧中 blk=8/ch=0 的 `out_valid` 事件**。

→ **无法据此判定强度是在 Scheduler 输出、Duty ROM、还是 Burst 处丢失。**

---

## 5. 回答核心问题

> **强度 4 究竟在哪一个模块、哪一个时钟周期丢失了？**

**已确证：强度 4 在 `matrix_buffer_axi.mb_rd_data` 处仍然完好存在**（t≈104.496e9，多次采到 4）。

**丢失点在 `mb_rd_data` 之后、`PCA9685.LED0_OFF_H` 之前**，具体是以下三段之一：

| 候选 | 位置 | 未排除理由 |
|---|---|---|
| **B1** | `scheduler_fsm.v:319` `.level_data(scan_rd_data)` 采样时序 | 探针未截获 blk8/ch0 的 bj/out 事件 |
| **B2** | `duty_lut_rom` 采样延迟（1 clk） | 无观测 |
| **B3** | `pca9685_burst_builder` 字节组装 / 通道序号 | 无观测 |
| **B4** | 第二帧的 burst 是否真正到达 PCA 0x40 | 无 PCA 写事件日志 |

**注意**：`LED0_OFF_H=0x10` 也可能只是**初始化阶段**留下的 FULL_OFF，
第二帧的 PWM 更新可能根本没写 LED0（例如只写了被判定为脏的通道）。

---

## 6. 最小修复建议

**本轮不能给出修复建议** —— 根因尚未定位到单一模块。
按"禁止提前修改 RTL"的纪律，必须先补测。

### 下一轮（Phase 4C-R3）必须增加的观测

1. **把 OUT 探针改为条件触发**：只在 `cur_blk==8 && cur_ch==0` 时打印，
   不设总数上限（或上限 50），确保截获第二帧
2. **同时打印 `duty_level_bits` 的采样源**：
   `dut.u_pipe.u_sched.level_data` 与当时的 `scan_rd_data` 是否同拍
3. **打印 duty ROM 输出**：`dut.u_pipe.duty_pwm` / `duty_full_off` 在 `out_valid` 那拍的值
4. **打印 refresh buffer**：`buf_ch_wr` / `buf_ch_idx` / `buf_ch_duty` / `buf_ch_full_off`
5. **PCA 模型加写事件日志**：记录 `(时间, TCA 通道, PCA 地址, 寄存器, 数据)`，
   区分初始化 FULL_OFF 与刷新 duty
6. **确保第二帧确实发生**：commit 之后再等足够长时间，并统计 `frame_cnt` 是否 ≥ 2

---

## 7. 修改影响范围与回退方案

**本轮未修改任何生产 RTL。**
仅修改测试平台 `sim/tb_e2e.sv`（新增探针块），可随时删除。

| 项 | 状态 |
|---|---|
| 生产 RTL | **零修改** |
| Phase 4A/4B 产物 | 未覆盖 |
| STM32 原始工程 | 只读 |
| 板卡 | 未接触 |

---

## 8. 交付物

- 本报告
- `SHA256_MANIFEST_4C_R2.md`
- 仿真快照 `e2eP`（含周期探针）

**波形文件（.wdb）本轮未保存** —— 探针以 `$display` 文本形式输出，
已包含周期级时间戳，可作为等价证据。下一轮将启用 `-wdb` 保存完整波形。
