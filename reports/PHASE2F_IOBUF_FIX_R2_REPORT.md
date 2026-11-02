# Phase 2F-IOBUF-FIX-R2 报告

**日期**：2026-11-02  **结论**：**仿真通过（18/18，真实 UNISIM IOBUF）；干净基线工程已建立并出 RTL manifest；
Vivado 构建在 BD 连接阶段失败 —— 综合/实现未完成，不宣称修复已通过硬件验收**

---

## 0. 验收状态

| 层级 | 状态 |
|---|---|
| **仿真通过** | ✅ **PASS（18/18）** |
| 综合通过 | ❌ **未完成**（BD 连接阶段失败） |
| 实现通过 | ❌ **未完成** |
| 真实引脚映射确认（J2 ↔ H12/B10） | ❌ **仍未确认** |
| 实物电压验证 | ❌ **未做** |

---

## 1. 第一部分：独立 XSim 仿真 —— ✅ 通过

### 1.1 方法（符合任务要求）
- 使用**真实 UNISIM `IOBUF` 原语**（`-L unisims_ver`），**不是普通 `assign` 三态模型**
- IOBUF 连接形式与 `rtl_fix/kv260_haptic_iobuf_wrapper.v` **完全一致**：
  ```verilog
  IOBUF u_scl_iobuf (.I(1'b0), .T(~scl_drv), .O(scl_sense), .IO(i2c_scl));
  IOBUF u_sda_iobuf (.I(1'b0), .T(~sda_drv), .O(sda_sense), .IO(i2c_sda));
  ```
- 外部上拉模型：Verilog 标准 `pullup` 原语
- 从设备模型：`assign bus = slave_low ? 1'b0 : 1'bz`
- 需要 `glbl` 模块（UNISIM IOBUF 依赖 GSR），已加入 elaboration

### 1.2 结果：**18 项检查全部 PASS**

| 组 | 检查项 | 结果 |
|---|---|---|
| T1 | 复位释放 → SCL/SDA 高 + sense 采样高 | ✅ 4/4 |
| T2 | drv=1 → 总线被拉低 | ✅ 3/3 |
| T3 | drv 1→0 → 总线回高 | ✅ 2/2 |
| T4 | 从设备拉低 → IOBUF 采样到低；从设备释放 → 回高 | ✅ 3/3 |
| T5 | SCL/SDA 独立工作 | ✅ 2/2 |
| T6 | START（SCL 高时 SDA 下降）、数据变化、STOP（SCL 高时 SDA 上升） | ✅ 4/4 |

```
=== RESULT: checks=18 errors=0 ===
=== R2 SIM: PASS ===
```

### 1.3 过程中的一次真实修正
首轮 17/18，T6c FAIL。排查后确认是**测试台序列写错**（STOP 前期望 SDA 低，但序列已提前释放 SDA），
不是 IOBUF 行为问题。修正序列后 18/18。**如实记录，未通过放宽断言取得 PASS。**

### 1.4 仿真产物
| 文件 | SHA256 |
|---|---|
| `tb_iobuf_od.v` | `fd65709573d9c6e392d46f1f1c8c1733c7a37819e784b0a22c82cd641683ca8c` |
| `sim_final.log` | `c21bee2db5da0e651d57df93f269c1acede92b77bc8cb2b6699ae65943a9184f` |

**结论：显式 IOBUF 的开漏语义在仿真层面已正确实现。** 这是本轮最确定的成果。

---

## 2. 第二部分：干净基线重建 —— 工程已建立

### 2.1 起点（严格按要求）
```
源：E:\KVI2C_1019A   （已验证基线克隆，非任何历史副本）
新：E:\KVHAP_2F_IOBUF_FIX_R2
```
**未从 `KVHAP_2E` / `KVHAP_2E_PULLUP_TEST` / `KVHAP_2F_IOBUF_FIX` 继续打补丁。**

