# Phase 2B-FIX：AXI-Lite 地址读写译码串扰修复

**日期**：2026-11-01  **结果**：**安全缺陷已修复并验证；1 项跨区域协议限制如实保留**

---

## 1. 问题回顾

Phase 2B-Lite 冒烟测试发现：一次**合法的矩阵写入**会把 `SW_OE_REQ` 置 1（可能意外使能执行器输出）。

根因（修改前 `rtl/kv260_haptic_top.v` L90–L92）：
```verilog
wire sel_ctrl = (s_axi_awaddr[13:12] == 2'b00) | (s_axi_araddr[13:12] == 2'b00);
wire sel_sts  = (s_axi_awaddr[13:12] == 2'b01) | (s_axi_araddr[13:12] == 2'b01);
wire sel_oe   = (s_axi_awaddr[13:12] == 2'b10) | (s_axi_araddr[13:12] == 2'b10);
```
写地址与读地址被 OR 到同一组选择信号。

---

## 2. 修改前后代码差异

**文件**：`rtl/kv260_haptic_top.v`（唯一修改文件）
**SHA256**：`cdfb57cbfdc014b06c533a2374c308e51a1eefc339c2571936afed6352399dfb` → **`a1d3fbb9057cb4b24d5d5892538ca8533c777a68bd85d1618b10895c49de39b9`**

### 2.1 区域译码拆分（核心安全修复）

```verilog
// 修改前
wire sel_ctrl = (s_axi_awaddr[13:12] == 2'b00) | (s_axi_araddr[13:12] == 2'b00);
wire sel_sts  = (s_axi_awaddr[13:12] == 2'b01) | (s_axi_araddr[13:12] == 2'b01);
wire sel_oe   = (s_axi_awaddr[13:12] == 2'b10) | (s_axi_araddr[13:12] == 2'b10);

// 修改后：写通道用 *_aw，读通道用 *_ar，互不干扰
wire sel_ctrl_aw = (s_axi_awaddr[13:12] == 2'b00);
wire sel_sts_aw  = (s_axi_awaddr[13:12] == 2'b01);
wire sel_oe_aw   = (s_axi_awaddr[13:12] == 2'b10);
wire sel_ctrl_ar = (s_axi_araddr[13:12] == 2'b00);
wire sel_sts_ar  = (s_axi_araddr[13:12] == 2'b01);
wire sel_oe_ar   = (s_axi_araddr[13:12] == 2'b10);
```

### 2.2 响应 mux 按方向分离

```verilog
// 写响应跟随 AW 译码
assign s_axi_awready = sel_ctrl_aw ? ctrl_awready : (sel_sts_aw ? sts_awready : (sel_oe_aw ? 1'b1 : 1'b0));
assign s_axi_wready  = sel_ctrl_aw ? ctrl_wready  : (sel_sts_aw ? sts_wready  : (sel_oe_aw ? 1'b1 : 1'b0));
assign s_axi_bvalid  = sel_ctrl_aw ? ctrl_bvalid  : (sel_sts_aw ? sts_bvalid  : oe_bvalid);
assign s_axi_bresp   = sel_ctrl_aw ? ctrl_bresp   : (sel_sts_aw ? sts_bresp   : oe_bresp);
// 读响应跟随 AR 译码
assign s_axi_arready = sel_ctrl_ar ? ctrl_arready : (sel_sts_ar ? sts_arready : (sel_oe_ar ? 1'b1 : 1'b0));
assign s_axi_rvalid  = sel_ctrl_ar ? ctrl_rvalid  : (sel_sts_ar ? sts_rvalid  : oe_rvalid);
assign s_axi_rresp   = sel_ctrl_ar ? ctrl_rresp   : (sel_sts_ar ? sts_rresp   : oe_rresp);
assign s_axi_rdata   = sel_ctrl_ar ? ctrl_rdata   : (sel_sts_ar ? sts_rdata   : oe_rdata);
```

### 2.3 内部子块 gating 分离

