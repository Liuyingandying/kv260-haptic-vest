# Phase 2B-FINAL 报告

**日期**：2026-11-01  **结果**：**回归全绿 + 安全调试镜像（零外部 PL I/O）构建成功**

---

## Phase 0：冻结检查（全部通过）

| 检查项 | 期望 | 实测 | 结果 |
|---|---|---|---|
| `kv260_haptic_top.v` SHA | `a1d3fbb9…` | `a1d3fbb9057cb4b24d5d5892538ca8533c777a68bd85d1618b10895c49de39b9` | ✅ **一致** |
| Phase 1G-V 冻结基线文件 | 保留 | `RTL_FREEZE_1GV.json` 存在，基线值 `cdfb57cb…` | ✅ **保留未覆盖** |
| 其余 20 个 RTL | 未变 | **0 个改动** | ✅ |
| AX7020 原工程 | HEAD `287e2ee`，39 行 | 一致 | ✅ |
| 旧仿真 snapshot 误用 | 无 | 每次新建 xsim.dir | ✅ |

---

## Phase 1：补充回归（全部通过）

### 1.1 Phase 1G-U 心跳（生产时钟 90,908,180 Hz）

```
rises=5  period avg=999.988 us  min=999.988 max=999.988
[PASS] H1 复位心跳为低          [PASS] H5 停振后无新沿
[PASS] H2 周期 ~1000us(±2%)     [PASS] H6 停振 -> 看门狗超时 -> OE# 高(关断)
[PASS] H3 干净 50% 方波         [PASS] H7 恢复时钟心跳恢复
[PASS] H4 心跳期间看门狗满足     [PASS] H8 恢复后看门狗重新满足
=== RESULT: checks=8 errors=0 -> PASS ===
```

### 1.2 Phase 1G-U 心跳（50 MHz，AX7020 兼容性）

```
period avg=1000.000 us   checks=8 errors=0 -> PASS
```

### 1.3 Phase 1G-V 联合仿真（生产时钟）

```
bus_bytes=331  hb_rises=12  hb_period avg=999.988 us min=999.988 max=999.988
=== JOINT RESULT: checks=8 errors=0 -> PASS ===
```

**修正记录**：首次运行时心跳周期报 1066.648 µs 并 FAIL。根因是**测试台没有给
`kv260_haptic_top` 传 `CLK_FREQ_HZ`**，用了默认 96,968,727 而仿真时钟为 90,908,180。
补上参数后 999.988 µs 通过。**这不是 RTL 缺陷**，但说明一条集成规则：
**任何实例化必须显式传入真实时钟频率**（Phase 2A 的 wrapper 已正确处理）。

### 1.4 Phase 2A Block Design（最终 RTL）

| 步骤 | 结果 |
|---|---|
| BD Validate | ✅ **BD VALIDATED** |
| 综合 | ✅ **0 errors，0 critical warnings** |
| 实现 | ✅ **route_design Complete!** |
| 时序 | ✅ **All user specified timing constraints are met** |
| **WNS / TNS** | **+6.724 ns / 0.000**；WHS +0.010 / THS 0.000；6734 端点 |
| CLB LUTs / Registers | 2126（1.82%）/ 2177（0.93%） |
| BRAM / DSP | 0 / 0 |
| **Bonded IOB** | **4**（SCL/SDA/heartbeat/oe_n，UNFIXED 自动分配，未施加候选 XDC） |

### 1.5 关键指标复核

| 指标 | 值 | 结论 |
|---|---|---|
| PL 时钟 | 请求 96.968727 MHz → **实际 90,908,180 Hz** | 已知，wrapper 按实测值传参 |
| I2C SCL | TICKS = 90908180/(4×378700) = 60 → **378,784 Hz** | ✅ ≤400 kHz |
| I2C t_LOW/t_HIGH | 2×60/90.908e6 = **1.320 µs** | ✅ ≥1.3 µs |
| **心跳** | HB_HALF = 45,454 → **999.99 µs ≈ 1.000 kHz** | ✅ 实测 999.988 µs |
| **PCA 初始化延时** | DLY_1MS = 90,908 → **1.000 ms** | ✅ `init_done` @ 48.287 ms |
| IOBUF 推断 | 综合报 `IOBUF => IOBUF ... : 2 instances`（SCL/SDA 三态对） | ✅ |
| OE 复位默认关断 | 层次直读 `dut.oe_n === 1` | ✅ |
| AXI 跨区误命中 | one-hot 检查 **0 违规**；T1–T5 全部 PASS | ✅ |

---

## Phase 2：T7b 已知限制（按批准接受）

