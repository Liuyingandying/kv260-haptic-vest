# Phase 4C-R1 — PWM Duty 数据链路根因定位

**日期**：2026-11-02
**方式**：只读审计 + 仿真观测（未修改任何生产 RTL）
**结论**：**数据链路前四级（AXI→Matrix Buffer→Commit→Active bank）已证明完全正确；
故障收窄到 Scheduler 扫描读取 → Duty ROM → Burst Builder 段落。
同时发现 Phase 4C 的两个"异常"中有一个是我自己的测试错误，不是 RTL 缺陷。**

---

## 0. 最重要更正：Phase 4C 中两个"异常"的定性

| Phase 4C 现象 | 正确解释 | 是否 RTL 缺陷 |
|---|---|---|
| `FRAME_CNT = 0` | **仿真窗口不足**。窗口从 4M → 20M clk 后 `FRAME_CNT = 1` | ❌ **不是** |
| `BURST_CNT = 14` | 同上，窗口不足导致只完成 14 个。20M 后变 17 | ❌ **不是** |
| `MB_STATUS_POST = 0x0E` 疑似矛盾 | **我读错地址**：0x1004 属 sts 窗口 = BURST_CNT，不是 MB_STATUS。MB_STATUS 在 **0x0004** | ❌ 我的错误 |
| 写矩阵字节 128 | **地址算错**：block8/ch0 的 `source_index = 15`，不是 128 | ❌ 我的错误 |

**Phase 4C 报告的"PWM 未写入"结论本身正确，但归因方向有误。以下是逐级实证。**

---

## 1. 阶段 1：真实寄存器定义（逐行核对，非猜测）

### 1.1 AXI 地址映射（`kv260_haptic_top.v:98-103`）

```verilog
wire sel_ctrl_aw = (s_axi_awaddr[13:12] == 2'b00);   // 0x0000..0x0FFF → matrix_buffer_axi
wire sel_sts_aw  = (s_axi_awaddr[13:12] == 2'b01);   // 0x1000..0x1FFF → axi_status_regs
wire sel_oe_aw   = (s_axi_awaddr[13:12] == 2'b10);   // 0x2000..0x2FFF → OE_CTRL
```

| 偏移 | 含义 | 证据 |
|---|---|---|
| 0x0000 | CTRL（写：bit1=COMMIT） | `matrix_buffer_axi.v:203,216,257` |
| 0x0004 | STATUS | `:281` `{29'h0, enable, ack_sticky, act_bank}` |
| 0x0008 | COMMIT_STATUS（v1.6） | `:228,247` |
| 0x0100–0x01FF | **pending bank** 写/读窗口 | `:201` `awaddr[9:8]==2'b01` |
| 0x0200–0x02FF | **active bank** 只读窗口 | `:270` `araddr[9:8]==2'b10` |
| 0x1000–0x1054 | 状态寄存器组 | `axi_status_regs.v` |
| 0x2000 | OE_CTRL | `haptic_oe_guard` |

### 1.2 字节地址公式（决定写入位置）

```verilog
// matrix_buffer_axi.v:204
wire [7:0] w_byte_addr = {awaddr_r[7:2], wcnt};    // ← 注意：不含 0x100 基址
// :272 读侧同一公式
wire [7:0] r_byte_addr = {araddr_r[7:2], rcnt[1:0]};
```

**关键**：`{addr[7:2], byte_in_word}` 直接就是 8-bit 矩阵字节索引。
所以 AXI `0x110C` 无效 —— 正确写法是 `0x10C` → `{0x0C>>2, 3} = {3,3} = 15`。

### 1.3 commit / bank swap

```verilog
assign cmt_write    = w_exec & w_is_ctrl0 & wdata_r[1];        // CTRL.bit1 = COMMIT
assign cmt_grant_now= cmt_pend_q & commit_grant_i & !cmt_block;
assign cmt_block    = commit_gate_en &
                      (sched_busy|dd_busy|init_busy|fault_latched);
```
`cmt_block` 在初始化完成、无故障、调度空闲时才放行 —— **仿真中确实放行了**（见 §2）。

### 1.4 `cmt_pend_lock` 生命周期

```verilog
// :206
wire cmt_pend_lock = (commit_gate_en===1'b1) & cmt_pend_q;
// :211
assign mb_wr_en = w_exec & w_is_pend & wstrb_r[wcnt] & !cmt_pend_lock;
```
**COMMIT 请求被挂起期间（`cmt_pend_q=1`），PEND 窗口写入会被丢弃**，
并置 `drop_seen_q` 粘滞标记。仿真中 `CMT_STATUS_PRE=0`、`POST=0` → **未发生丢弃**。

### 1.5 强度编码（`scheduler_fsm.v:54,70`）

```verilog
input  wire [7:0] level_data,   // 当前 cell 强度 (0~4, 显示 bank)
output reg  [2:0] duty_level,   // = level_data
```
**矩阵字节直接就是 0–4 级强度，无灰度转换。**
（Phase 4C 中"255 代替合法值"的尝试是错误方向，已撤回。）

### 1.6 `source_index` 真实映射（`matrix_map_rom.v:42-53`）—— 本次根因关键

```verilog
if (blk < 8) begin
    row = blk*2 + ch[3];            // block*2 + channel/8
    col = ch[2:0];                  // channel % 8
end else begin
    row = (blk-8)*2 + ch[3];
    if (blk <= 11) col = 15 - ch[2:0];   // ★ 右上区列镜像
    else           col = 8 + ch[2:0];
end
source_index = {row[3:0], col};     // = row*16 + col
```

**`source_index ≠ block*16 + channel`。** 例：block 8 / ch 0 → `row=0, col=15` → **source_index = 15**。