```verilog
.s_axi_ctrl_awvalid (s_axi_awvalid && sel_ctrl_aw),   // 原 sel_ctrl
.s_axi_ctrl_wvalid  (s_axi_wvalid  && sel_ctrl_aw),   // 原 sel_ctrl
.s_axi_ctrl_arvalid (s_axi_arvalid && sel_ctrl_ar),   // 原 sel_ctrl
.s_axi_sts_awvalid  (s_axi_awvalid && sel_sts_aw),    // 原 sel_sts
.s_axi_sts_wvalid   (s_axi_wvalid  && sel_sts_aw),    // 原 sel_sts
.s_axi_sts_arvalid  (s_axi_arvalid && sel_sts_ar),    // 原 sel_sts
```

### 2.4 OE 写通路：AW / W 独立缓冲（协议修复）

```verilog
// 修改前：要求 AWVALID 与 WVALID 同拍
wire oe_wr_hit = s_axi_awvalid && s_axi_wvalid && sel_oe;

// 修改后：两通道各自锁存，齐备即提交
reg  oe_aw_pend, oe_w_pend;
reg [31:0] oe_wdata_q;
wire oe_aw_acc = s_axi_awvalid && sel_oe_aw && !oe_aw_pend;
wire oe_w_acc  = s_axi_wvalid  && sel_oe_aw && !oe_w_pend;
wire oe_commit = oe_aw_pend && oe_w_pend;
// oe_commit 时按 oe_wdata_q 更新 sw_oe_req 并给出 BRESP
```

### 2.5 OE 读通路使用 AR 译码
```verilog
if (sel_oe_ar && s_axi_arvalid && !oe_rvalid) begin   // 原 sel_oe
```

**未修改**：矩阵缓冲、PCA 初始化 FSM、I2C、调度、OE 守卫、心跳逻辑
**未使用规避手段**：未清零 ARADDR、未修改测试顺序、未屏蔽 OE 请求

---

## 3. 新增测试覆盖（T1–T7 + one-hot）

`logs/tb_axi_interleaved.v`，时钟 90,908,180 Hz，OE 状态通过**层次直读 `dut.oe_n` / `dut.sw_oe_req`** 验证（不经 AXI 读回，避免读通路错误掩盖真实状态）。

| 测试 | 操作 | 结果 |
|---|---|---|
| B0/B1 | 复位基线 | **PASS**（`dut.oe_n=1`，`sw_oe_req=0`） |
| **T1** | 读 0x2000 → 写 0x0000 | **PASS**（未误写 OE） |
| **T2** | 读 0x0000 → 写 0x2000 | **PASS**（仅 OE 响应，输出保持关断） |
| **T3** | 读 0x1000 → 写 0x0000 | **PASS**（状态寄存器未被改） |
| **T4** | 写矩阵 → 读 OE | **PASS**（读回 `0x00000002`：oe_n=1、req=0） |
| **T5** | 三区域连续交错读写 12 次 | **PASS**（oe_n 保持 1、req 保持 0） |
| **T6** | AW 先于 W（延迟 5 拍） | **PASS** |
| **T7** | W 先于 AW（同区域，延迟 5 拍） | **PASS** |
| **OH1** | one-hot 检查：每次 AW/AR 最多命中 1 个区域 | **PASS（0 违规）** |
| **T7b** | **跨区域** W 先于 AW（W 指向 OE，AW 尚未给出） | ❌ **挂起（已知限制）** |

日志：`logs/axi_interleaved_test.log`（17 项 PASS）

---

## 4. OE 复位安全性独立审查（§四）

### 4.1 Phase 2B-Lite 中 `oe_n=0` 的真相

**结论：那是读通路串扰造成的"读到错误数据"，不是 OE 状态真的为 0。**

| 检查项 | 结果 |
|---|---|
| RTL 复位赋值 | `haptic_oe_guard.v` L104 `oe_n <= 1'b1`（关断）✅ |
| 守卫输出逻辑 | L109 `oe_n <= ~(arm_ready && dwell_ok)`，`arm_ready` 需 `init_healthy`+软件请求+延时 ✅ |
| AXI OE 寄存器复位 | `sw_oe_req <= 1'b0` ✅ |
| 测试台复位时序 | 复位期间 `oe_n=1`（层次直读已验证）✅ |
| **物理 OE# 输出** | 修复后层次直读 `dut.oe_n === 1'b1` 在所有测试点均成立 ✅ |

修改前读 `0x2000` 时，`sel_ctrl` 被 stale `awaddr` 置真 → mux 优先返回 `ctrl_rdata`（未驱动） → 读到 0，**看起来像 `oe_n=0`**。

### 4.2 修复后 OE 安全条件验证

