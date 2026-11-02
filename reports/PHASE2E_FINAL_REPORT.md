# Phase 2E 最终报告

**日期**：2026-11-02  **结果**：**COMMIT_ACK 根因查明（安全门控，非缺陷）；
I²C 焊盘成功移交 haptic master，集成构建与上板加载通过；真实波形与 PCA9685 未验证**

---

## 0. 验收层级（严格区分）

| 层级 | 状态 |
|---|---|
| RTL 仿真通过 | ✅ 既有（21 项 / 1E / 1F / 1G-U / 1G-V / AXI 交错） |
| Vivado 实现通过 | ✅ **本轮：BD 验证 + 综合 + 实现 + 时序全过** |
| Linux AXI 真实访问通过 | ✅ **本轮：读/写/读回全通过，系统不挂死** |
| **I²C 波形通过** | ❌ **未验证**（需逻辑分析仪，我无法操作实验室仪器） |
| **PCA9685 实际响应通过** | ❌ **未验证**（需物理接线，且 V+ 必须保持断开） |
| 完整触觉链 | ❌ 未达成 |

---

## 1. 第一阶段：COMMIT_ACK 根因核查（完成）

### 1.1 准确产生条件（源码级）

`rtl/matrix_buffer_axi.v`：
```verilog
L217  assign cmt_block = (commit_gate_en === 1'b1) &
                         ((sched_busy_i  === 1'b1) | (dd_busy_i   === 1'b1) |
                          (init_busy_i   === 1'b1) | (fault_latched_i === 1'b1));
L121  assign commit_ack_o = mb_commit_ack |
                            ((commit_gate_en === 1'b1) & cmt_write & !cmt_block);
L220  wire cmt_grant_now = cmt_pend_q & commit_grant_i & !cmt_block;
L248  assign mb_commit = (commit_gate_en === 1'b1) ? cmt_grant_now : cmt_write;
L280  wire [31:0] status_val = {29'h0, enable_r, ack_sticky, mb_act_bank};
```

`rtl/kv260_haptic_pipeline.v`（实际连接）：
```verilog
L261  .commit_gate_en  (1'b1),                      // 门控硬连线，永远启用
L262  .sched_busy_i    (sched_busy),
L263  .dd_busy_i       (dd_active),
L264  .init_busy_i     (init_busy || !runtime_run), // 初始化未完成 → 阻塞
L265  .fault_latched_i (sw_fault_latched),          // 故障锁存 → 阻塞
```

### 1.2 STATUS 寄存器解码（澄清上一轮的 0x00000004）

```
STATUS = {29'h0, enable_r, ack_sticky, mb_act_bank}
        bit0 = act_bank    bit1 = COMMIT_ACK    bit2 = ENABLE 回显
```
上一轮读到的 `MB_STATUS = 0x00000004` = **bit2=1 = ENABLE 回显**，寄存器本身完全正常。

### 1.3 结论：**是安全门控，不是缺陷**

板端实测 `ST_FAULT_LATCHED = 1`。原因：**未连接 PCA9685，I²C 初始化必然失败 → 故障锁存**。
于是 `fault_latched_i=1` 与 `init_busy||!runtime_run=1` 同时成立 → `cmt_block=1`
→ `commit_ack_o` 被抑制、`cmt_grant_now` 被抑制。

**这完全符合 fail-closed 安全设计：故障状态下禁止换帧。按任务要求，绝不削弱该门控。**

### 1.4 前置条件链（为什么现在无法验证 ACK）

```
COMMIT_ACK 置位
   └─ 需要 cmt_block = 0
        ├─ sched_busy = 0        （空闲即可）
        ├─ dd_busy = 0           （空闲即可）
        ├─ init_busy = 0 && runtime_run = 1   ← 需要 init_done + init_healthy
        └─ fault_latched = 0     ← 需要初始化成功
             └─ 需要真实 I²C 上 PCA9685 返回 ACK
```

**因此在 PCA9685 接上并初始化成功之前，COMMIT_ACK 在硬件上不可能置位。**
真实 ACK 验证必须等到第四阶段。

### 1.5 关于"分别模拟正常初始化 / 故障锁存两种情况"

- **故障锁存情况**：已在真实硬件复现（本轮上板，`FAULT=1`，COMMIT 被门控）✅
- **正常初始化情况**：无 PCA9685 无法在硬件上构造；仿真可做但不能替代硬件验收，
  按任务要求"不将仿真通过等同于硬件通过"，故**不宣称已验证**。

---

## 2. 第二阶段：I²C 引脚接管（完成）

