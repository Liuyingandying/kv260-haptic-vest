# Phase 2F-R2 接口诊断报告

**日期**：2026-11-02  **方式**：只读（未改 RTL、未改 R2 工程、未综合/实现、未上板）
**结论**：**情况 C —— AXI 接口识别完全正常；失败原因是 BD 里 `smartconnect_0` 只有 3 个 MI，
不存在 `M03_AXI`。最小修复为一行 Tcl。**

---

## 0. 三选一结论

| 情况 | 判定 | 证据 |
|---|---|---|
| **A. wrapper 未正确创建** | ❌ 排除 | `CREATE_OK`，21 个引脚全部生成 |
| **B. wrapper 已创建但 AXI 未识别** | ❌ 排除 | `INTF_COUNT=1`，`INTF=s_axi TYPE=ip` |
| **C. 接口已识别，BD 连接脚本引用错误** | ✅ **成立** | `smartconnect_0` 无 `M03_AXI` |

---

## 1. 实际识别到的接口与端口（实证输出）

在临时副本 `E:\KVHAP_2F_R2_DIAG` 的 scratch BD 中执行：

```tcl
create_bd_design scratch
create_bd_cell -type module -reference kv260_haptic_iobuf_wrapper probe
get_bd_intf_pins probe/*
get_bd_pins probe/*
```

**实际输出：**
```
FILES=E:/KVHAP_2F_R2_DIAG/rtl_r2/kv260_haptic_iobuf_wrapper.v
TOP=kv260_i2c_bringup_top
CREATE_OK
INTF_COUNT=1
  INTF=s_axi TYPE=ip
PIN_COUNT=21
  PIN=aclk            DIR=I
  PIN=aresetn         DIR=I
  PIN=i2c_scl_io      DIR=IO
  PIN=i2c_sda_io      DIR=IO
  PIN=s_axi_araddr    DIR=I
  PIN=s_axi_arready   DIR=O
  PIN=s_axi_arvalid   DIR=I
  PIN=s_axi_awaddr    DIR=I
  PIN=s_axi_awready   DIR=O
  PIN=s_axi_awvalid   DIR=I
  PIN=s_axi_bready    DIR=I
  PIN=s_axi_bresp     DIR=O
  PIN=s_axi_bvalid    DIR=O
  PIN=s_axi_rdata     DIR=O
  PIN=s_axi_rready    DIR=I
  PIN=s_axi_rresp     DIR=O
  PIN=s_axi_rvalid    DIR=O
  PIN=s_axi_wdata     DIR=I
  PIN=s_axi_wready    DIR=O
  PIN=s_axi_wstrb     DIR=I
  PIN=s_axi_wvalid    DIR=I
```

**`s_axi` 接口被 Vivado 正确识别为 AXI 接口（TYPE=ip），两个 inout 焊盘也正常出现。**

---

## 2. wrapper 源码检查（第 2 项）

文件：`E:\KVHAP_2F_IOBUF_FIX_R2\rtl_r2\kv260_haptic_iobuf_wrapper.v`
SHA256：`cc12324da36adbf92e1bded61dde3d8efb4d2cb98b40e07dbc48f1c1b117ad83`

### 2.1 端口完整性（全部存在）
| 信号 | 行号 | 声明 |
|---|---|---|
| `aclk` | L9 | `input wire` |
| `aresetn` | L12 | `input wire` |
| `s_axi_awaddr [15:0]` | L13 | `input` |
| `s_axi_awvalid` | L14 | `input` |
| `s_axi_awready` | L15 | `output` |
| `s_axi_wdata [31:0]` | L16 | `input` |
| `s_axi_wstrb [3:0]` | L17 | `input` |
| `s_axi_wvalid` | L18 | `input` |
| `s_axi_wready` | L19 | `output` |
| `s_axi_bresp [1:0]` | L20 | `output` |
| `s_axi_bvalid` | L21 | `output` |
| `s_axi_bready` | L22 | `input` |
| `s_axi_araddr [15:0]` | L23 | `input` |
| `s_axi_arvalid` | L24 | `input` |
| `s_axi_arready` | L25 | `output` |
| `s_axi_rdata [31:0]` | L26 | `output` |
| `s_axi_rresp [1:0]` | L27 | `output` |
| `s_axi_rvalid` | L28 | `output` |
| `s_axi_rready` | L29 | `input` |
| `i2c_scl_io` / `i2c_sda_io` | L33-34 | `inout` |

### 2.2 属性块（完整且位置正确）
- L7-8：`aclk` 的 `X_INTERFACE_INFO` + `X_INTERFACE_PARAMETER`（含 `ASSOCIATED_BUSIF s_axi`、`ASSOCIATED_RESET aresetn`）
- L10-11：`aresetn` 的 `X_INTERFACE_INFO` + `X_INTERFACE_PARAMETER`
- L13-29：`s_axi` 全部 17 条 `X_INTERFACE_INFO`
- L30：`s_axi` 的 `X_INTERFACE_PARAMETER`（`PROTOCOL AXI4LITE, DATA_WIDTH 32, ADDR_WIDTH 16`）