| 要求 | 结果 |
|---|---|
| 复位后 OE# 保持关断 | ✅ `dut.oe_n=1` |
| 未完成初始化不得使能 | ✅ 未注入 `init_healthy`，`armed=0` |
| 矩阵写入/状态读取不改变 OE 请求 | ✅ T1/T3/T4/T5 全部验证 |
| 故障时保持关断 | 未在本轮重新注入（Phase 1B T10 已覆盖） |

---

## 5. 完整回归结果

| 阶段 | 结果 | 说明 |
|---|---|---|
| **新增 AXI 交错测试** | **17 PASS / 1 KNOWN LIMIT** | T1–T7 + one-hot 全过；T7b 挂起 |
| **Phase 1B 21 项** | **21/21 PASS**（checks=21 errors=0） | 断言未改 |
| **Phase 1E 16 块 + 256 通道** | **PASS 11/11**，`FULL_OFF 0/256` | `init_done=1 healthy=1 online=ffff fault=0` @ 48.287 ms |
| Phase 1D I2C 时序 | **PASS**（含在 Phase 1E B1–B6） | 周期 2639.9 ns → 378.79 kHz，t_LOW/HIGH 1319.9 ns |
| Phase 1F 延时 | **PASS（间接）** | `init_done` 仍为 48.287 ms，与修复前一致 → 延时未被破坏 |
| Phase 1G-U 心跳 | **未重跑** | 与 AXI 译码无耦合；`wdog_heartbeat` 端口未改动 |
| Phase 1G-V 联合回归 | **未重跑** | 同上 |
| Phase 2A BD 集成 | **未重跑** | 需在最终 RTL 上重跑才能定稿 |

**未使用 I2C_TIMEOUT_US=200 / FS_TIMEOUT_MS=2 作为正常初始化验证条件**（21 项 TB 自身仍保留该配置，属已知局限，Phase 1E 用的是生产参数 5000 µs / 500 ms）。

---

## 6. 剩余风险

| # | 风险 | 等级 | 说明 |
|---|---|---|---|
| 1 | **T7b：跨区域 W 先于 AW 会挂起** | 中 | AXI-Lite 的 W 通道无地址，冻结设计无法在 AW 到达前判断目标区域。**ZynqMP PS 的 AXI 主控对 AXI-Lite 写会同时给出 AW/W，不会触发此情形**；但严格按规范这是缺陷。修复需在顶层加 AW/W skid buffer（本轮尝试因声明顺序问题未完成，已回退，避免引入新风险） |
| 2 | Phase 1G-V 全回归 / Phase 2A BD 未在最终 RTL 上重跑 | 中 | 本次只重跑了 21 项 + Phase 1E；其余待补跑 |
| 3 | 21 项 TB 的短超时局限（T5b/T8c/T10b/T11 空洞） | 中 | 已知，未修复 |
| 4 | J2 针号 / Bank45 VCCO 仍 UNKNOWN | 高 | 硬件阻塞 |
| 5 | 0xA0003000 未映射会挂死 AXI | 中 | 软件必须规避 |

---

## 7. 交付物

| 文件 | 说明 |
|---|---|
| `reports/PHASE2B_FIX_REPORT.md` | 本报告 |
| `logs/axi_interleaved_test.log` | T1–T7 + one-hot 测试日志 |
| `logs/full_regression.log` | 21 项 + Phase 1E 回归日志 |
| `logs/tb_axi_interleaved.v` | 新增测试台 |
| `RTL_FREEZE_2B_FIX.json` | 新冻结基线 |
| `SHA256_MANIFEST.json` | 全项目清单 |
| `phase2b/kv260_haptic_top.v.BEFORE_2BFIX` | 修改前备份 |

## 8. 边界声明

- ✅ 仅修改 `rtl/kv260_haptic_top.v` 一个文件
- ✅ Phase 1G-V 冻结基线 `RTL_FREEZE_1GV.json` **保留未覆盖**
- ✅ 未修改 AX7020 原工程；未修改其他功能 RTL
- ✅ 未生成部署 bitstream；未导出部署用 XSA
- ✅ 未 JTAG、未 loadapp、未接 PCA9685/执行器、未改供电

**核心目标达成：PS 对矩阵缓冲区的正常写入，已不可能意外改变 OE 控制状态（T1–T5 实测验证）。**
