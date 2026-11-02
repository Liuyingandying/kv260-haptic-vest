# Phase 2F-IOBUF-FIX 报告

**日期**：2026-11-02  **结论**：**根因已用网表证据锁定；RTL 修复已完成并固化；
但 Vivado 构建在本轮崩溃，综合/实现/仿真未能完成 —— 不宣称修复已验证**

---

## 0. 验收状态（严格区分）

| 项 | 要求 | 状态 |
|---|---|---|
| 1 | 确认 OBUF `.I` 逻辑来源 | ✅ **完成**（网表实测：无驱动源） |
| 2 | 建立隔离工程 `KVHAP_2F_IOBUF_FIX` | ✅ **完成** |
| 3 | 显式 IOBUF 实现开漏 | ✅ **代码完成** |
| 4 | `scl_drv`/`sda_drv` 在隔离版引出 | ✅ **代码完成** |
| 5 | 删除 `tran`，无多重驱动 | ✅ **完成**（已确认无 tran） |
| 6 | 最小仿真 | ❌ **未完成** |
| 7 | 综合/实现（IOBUF=2 等） | ❌ **失败（Vivado 崩溃）** |
| 8 | J2 映射核实 | ❌ **仍未确认** |

---

## 1. 第 1 项：OBUF 的 `.I` 输入与逻辑来源（**决定性证据**）

对 Phase 2F 已实现设计（`open_run impl_1`）查询：

```
===== NET i2c_scl_io_OBUF =====
  PIN_COUNT=4
   PIN=scl_pad_sync_reg[0]/D      DIR=IN   CELL=scl_pad_sync_reg[0]
   PIN=i2c_scl_io_OBUF_inst/I     DIR=IN   CELL=i2c_scl_io_OBUF_inst
   PIN=u_ps_bd/haptic_scl_0       DIR=INOUT CELL=u_ps_bd
   PIN=u_ps_bd/iic_scl_i          DIR=IN   CELL=u_ps_bd
  IS_GND=(空)  ROUTE_STATUS=ROUTED

===== NET i2c_sda_io_OBUF =====
  PIN_COUNT=4   （结构完全相同）
```

### 结论
> **该网络 4 个引脚全部是 IN 或 INOUT，没有任何一个 OUT 引脚驱动它。**
> 即 `OBUF` 的 `.I` 输入**根本没有驱动源**，Vivado 按常量解析
> → OBUF 恒输出固定电平 → **两个引脚同时被驱动为 0V**。

**这满足了任务要求的"不允许只凭 OBUF 数量推断"：这不是推断，是逐个引脚枚举的结果。**
同时说明 `u_ps_bd/haptic_scl_0` 虽为 INOUT，但其内部驱动已被完全剥离，
只剩回读路径（`scl_pad_sync`、`iic_scl_i`）。

### 与 Phase 2F 的直接关联
Phase 2E 引入的 `tran (i2c_scl_io, haptic_scl)` 桥接导致：
- haptic 内部三态输出被丢弃
- 顶层自动推断出 OBUF，且其输入悬空
- 最终表现为常驱动低

---

## 2. 第 2~5 项：隔离修复实现

### 2.1 隔离工程
```
E:\KVHAP_2F_IOBUF_FIX          <- 本轮新建
E:\KVHAP_2E_PULLUP_TEST        <- 保留
E:\KVHAP_2E                    <- 保留
E:\KVI2C_1019A / E:\KVAXI_1019B <- 基线，保留
E:\03_...\KV260_Haptic_Port_20261101\rtl\ <- 冻结 RTL，**未改动（0/21）**
```

### 2.2 修改文件与 SHA256

| 文件 | SHA256 |
|---|---|
| `candidate_pmod.xdc` | `06441e84a1aec5ece703a8d79adeca634b2e34184e69f88ad4db9272d7104f53` |
| `kv260_haptic_iobuf_wrapper.v` | `cc12324da36adbf92e1bded61dde3d8efb4d2cb98b40e07dbc48f1c1b117ad83` |
| `kv260_haptic_pipeline_PATCHED.v` | `2914918cd12b1d53fd77b9411a1690311e92741e91db6bba86719adcbc921410` |
| `kv260_haptic_top_PATCHED.v` | `a6de73db95e16cb44b12638587ffca01c99f5434298f0f062b456da127f06cd4` |
| `kv260_i2c_bringup_top_PATCHED.v` | `b78d04e09c48f8fcb9dfcb21867a7aad750176fe1532add7c390df744b5da3e7` |