### 2.1 隔离工程（原始工程未动）
```
E:\KVHAP_2D  （Phase 2D，保留）
E:\KVI2C_1019A（已验证基线，保留）
E:\KVHAP_2E  （Phase 2E 新隔离工程）← 本轮
```
**未覆盖任何原工程**；基线 RTL 复制到 `E:\KVHAP_2E\rtl_pad\` 后修改，
不触碰 `E:\01_Projects\...\kv260_i2c_bringup_20261019T090440Z\rtl\`。

### 2.2 保留内容（未重建）
- KV260 PS / AFI（128-bit）/ AXI SmartConnect / 时钟 / 复位结构
- Linux UIO 访问架构
- `diag_gpio`（0x9E000000）、`M_AXI_PERCEPTION`（0x9F000000）
- 顶层端口名 `i2c_scl_io` / `i2c_sda_io` 及 XDC（H12/B10 约束不变）

### 2.3 焊盘所有权移交（4 处精确修改）

| 文件 | 修改 |
|---|---|
| `rtl_pad/kv260_haptic_pad_wrapper.v` | **新建**：暴露 `haptic_scl`/`haptic_sda` inout 的 BD 包装 |
| `rtl_pad/kv260_i2c_bringup_top.v` | 删除基线两个 IOBUF；`assign iic_scl_i = i2c_scl_io;`；新增 `tran (i2c_scl_io, haptic_scl)` / `tran (i2c_sda_io, haptic_sda)`；BD 实例新增 `.haptic_scl_0/.haptic_sda_0` |
| BD | `haptic_bd` → `haptic_pad`（新模块名绕缓存）；`make_bd_pins_external` 两个 inout |
| `build_2e.tcl` | 集成脚本 |

**单一驱动保证**：
```
删除前：u_scl_iobuf/OBUFT 与 haptic IOBUF 同时驱动 → DRC MDRV-1 报错（已复现）
删除后：只有 haptic 内部 IOBUF 经 tran 驱动焊盘 → 单驱动，DRC 通过
```

### 2.4 IOBUF 开漏确认

冻结 RTL `kv260_haptic_top.v` 内部 IOBUF（来自 Phase 1G-V 回归）：
- `I = 1'b0`（只驱动 0）
- `T = ~drive_low`（要拉低才使能输出，否则高阻）
- **符合开漏规范：只主动拉低或释放，不主动推高**

### 2.5 引脚与 Bank45 电压条件

Vivado `report_io` 实测：
```
H12  i2c_scl_io  Bank45  IO_L4N_AD12N_45  BIDIR  LVCMOS33  DRIVE 4  SLEW SLOW
B10  i2c_sda_io  Bank45  IO_L9N_AD11N_45  BIDIR  LVCMOS33  DRIVE 4  SLEW SLOW
```
来源：基线 `candidate_pmod.xdc`，注释注明依据**官方 BIST**（HDA11=H12、HDA15=B10，bank45 LVCMOS33）。

> ⚠️ **Bank45 实际 VCCO 仍未实物测量。LVCMOS33 在 Bank45 属 HD Bank，
> 需 VCCO=3.3 V 才成立。接外设前必须实测。**

### 2.6 构建结果

| 项 | 值 |
|---|---|
| BD | 验证通过 |
| 综合 / 实现 | 通过 |
| 时序 | **All user specified timing constraints are met**（WNS 3.063 / WHS 0.010） |
| CLB LUTs | 8,512（7.27%） |
| CLB Registers | 14,372（6.14%） |
| Block RAM | 7 |
| **Bonded IOB** | **2** |
| XSA | `kv260_haptic_2e.xsa`，sha256 **`902859491bcea8a7876894e296b6c38020bb74d2bc1447dcf5c89bd431823caf`** |

---

## 3. 第三阶段：真实 I2C 验证

### 3.1 已完成部分（Runtime App 加载 + 用户态访问）

```
包 sha256 = 47ff92a54f6943322b08f315e4371a89e26a02bcd7c51b0dfb959ee94c3b5e75（两端一致）
sudo xmutil unloadapp                -> remove from slot 0 returns: 0 (Ok)
sudo xmutil loadapp kv260-haptic-2e  -> kv260-haptic-2e: Loaded with slot_handle 0
fpga_manager/fpga0/state             -> operating

UIO:
  uio4: kv260-iic-diag
  uio5: kv260-pspl-mailbox
  uio6: kv260-haptic         <-- 我们的触觉设计
       map0 addr = 0x000000009c000000
```

**用户态 AXI 真实访问（I²C 焊盘已归 haptic 所有）**

| 偏移 | 名称 | 值 |
|---|---|---|
| 0x0000 | MB_CTRL | `0x00000000` |
| 0x0004 | MB_STATUS | `0x00000000` |
| 0x1028 | INIT_DONE | `0x00000000`（无 PCA9685，符合预期） |
| 0x102C | ONLINE_MASK | `0x00000000` |
| **0x103C** | **FAULT_LATCHED** | **`0x00000001`** |
| **0x1054** | **RECOVERY_STATUS** | **`0x0000005B`** |
| **0x2000** | **OE_CTRL** | **`0x00000002`（oe_n=1 → 输出关断 ✅ PASS）** |