| 项 | 状态 |
|---|---|
| 是否宣称完全符合 AXI-Lite 任意 AW/W 到达顺序 | ❌ **不宣称** |
| 适用范围 | **仅 ZynqMP PS HPM 主控路径**（该主控对 AXI-Lite 写同时给出 AW/W） |
| 后续动作 | 真实硬件测试时**观测 AWVALID/AWREADY/WVALID/WREADY** |
| 触发预案 | 一旦出现跨区域 W 先于 AW 并挂起，**立即停止测试** |
| 是否再试 skid buffer | **否**（按批准不再迭代） |

---

## Phase 3：安全调试镜像（构建成功）

### 3.1 设计

新增 `phase2bfinal/kv260_haptic_debug_wrapper.v`（**不改冻结 RTL**）：

```
kv260_haptic_debug_wrapper
├─ 对外只暴露: aclk / aresetn / s_axi（接 BD SmartConnect）
├─ i2c_scl_pin / i2c_sda_pin  -> 内部 net（不引出到任何引脚）
├─ oe_n / wdog_heartbeat      -> 内部 net（仅连 dbg 观察口，BD 内不引出）
└─ 保留: 真实矩阵缓冲区、状态寄存器、OE 守卫、心跳、AXI 译码

BD: zynq_ps(M_AXI_HPM0_FPD) -> smartconnect -> debug_wrapper/s_axi
    pl_clk0 -> smartconnect + wrapper + maxihpm0_fpd_aclk + proc_sys_reset
    pl_resetn0 -> proc_sys_reset -> peripheral_aresetn
    无任何 make_bd_pins_external
```

### 3.2 构建结果

| 步骤 | 结果 |
|---|---|
| BD Validate | ✅ **BD VALIDATED** |
| 综合 | ✅ **0 errors，0 critical warnings** |
| 实现 | ✅ **write_bitstream Complete!** |
| **Bonded IOB** | **0** ← **零外部 PL 用户 I/O 达成** |
| CLB LUTs / Registers | 2109（1.80%）/ 2159（0.92%） |
| **WNS / TNS** | **+7.216 ns / 0.000**；6698 端点；**约束全部满足** |
| 未关闭 DRC / 未忽略引脚冲突 | ✅ **没有使用任何绕过手段** |

### 3.3 产物

| 文件 | 大小 | SHA256 |
|---|---|---|
| `phase2bfinal/build/p2b_debug_bd_wrapper.bit` | 7,797,832 B | `6d052180af90b2947e458eb999971307…` |
| `phase2bfinal/build/p2b_debug_debug.xsa` | 1,213,103 B | `12f44ec7fdf21853dd5988f02f3cd7b7…` |

### 3.4 安全性质

| 性质 | 保证方式 |
|---|---|
| 不暴露未经确认的 J2 外部信号 | 调试 wrapper 把它们全部留在 PL 内部，**Bonded IOB = 0** |
| 不连接真实 PCA9685 | I2C 总线内部悬空，master 会超时 |
| OE 保持安全关断 | I2C 初始化必然失败 → fault 锁存 → `oe_n` 保持 1 |
| 不使用未经确认的 PACKAGE_PIN | 本镜像**不使用任何 PACKAGE_PIN** |
| 未关闭 DRC | `write_bitstream` 在默认 DRC 下通过 |

---

## Phase 4：四层结论严格区分

| 层级 | 状态 | 依据 |
|---|---|---|
| **1. RTL 回归通过** | ✅ **PASS** | 1G-U 8/8 ×2 时钟、1G-V 8/8、Phase 1B 21/21、Phase 1E 11/11、FULL_OFF 0/256、AXI 交错 17 PASS |
| **2. Vivado 实现通过** | ✅ **PASS** | 两个项目均 0 errors、约束满足、WNS +6.724 / +7.216 |
| **3. 调试镜像生成成功** | ✅ **PASS** | bitstream + XSA 均已产出，Bonded IOB = 0 |
| **4. PS/PL 真实硬件通信** | ⛔ **NOT VERIFIED** | **未烧录、未上板、未运行软件** |

**前三项不推导第四项。**

---

## 最终 RTL SHA256 清单（21 个文件）