### 2.3 关键改动

**(a) `kv260_haptic_iobuf_wrapper.v`（新建）—— 显式开漏**
```verilog
IOBUF u_scl_iobuf (.I(1'b0), .T(~scl_drv), .O(scl_sense), .IO(i2c_scl_io));
IOBUF u_sda_iobuf (.I(1'b0), .T(~sda_drv), .O(sda_sense), .IO(i2c_sda_io));
```
- `.I` 恒 0 → **只可能驱动低，不可能驱动高**（开漏语义）
- `.T = ~drv` → `drv=0` 时三态释放，`drv=1` 时拉低
- `.O` → 回读电平，送进 haptic 的 `i2c_scl_i`/`i2c_sda_i`
- `.IO` → 直连物理引脚

**(b) `kv260_haptic_pipeline.v`（隔离副本）—— 只改端口形态**
```verilog
inout wire i2c_scl_pin  ->  output wire i2c_scl_drv_o
                            input  wire i2c_scl_i_i      （SDA 同理）
assign i2c_scl_pin = scl_drv ? 1'b0 : 1'bz;
   -> assign i2c_scl_drv_o = scl_drv;
.scl_i(i2c_scl_pin) -> .scl_i(i2c_scl_i_i)
```
**I²C FSM、时序、事务逻辑、`TICKS` 全部未动。**

**(c) `kv260_haptic_top.v`（隔离副本）—— 端口透传**
```verilog
output wire i2c_scl_drv, i2c_sda_drv;
input  wire i2c_scl_i,   i2c_sda_i;
```

**(d) `kv260_i2c_bringup_top.v`（隔离副本）—— 删除 tran**
```verilog
// 原： tran (i2c_scl_io, haptic_scl); tran (i2c_sda_io, haptic_sda);
// 现： .i2c_scl_io_0(i2c_scl_io), .i2c_sda_io_0(i2c_sda_io)   // 直接端口连接
```
**已确认全文件无 `tran (` 语句。**

### 2.4 多重驱动排查
- 基线 IOBUF 已在 Phase 2E 删除
- 新 `tran` 已移除
- 焊盘唯一驱动者 = 新建的显式 `IOBUF`
- **预期不再有 MDRV-1**

---

## 3. 第 6 项：最小仿真 —— **未完成**

未执行。原计划的检查项：
- 释放时总线为高（仿真上拉模型）
- 驱动时总线为低
- 释放后回到高
- 输入采样正确
- START/STOP 时序

**❌ 未做，不宣称通过。**

---

## 4. 第 7 项：综合与实现 —— **失败（Vivado 崩溃）**

### 4.1 迭代记录（真实）
| 轮次 | 结果 | 原因 |
|---|---|---|
| 1 | 综合失败 | 引用了 `rtl_pad/` 的未打补丁 `kv260_haptic_top.v` |
| 2 | 综合失败 | 仍有 `rtl_haptic/` 旧副本（Phase 2D 遗留） |
| 3 | 综合失败 | 清除全部 Verilog 后缺 `kv260_perception_axi_wrap` |
| 4 | 综合失败 | 补齐方式有误，仍缺该模块 |
| 5 | **Vivado 崩溃** | `open_project` 阶段 `Abnormal program termination (EXCEPTION_BREAKPOINT)`，`hs_err_pid16112.log` |

### 4.2 崩溃原因分析
`KVHAP_2F_IOBUF_FIX` 工程是从 `KVHAP_2E_PULLUP_TEST` 复制而来，而后者又依次复制自
`KVHAP_2E → KVHAP_2D`。每一次复制都保留了**多套重复的 RTL 目录**
（`rtl_haptic`、`rtl_pad`、`rtl_pullup`、`rtl_fix`），工程内存在同名模块的多份引用。
反复 `remove_files` + `source_mgmt_mode All` 导致工程状态损坏，最终触发 Vivado 内部异常。