### 2.3 与已成功识别的 `kv260_haptic_pad_wrapper.v` 对比
| 项 | pad_wrapper（Phase 2E 成功） | iobuf_wrapper（R2） | 差异 |
|---|---|---|---|
| aclk 属性 | L7-8 相同 | L7-8 相同 | 无 |
| aresetn 属性 | L10-11 相同 | L10-11 相同 | 无 |
| s_axi 属性 | L13-29 相同 | L13-29 相同 | 无 |
| s_axi PARAMETER | L30 | L30 | **完全相同** |
| L30 之后 | 空行 + 注释 + inout | 空行 + 注释 + inout | **结构一致** |

**>>> 属性写法与位置与"曾经成功识别"的版本逐行一致，不是属性问题。**

---

## 3. Vivado 解析检查（第 3 项）

| 检查 | 结果 |
|---|---|
| `get_files *kv260_haptic_iobuf_wrapper.v` | ✅ 命中 1 个：`E:/KVHAP_2F_R2_DIAG/rtl_r2/kv260_haptic_iobuf_wrapper.v` |
| 顶层 | `kv260_i2c_bringup_top`（正确） |
| 同名模块 / 重复源 | ❌ 无（R2 的 `rtl_r2` 为单一一套；VD 自动产物重复属正常） |
| `create_bd_cell -reference` | ✅ `CREATE_OK` |
| 接口识别 | ✅ `s_axi` TYPE=ip |

---

## 4. 真正失败原因（第 7 项：情况 C 的具体形式）

### 4.1 原始报错
```
ERROR: [BD 5-106] Arguments to the connect_bd_intf_net command cannot be empty.
```

### 4.2 定位
R2 工程 BD 文件中 `smartconnect_0` 的 MI 接口：

```
"value": "M00_AXI:M01_AXI:M02_AXI"        <-- 只有 3 个
已存在并连接：M00_AXI / M01_AXI / M02_AXI
```

**不存在 `M03_AXI`。**

因此：
```tcl
connect_bd_intf_net [get_bd_intf_pins smartconnect_0/M03_AXI] [get_bd_intf_pins haptic_fix/s_axi]
```
中第一个参数 `[get_bd_intf_pins smartconnect_0/M03_AXI]` **返回空** → 报 "cannot be empty"。

### 4.3 为什么历史上能跑通
`KVI2C_1019A`（干净基线）的 smartconnect 是 **NUM_MI=3**，对应基线的三个从设备：
`iic_probe@0x9D`、`diag_gpio@0x9E`、`M_AXI_PERCEPTION@0x9F`。

而 `KVHAP_2E` / `KVHAP_2E_PULLUP_TEST` 等历史工程里，`NUM_MI` 早在 Phase 2E 就被扩到 **4**，
所以 `M03_AXI` 存在、脚本能跑。

**R2 是从干净基线复制的，`NUM_MI=3` 状态被如实继承 —— 而我的 R2 构建脚本漏了扩容这一步。**

### 4.4 归类
- 不是 A（wrapper 创建正常）
- 不是 B（接口识别正常）
- **是 C 的一种特例：不是"引用写错"，而是"目标接口尚未创建"** ——
  BD 的 `smartconnect_0` 没有被先扩到 4 个 MI。

---

## 5. 最小修复建议（**本轮不执行，等待审核**）

在 `build_r2.tcl` 中，`create_bd_cell ... haptic_fix` **之前**插入一行：

```tcl
set_property CONFIG.NUM_MI 4 [get_bd_cells smartconnect_0]
```

其余脚本保持不变（`M03_AXI` 会在扩容后自动出现）。

### 风险评估
- 该改动只会**增加**一个 MI 端口，不改变已有 M00/M01/M02 的连接
- 不改变地址映射（新增 `0x9C000000` 是唯一新映射）
- 不改变 PS 配置、AFI、PL0 时钟
- **属于最小、可逆的 BD 改动**

### 受影响的其他脚本
同样的遗漏可能存在于任何"从 `KVI2C_1019A` 直接复制"的新工程；
若后续再无干净复制，可忽略。

---

## 6. 尚未验证 / 未做的事项

| 项 | 状态 |
|---|---|
| 修复后 BD Validate | ❌ 未执行 |
| 综合 / 实现 | ❌ 未执行 |
| IOBUF = 2 的网表证据 | ❌ 无数据 |
| MDRV-1 = 0 | ❌ 无数据 |
| H12/B10 映射、PULLUP、WNS/TNS | ❌ 无数据 |
| IOBUF 的 I/T/O/IO 实际连接 | ❌ 未检查 |
| J2 ↔ H12/B10 物理映射 | ❌ 仍 UNKNOWN |
| 实物电压 | ❌ 未测 |

---

## 7. 边界

- ✅ 只读诊断：未修改 RTL
- ✅ 未修改 `E:\KVHAP_2F_IOBUF_FIX_R2`（诊断在临时副本 `E:\KVHAP_2F_R2_DIAG` 中）
- ✅ 未执行综合 / 实现 / bitstream
- ✅ 未修改冻结 RTL
- ✅ **未进行任何板端操作**
- ✅ 未做第二轮盲目补丁

**诊断完成，已停止，等待审核。**