写入读回：
```
w 0x0000 <- 0x00000001  (ENABLE)
r 0x0000    = 0x00000001  EN=1  ✅ PASS
```

矩阵写入 + COMMIT：
```
w 0x0100 <- 0x00000003
w 0x0000 <- 0x00000003
MB_STATUS = 0x00000004  COMMIT_ACK=0  iter=19999   ← 安全门控（见第一阶段）
```

**`SYSTEM_ALIVE` —— 全程未挂死。**

### 3.2 未完成部分（需要物理仪器/接线）

| 项 | 为什么没做 |
|---|---|
| **逻辑分析仪抓 SCL/SDA** | 需连接探头，我无法操作实验室仪器 |
| **实测 SCL ≈ 400 kHz** | 同上（设计值：100 MHz / (4×63) ≈ 397 kHz） |
| **START / 地址 / ACK / STOP 时序** | 同上 |
| **PCA9685 地址 ACK** | 需物理接线 + Bank45 VCCO 实测 |

> **明确声明：I²C 波形与 PCA9685 响应均未通过硬件验证。**
> 当前只能确认：AXI 可达、寄存器可读写、OE 关断、I²C 焊盘所有权已归 haptic、系统不挂死。

---

## 4. 第四阶段：PCA9685 —— 未开始

前置条件（全部未满足）：
1. Bank45 VCCO 实测确认 3.3 V（LVCMOS33 成立）
2. J2 引脚实物确认（H12/B10 有官方 BIST 佐证，但仍需实物核对）
3. 逻辑分析仪验证 I²C 波形
4. PCA9685 模块 + 上拉电阻
5. **V+ 执行器电源保持断开**

**当前不具备执行条件，未做任何接线。**

---

## 5. 未解决问题

| # | 问题 | 等级 |
|---|---|---|
| 1 | **I²C 波形未验证**（需逻辑分析仪） | 高 |
| 2 | **PCA9685 未连接，COMMIT_ACK 因此无法置位** | 高 |
| 3 | **Bank45 实际 VCCO 未实测** | 高（阻塞接线） |
| 4 | J2 引脚未实物确认 | 高 |
| 5 | 256 路矩阵 / 双缓冲 / Scheduler / 灰度 ROM 未在硬件验证 | 中 |
| 6 | SCL 设计值 ≈397 kHz，需实测确认 | 中 |

---

## 6. 交付物

| 文件 | 说明 |
|---|---|
| `phase2e/kv260_haptic_2e.xsa` | 集成 XSA（sha256 `902859491bcea8a7876894e296b6c38020bb74d2bc1447dcf5c89bd431823caf`） |
| `phase2e/kv260_haptic_2e.bit.bin` | 可加载位流（sha256 `1520a41d9f5a2bd87f3907e48605768a5fdb856244b9d64c71b5f0b6c682a272`） |
| `phase2e/kv260_haptic_2e.dtbo` | 设备树 overlay |
| `phase2e/kv260_haptic_pad_wrapper.v` | 焊盘所有权包装模块 |
| `phase2e/kv260_i2c_bringup_top_PHASE2E.v` | 修改后的顶层（含 tran 移交） |
| `E:\KVHAP_2E\` | 完整隔离工程 |
| `kv260_2e/evidence/` | 上板原始输出（net / load / probe） |

---

## 7. 边界

- 未覆盖原工程（`KVHAP_2D`、`KVI2C_1019A`、`01_Projects` 基线全部保留）
- 未修改冻结 RTL（21 个文件 SHA 未变）
- 未修改 QSPI、未覆盖 Image A
- 未连接 PCA9685 / 执行器 / V+
- 未改变供电、未改 STM32
- **未削弱任何安全门控**（COMMIT 门控保持原样）
- 未把仿真结果当作硬件结果

---

## 8. 构建过程经验（供后续参考）

| 问题 | 原因 | 解法 |
|---|---|---|
| BD 报 `FREQ_HZ does not match (90908180 vs 96968727)` | BD 缓存了模块接口属性 | 换新模块名强制重新解析 |
| BD 报 `address 0xA0000000 must fit aperture` | PS 只路由 `0x8000_0000–0x9FFF_FFFF` | 改用 `0x9C000000` |
| 综合报 `port 'haptic_scl' does not exist` | `make_bd_pins_external` 自动加 `_0` 后缀 | 用 `haptic_scl_0` / `haptic_sda_0` |
| DRC `MDRV-1 Multiple Driver` | 基线 IOBUF 与 haptic IOBUF 同时驱动 | 删除基线 IOBUF，仅保留 haptic |
| 长构建误判为卡死 | 单次 wait 30 分钟 | 改为异步 + 短间隔轮询 + 日志新鲜度判断 |