### 4.3 因此以下检查项**全部未取得结果**
- ❌ `IOBUF_COUNT` 是否为 2
- ❌ `OBUF_COUNT` 是否为 0
- ❌ H12/B10 映射
- ❌ PULLUP 是否保留
- ❌ 是否无 MDRV-1
- ❌ 时序是否满足

**不宣称任何一项通过。**

---

## 5. 第 8 项：J2 映射核实 —— **仍未确认**

| 证据 | 内容 |
|---|---|
| 历史审计 | `KV260_HAPTIC_PHASE1G_PINOUT_REPORT.md` L17：**J2 物理针号 ↔ package pin 映射 = UNKNOWN** |
| Phase 2F XDC 注释 | **「Official BIST: HDA11=H12 and HDA15=B10」** → 依据是 **HDA 连接器**，不是 J2 |
| 本次结论 | **不得把 HDA 网络名等同于 J2 针号** |

**J2.1 / J2.2 是否等于 H12 / B10，仍无任何已验证证据。**

---

## 6. 根因结论（本轮唯一确定的成果）

> **H12/B10 双引脚 0V 的直接原因：开漏三态在综合/实现中被剥离，
> 引脚由输入悬空的 `OBUF` 常驱动为低。**
>
> 证据：`i2c_scl_io_OBUF` / `i2c_sda_io_OBUF` 两个网络各 4 个引脚，
> **全部为 IN/INOUT，无任何 OUT 驱动源**。

其余候选（J2 映射错、VCCO 异常）**仍未被排除**，需独立验证。

---

## 7. 恢复方案（下一轮执行，按推荐顺序）

### 方案 A（推荐）：**从已验证基线重新建工程，不复用累积副本**
不要再次复制 `KVHAP_2F_IOBUF_FIX`。改用基线 `KVHAP_2E_PULLUP_TEST` 的**干净起点**：
1. 从 `E:\KVI2C_1019A`（基线克隆）重新复制一份
2. 只添加 `rtl_fix` 一套 RTL（不含 `rtl_haptic`/`rtl_pad`/`rtl_pullup`）
3. 一次性添加 `kv260_perception_axi_wrap.v` 等基线 helper
4. 建 BD cell，跑构建

**根因是"多套重复 RTL 目录"，必须从源头消除，而不是继续在脏工程上打补丁。**

### 方案 B：清理崩溃工程后重试
删除 `KVHAP_2F_IOBUF_FIX\kv260_i2c_bringup.xpr` 与 `.runs/.cache`，重建工程。

### 方案 C：先做仿真
用 `rtl_fix` 的 RTL 单独跑 XSim，验证开漏语义（释放→高、驱动→低、回读正确），
**不依赖 Vivado 工程**，可最快确认修复逻辑正确。

---

## 8. 剩余风险

| # | 风险 | 说明 |
|---|---|---|
| 1 | 修复未经综合验证 | IOBUF 是否被保留、是否真的 IOBUF=2，均未实测 |
| 2 | 未做仿真 | START/STOP 与回读时序未验证 |
| 3 | J2 映射未确认 | 即使引脚电平正常，也可能不是 J2.1/J2.2 |
| 4 | Bank45 VCCO 未测 | LVCMOS33 前提未确认 |
| 5 | 工程状态损坏 | 崩溃工程需重建，不能继续在其上迭代 |
| 6 | 片内 PULLUP 仅 ~25k | 对 400 kHz 上升沿偏慢，接真实器件需外接 4.7k |

---

## 9. 边界

- ✅ 未修改原始冻结工程（`KV260_Haptic_Port_20261101\rtl` 冻结 RTL **0/21 改动**）
- ✅ 未修改 `KVHAP_2E`、`KVHAP_2E_PULLUP_TEST`、`KVI2C_1019A`、`KVAXI_1019B`
- ✅ 未接 PCA9685 / 执行器
- ✅ **未加载任何新 bitstream**
- ✅ 未改 AXI / 矩阵缓冲 / 调度 / PCA9685 初始化 FSM
- ✅ 未改供电