| 文件 | SHA256 |
|---|---|
| `axi_status_regs.v` | `57b15b840fc6104c47bf1ec5e19aab95bbd61609ea080f5966fdefd5bde91c2c` |
| `block_refresh_buffer.v` | `ff873640f523c1be3170c2f1da96f61dda61222f488bdb564fdf2cc4787427ec` |
| `dirty_detector.v` | `525c1401db9afba8ee1605e2f6801282208b8a4b328d298280f62fa0931adbe8` |
| `duty_lut_rom.v` | `96ff1cdce67dbcfc21e5c12782bc608dc135f29d3b6ab89e1f4c0403734f4122` |
| `failsafe_timer.v` | `e9f731463c98578b7038b6584256c5a399771ae14bff68d5ff2af70bbb45d90f` |
| `fault_recovery_ctrl.v` | `c622752364dd60a493b731c7aa54ba6cfe04bedec52148bbcb1428cf5586fe15` |
| `haptic_oe_guard.v` | `12fa6ca51b8cee00519333353b166ba91fe3e173dbf3e3a1e3a9a14184cfa853` |
| `i2c_fault_monitor.v` | `078c48cff70e2dfc5e398ed7a7e416a0be79812c3427ef6111e9d5ab1d9e5f6c` |
| `i2c_master.v` | `b0282ba70f368f761036b52729b276478225fe388140a6065dd4d20b9f75ccae` |
| `i2c_transaction_builder.v` | `cafbc7be5851751a540190e02267dc55adba2b2e8ad30caf7bf57c9931aced1d` |
| `kv260_haptic_pipeline.v` | `899240a2d94c5535b18686c8251a38c49872bb52e4767902c33a9151fe71f0d7` |
| `kv260_haptic_top.v` | `a1d3fbb9057cb4b24d5d5892538ca8533c777a68bd85d1618b10895c49de39b9` |
| `matrix_buffer.v` | `bf7564d6b3d3fdc28b76655587f434efc371e87abc292c13514c3108fcf9a394` |
| `matrix_buffer_axi.v` | `53dc927cdfe4a1c098331910ae86be8b43dd722cf53abee6e46307210cb67397` |
| `matrix_map_rom.v` | `5d9ed61f2c48dcc3cdc554396d0f786daef988af1599b21b4569a0d96cc2116b` |
| `pca9685_burst_builder.v` | `3fd532529719775a069747cbb69301b9f6191dfe18b1066cbf1c604f7eea850a` |
| `pca_init_sequencer.v` | `a245e27123b9ad749cae6dddfe005061d46d25eb7035dbef0935b17826c1eeaf` |
| `scheduler_fsm.v` | `2c2557057f2a846a2814a81a4ec6ca1d2f6bf0806df9685cfda36ef66090d8eb` |
| `tactile_render_top.v` | `ab16f6327b9e8256294b6b254f58234bd9b469007d2c6bb6688f813521cc6320` |
| `tactile_v12_ctrl.v` | `d12212aa568e3f10ff97fb97ae70cbc7f0d63ccde8ecec6e91cf82be0350fea8` |
| `tca9548_inject.v` | `6d51f4a3c058e488ba5b36a485dbca5c28f98695b8d7b6c1135192557410fb78` |

---

## 剩余问题清单

| # | 问题 | 等级 | 状态 |
|---|---|---|---|
| 1 | **T7b：跨区域 W 先于 AW 会挂起** | 中 | **已批准接受为已知限制**，仅限 ZynqMP PS HPM 路径；观测到即停止 |
| 2 | **PS/PL 真实通信未验证** | 高 | 需上板：加载 bitstream + 运行 `haptic_axi_smoke` |
| 3 | **J2 针号映射仍 UNKNOWN** | 高 | 阻塞任何真实外设接线 |
| 4 | **Bank45 实际 VCCO 未实测** | 高 | 若 1.8 V 则 LVCMOS33 方案作废 |
| 5 | 部署镜像的 4 个外部端口仍 UNFIXED（自动分配 bank65/66） | 中 | 待 J2 确认后施加候选 XDC v2 |
| 6 | 21 项 TB 的短超时局限（T5b/T8c/T10b/T11 空洞） | 中 | 已知，未修复 |
| 7 | 0xA0003000 未映射会挂死 AXI | 中 | 软件必须规避（`haptic_regs.h` 已加守卫宏） |
| 8 | 6 个候选引脚中 5 个被官方分配给 I2S | 中 | 需 XTP682 确认能否复用 |

---

## 边界声明

- ✅ 未修改 AX7020 原工程（HEAD `287e2ee`，39 行）
- ✅ 未覆盖 Phase 1G-V 冻结基线
- ✅ 未擅自修改 J2 引脚映射（本镜像不使用任何 PACKAGE_PIN）
- ✅ 未连接 PCA9685 / 执行器，未改供电
- ✅ **未执行 JTAG / `loadapp` / 实际烧录**
- ✅ 未关闭 DRC、未忽略引脚冲突