### 2.2 单一 RTL 来源
`E:\KVHAP_2F_IOBUF_FIX_R2\rtl_r2\` —— **唯一一套功能 RTL，共 26 个文件**
（含 25 个 .v/.sv + 1 个 .xdc；无 `rtl_pad`/`rtl_haptic`/`rtl_pullup` 等历史目录）

### 2.3 RTL MANIFEST（逐文件 SHA256）

| 文件 | 字节 | SHA256 |
|---|---|---|
| `axi_status_regs.v` | 11757 | `57b15b840fc6104c47bf1ec5e19aab95bbd61609ea080f5966fdefd5bde91c2c` |
| `block_refresh_buffer.v` | 10231 | `ff873640f523c1be3170c2f1da96f61dda61222f488bdb564fdf2cc4787427ec` |
| `candidate_pmod.xdc` | 700 | `06441e84a1aec5ece703a8d79adeca634b2e34184e69f88ad4db9272d7104f53` |
| `dirty_detector.v` | 8332 | `525c1401db9afba8ee1605e2f6801282208b8a4b328d298280f62fa0931adbe8` |
| `duty_lut_rom.v` | 1850 | `96ff1cdce67dbcfc21e5c12782bc608dc135f29d3b6ab89e1f4c0403734f4122` |
| `failsafe_timer.v` | 6125 | `e9f731463c98578b7038b6584256c5a399771ae14bff68d5ff2af70bbb45d90f` |
| `fault_recovery_ctrl.v` | 2518 | `c622752364dd60a493b731c7aa54ba6cfe04bedec52148bbcb1428cf5586fe15` |
| `haptic_oe_guard.v` | 5980 | `12fa6ca51b8cee00519333353b166ba91fe3e173dbf3e3a1e3a9a14184cfa853` |
| `i2c_fault_monitor.v` | 5664 | `078c48cff70e2dfc5e398ed7a7e416a0be79812c3427ef6111e9d5ab1d9e5f6c` |
| `i2c_master.v` | 13713 | `b0282ba70f368f761036b52729b276478225fe388140a6065dd4d20b9f75ccae` |
| `i2c_open_drain_guard.sv` | 1422 | `fa63d810ae301d81f9646bc0cbadd4a2ff84b670f5003e0aa15686389345e241` |
| `i2c_transaction_builder.v` | 6318 | `cafbc7be5851751a540190e02267dc55adba2b2e8ad30caf7bf57c9931aced1d` |
| `kv260_haptic_iobuf_wrapper.v` | 4454 | `cc12324da36adbf92e1bded61dde3d8efb4d2cb98b40e07dbc48f1c1b117ad83` |
| `kv260_haptic_pipeline.v` | 27560 | `2914918cd12b1d53fd77b9411a1690311e92741e91db6bba86719adcbc921410` |
| `kv260_haptic_top.v` | 14804 | `a6de73db95e16cb44b12638587ffca01c99f5434298f0f062b456da127f06cd4` |
| `kv260_i2c_bringup_top.v` | 14160 | `b78d04e09c48f8fcb9dfcb21867a7aad750176fe1532add7c390df744b5da3e7` |
| `kv260_perception_axi_wrap.v` | 6074 | `57e635a9b73580c19d10e492789a3d63165ba362c33e385f1361723a4c0a2052` |
| `matrix_buffer_axi.v` | 16021 | `53dc927cdfe4a1c098331910ae86be8b43dd722cf53abee6e46307210cb67397` |
| `matrix_buffer.v` | 4516 | `bf7564d6b3d3fdc28b76655587f434efc371e87abc292c13514c3108fcf9a394` |
| `matrix_map_rom.v` | 4115 | `5d9ed61f2c48dcc3cdc554396d0f786daef988af1599b21b4569a0d96cc2116b` |
| `pca_init_sequencer.v` | 17190 | `a245e27123b9ad749cae6dddfe005061d46d25eb7035dbef0935b17826c1eeaf` |
| `pca9685_burst_builder.v` | 9247 | `3fd532529719775a069747cbb69301b9f6191dfe18b1066cbf1c604f7eea850a` |
| `scheduler_fsm.v` | 13082 | `2c2557057f2a846a2814a81a4ec6ca1d2f6bf0806df9685cfda36ef66090d8eb` |
| `tactile_render_top.v` | 25444 | `ab16f6327b9e8256294b6b254f58234bd9b469007d2c6bb6688f813521cc6320` |
| `tactile_v12_ctrl.v` | 10219 | `d12212aa568e3f10ff97fb97ae70cbc7f0d63ccde8ecec6e91cf82be0350fea8` |
| `tca9548_inject.v` | 12308 | `6d51f4a3c058e488ba5b36a485dbca5c28f98695b8d7b6c1135192557410fb78` |

Manifest 文件：`E:\KVHAP_2F_IOBUF_FIX_R2\RTL_MANIFEST.json`

### 2.4 保留项（按要求未改动）
- PS 配置：未动 BD 的 `zynq_ultra_ps_e_0`
- AFI = 128-bit：沿用基线 `PSU__MAXIGP2__DATA_WIDTH=128`
- PL0 = 100 MHz：沿用基线
- 基线三个从设备与地址：`iic_probe@0x9D000000`、`diag_gpio@0x9E000000`、`M_AXI_PERCEPTION@0x9F000000`
- Haptic 地址：**`0x9C000000`，64 KiB**
- Phase 2E 焊盘接管：保留（基线 IOBUF 已删除，`iic_*` 仅作回读）
- **`tran` 已消除**（`kv260_i2c_bringup_top.v` 内已确认无 `tran (` 语句）
- 显式 IOBUF 位于 `kv260_haptic_iobuf_wrapper` 的物理 I/O 边界

---

## 3. 第三部分：Vivado 验收 —— ❌ 失败

### 3.1 实际结果
```
PURGED=15
SRC_COUNT=50+13
XDC=E:/KVHAP_2F_IOBUF_FIX_R2/rtl_r2/candidate_pmod.xdc
ERROR: [BD 5-106] Arguments to the connect_bd_intf_net command cannot be empty.
ERROR: [Common 17-39] 'connect_bd_intf_net' failed due to earlier errors.
```

### 3.2 失败点
`connect_bd_intf_net ... [get_bd_intf_pins haptic_fix/s_axi]`
→ **新建 BD cell `haptic_fix` 上不存在 `s_axi` 接口引脚**，
说明 Vivado 未能从 `kv260_haptic_iobuf_wrapper.v` 的 `X_INTERFACE_INFO` 属性识别 AXI4-Lite 接口。

### 3.3 因此以下验收项**全部未取得结果**
| 验收项 | 要求 | 结果 |
|---|---|---|
| BD Validate | 通过 | ❌ 未到该步 |
| Synthesis | 通过 | ❌ 未执行 |
| Implementation | 通过 | ❌ 未执行 |
| Timing WNS ≥ 0 / TNS = 0 | — | ❌ 无数据 |
| DRC | 无 MDRV-1 | ❌ 无数据 |
| **IOBUF = 2** | — | ❌ **无数据** |
| 目标引脚 OBUF 独立驱动 = 0 | — | ❌ 无数据 |
| H12 = SCL / B10 = SDA | — | ❌ 无数据 |
| PULLUP = TRUE | — | ❌ 无数据 |
| IOBUF 的 I/T/O/IO 实际连接 | 逐条检查 | ❌ **未做** |

### 3.4 按失败处理规则的动作
> "如果 Vivado 再次崩溃，保存完整日志和崩溃位置，不在损坏工程上反复 remove_files"

本轮**未崩溃**（是 Tcl 报错退出），但已按同等级处理：
- **立即停止，未在 R2 工程上做任何 `remove_files` 重试**
- 完整日志保留：`E:\KVHAP_2F_IOBUF_FIX_R2\build_r2.log`
- 未进行无新证据的补丁迭代

---

## 4. 下一步（有明确新假设，不是盲目重试）

### 4.1 待验证的假设
`kv260_haptic_iobuf_wrapper.v` 的 AXI 接口未被 BD 识别，最可能原因：
**该文件在 `rtl_r2` 中的 `X_INTERFACE_INFO` 属性块与 Vivado 2025.2 的模块参考解析不兼容**
（例如属性需紧邻端口声明、或 BD 参考要求接口端口命名规范 `s_axi_*`）。

### 4.2 最小验证实验（不重建工程）
在 Vivado 中**只读**执行：
```tcl
open_project E:/KVHAP_2F_IOBUF_FIX_R2/kv260_i2c_bringup.xpr
create_bd_design scratch
create_bd_cell -type module -reference kv260_haptic_iobuf_wrapper probe
report_property [get_bd_cells probe]
get_bd_intf_pins probe/*      ;# 看是否识别到 s_axi
get_bd_pins probe/*
```
**这能在动 BD 之前确定是"接口没识别"还是"cell 没创建成功"。**

### 4.3 若确认接口未识别
对 `kv260_haptic_iobuf_wrapper.v` 做**属性块位置校正**（把 `X_INTERFACE_INFO` 紧贴端口声明、
确保 `s_axi` 所有成员连续且命名规范），然后**在全新工程副本**中重试 —— 不在 R2 上反复改。

### 4.4 仍待解决的两个独立问题（与本次构建无关）
1. **J2.1/J2.2 ↔ H12/B10 映射从未确认**（XDC 依据是 HDA11/HDA15，不是 J2）
2. **Bank45 实际 VCCO 未实测**

---

## 5. 剩余风险

| # | 风险 | 说明 |
|---|---|---|
| 1 | 修复未通过综合验证 | IOBUF 是否保留、是否 IOBUF=2，无数据 |
| 2 | BD 接口识别问题 | 阻塞构建，需先定位 |
| 3 | J2 映射未确认 | 即使电平正常也可能测错引脚 |
| 4 | Bank45 VCCO 未测 | LVCMOS33 前提未确认 |
| 5 | 仿真≠硬件 | 仿真 PASS 只证明逻辑，不证明时序/驱动能力 |
| 6 | 片内 PULLUP ~25 kΩ | 对 400 kHz 上升沿偏慢，接真实器件需外接 4.7 kΩ |

---

## 6. 边界

- ✅ 未修改 AXI 地址映射（仍 0x9C000000 / 64 KiB）
- ✅ 未修改 PS 配置（AFI 128-bit / PL0 100 MHz 沿用基线）
- ✅ 未修改矩阵控制逻辑、PCA9685 初始化 FSM、调度器
- ✅ 未修改原始冻结工程（`KV260_Haptic_Port_20261101\rtl` 冻结 RTL **0/21**）
- ✅ 未连接 PCA9685
- ✅ **未加载任何新 bitstream**
- ✅ 未生成未核验的硬件接线结论