---

## 2. 阶段 2：Matrix Buffer 实测追踪

使用正确的字节 15 与正确回读地址（0x10C / 0x20C）：

| 观测点 | 实测值 | 判定 |
|---|---|---|
| 写入 `PEND_W_0x10C`（bytes 12–15） | **`0x04000000`** → byte15 = 4 | ✅ **写入落库成功** |
| `PEND_W_0x108`（bytes 8–11） | `0x00000000` | ✅ 相邻字节未被误写 |
| `MB_STATUS_PRE` | `0x00000004` → enable=1, act_bank=0 | ✅ |
| `MB_STATUS_POST` | `0x00000007` → enable=1, ack=1, **act_bank=1** | ✅ **bank 已交换** |
| `CMT_STATUS_PRE/POST` | `0x0` / `0x0` | ✅ 无 pending 挂起、无丢弃 |
| **`ACT_W_0x20C`（active bank）** | **`0x04000000`** | ✅ **commit 后数据正确进入 active bank** |

**结论：AXI 写入 → pending bank → commit swap → active bank，全链路数据正确。**

---

## 3. 阶段 5：FRAME_CNT / BURST_CNT 真实含义

`axi_status_regs.v:205-206`：
```verilog
4'd0: rdata_r <= {16'h0000, frame_cnt};    // 0x1000
4'd1: rdata_r <= {16'h0000, burst_cnt};    // 0x1004
```
- `frame_cnt` ← `frame_done_i`（调度器 256 通道扫描完成脉冲）
- `burst_cnt` ← `burst_trig_i`

**实测（窗口 20M clk ≈ 206 ms）：**
```
FRAME_CNT   = 1     ← 一帧完整扫完
BURST_CNT   = 17    ← 16 个 block 突发 + 1（TCA 全局/额外事务）
DIRTY_BITMAP= 0xFFFF, DIRTY_COUNT = 16   ← 首帧 16 块全脏
```

**因此 Phase 4C 的 `FRAME_CNT=0 / BURST_CNT=14` 纯属窗口不足，不是 RTL 缺陷。**

---

## 4. 仍未定位的段落

| 级 | 状态 | 证据 |
|---|---|---|
| 1. AXI 写入 | ✅ **正确** | PEND 回读 = 0x04000000 |
| 2. Matrix Buffer 保存 | ✅ **正确** | 同上 |
| 3. Commit / bank swap | ✅ **正确** | act_bank 0→1 |
| 4. Active bank 数据 | ✅ **正确** | ACT 回读 = 0x04000000 |
| 5. **Scheduler 读到的 level** | ❌ **疑似为 0** | block8 LED0_OFF_H 仍 0x10 |
| 6. Duty ROM 输出 | ❓ 未观测 | — |
| 7. Burst Builder 字节 | ❓ 未观测 | — |
| 8. PCA9685 LED0 寄存器 | ❌ **仍为 FULL_OFF** | `LED0_OFF_H=0x10` |

### 根因候选（按证据强度排序）

**候选 A（最强）：Scheduler 的 `scan_rd_addr` 与 `mb_rd_addr` 仲裁时序错配**
`matrix_buffer_axi.v:274`：
```verilog
assign mb_rd_addr = (scan_rd_sel===1'b1) ? scan_rd_addr :
                    ((rstate==R_BURST) && r_buf_sel_act ? r_byte_addr : 8'h00);
```
若 `scan_rd_sel` 在 scheduler 送址那一拍为 0，地址会被强制成 `8'h00`
→ 读到 byte 0（=0）→ level 0 → FULL_OFF。
**这能完整解释"active bank 数据正确但 duty 为 FULL_OFF"。**

**候选 B：`mb_rd_data` 取的是 active bank，但扫描发生在 swap 之前**
`frame_cnt=1` 那一帧可能是 commit 之前 dirty-detector 触发的空帧。

**候选 C：burst builder 的 LED0 字节序与我读的寄存器偏移不一致**
PCA9685 `LED0_ON_L=0x06`，若 builder 实际从别的寄存器起写，`rr[0x09]` 就不是 ch0 的 OFF_H。

**候选 D：我观测的 `gp[8].rr[]` 索引与 block8 实际 PCA 不一致**
block8 → 0x40，`gp[8]` 应为 0x40 —— 已核对 `matrix_map_rom.v:67` 一致。

---

## 5. 最小验证方案（下一轮，不改 RTL）

1. **探针 A**：在 TB 中层次化打印 `dut.u_pipeline.scan_rd_sel`、`scan_rd_addr`、`mb_rd_addr`、`scan_rd_data`，
   确认 scheduler 送出的地址与 buffer 实际读地址是否一致、且 `scan_rd_data == 4`
2. **探针 B**：打印 `duty_level`、`duty_pwm`、`duty_full_off`（pipeline 内部 wire），确认 duty ROM 输出
3. **探针 C**：打印 `buf_ch_duty` / `buf_ch_full_off` / `buf_trig`，确认 refresh buffer 收到 0xFF0
4. **探针 D**：在 PCA 模型内加写事件日志（寄存器地址 + 数据 + 时刻），区分初始化 FULL_OFF 与刷新 duty
5. 以上四项定位后，再判断是否需要改 RTL

---

## 6. 交付物

| 文件 | 说明 |
|---|---|
| 本文件 | 根因定位报告 |
| `RTL_INTEGRATION_AUDIT.md` | Phase 4C 的集成审计 |
| `SHA256_MANIFEST_4C.md` | 文件清单 |

**本轮未修改任何生产 RTL、未修改 4A/4B 产物、未触碰 STM32 工程、未上板。**
