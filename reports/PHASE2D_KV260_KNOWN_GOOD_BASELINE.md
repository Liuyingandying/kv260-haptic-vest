# Phase 2D — KV260 已知良好基线（KNOWN GOOD BASELINE）

**日期**：2026-11-02  **性质**：Phase 0 检索结论

---

## 1. 结论先行（必须纠正的前提）

任务书前提为"我们之前已经实现过 KV260 + Linux 用户态程序 → FPGA 内部寄存器读写"。

**经过对当前工作区与历史归档的完整检索，这一前提不成立：**

> **本工作区内不存在任何"KV260 + 自定义 PL 设计 + Linux 用户态寄存器读写成功"的工程、
> XSA、BIT、DTBO、程序或上板日志。**

只有 AX7020 的同类成果存在，不能混用。

---

## 2. 检索范围与方法

检索根目录：`E:\03_Academics\Competitions\嵌入式比赛`

| 检索项 | 方法 | 结果 |
|---|---|---|
| 全部 `.xsa` | 递归 glob | 仅 3 个：AX7020 ×2（`ax7020_matrix.xsa`、`ax7020_matrix_v16.xsa`）+ 本项目 `p2b_debug_debug.xsa` |
| 全部 `.bit` | 递归 glob | 本项目外仅 `matrix_bd_wrapper.bit`、`matrix_bd_wrapper_v16.bit`（位于 `fpga\16x16\vivado\`，属 **AX7020**，797 KB/745 KB，K26 器件不可能这么大） |
| 全部 `.hwh` / `.bd` / `.xpr` | 递归 glob | 本项目外无 KV260 相关 |
| `.md/.txt` 中 "KV260 + PL 寄存器 / /dev/mem / AXI HPM0_FPD" | 全文正则 | 命中全部来自本项目 Phase 2A/2B/2C/PL_DIAG 报告（**均为失败记录**） |
| UIO / devmem 使用证据 | 全文正则 | 无任何 KV260 上读取 PL 寄存器的程序或日志 |

---

## 3. 唯一真实的 KV260 用户态 PL 访问能力

`kv260_haptic_h0_20261101/evidence/board_state/` 记录了 H0 只读审计时的板端真实状态：

```
04_uio.txt:
  /dev/uio0  /dev/uio1  /dev/uio2  /dev/uio3

02_fpga_state.txt:
  fpga_manager/fpga0/state = operating
  name = Xilinx ZynqMP FPGA Manager

03_dt_overlay.txt:
  overlays = k26-starter-kits_image_1   status = applied

01_xmutil.txt:
  k26-starter-kits  XRT_FLAT  0->0,     <- 原厂 shell 正在运行
  另有 kv260-pspl / kv260-axi-recovery / kv260-iic-bringup / kv260-id-monitor 等 AMD 官方 app（均未加载）
```

并且在 Phase 2D 本轮实测（原厂 PL 运行中）：

```
/dev/uio0..uio3  name = axi-pmon     <- 原厂 Kria shell 的 AXI 性能监视器
```

### 3.1 这说明了什么

**KV260 上确实存在"Linux 用户态可访问的 PL 侧 UIO 设备"，但它们属于原厂 `k26-starter-kits`
PL 设计（axi-pmon ×4），不是我们的设计。**

- 设备节点存在（`ls -l /dev/uio*` 有输出）—— 这是真实的
- **但没有任何证据表明曾对这些设备做过读写，也没有任何自定义 PL 设计被成功访问过**

### 3.2 可以确认的"已知良好"事实（有证据）

| 事实 | 证据来源 |
|---|---|
| KV260 revB 可正常启动 Ubuntu 24.04（kernel 6.8.0-1015-xilinx） | 多次串口启动日志 |
| 原厂 `k26-starter-kits` PL 通过 `xmutil` 正常加载，`fpga_manager` = operating | H0 审计 + 本轮 |
| 原厂 PL 设计向 Linux 暴露 4 个 `axi-pmon` UIO 设备 | H0 审计 + 本轮 |
| `xmutil loadapp` 可以把**任意** BOOT.BIN/bit.bin 加载进 PL 并挂载 DT overlay（不报错） | 本项目 Phase 2B/2C 实测 |
| PS 侧 PL0/PL1 时钟门控（CLKACT bit24）为 1 | 本轮 `busybox devmem 0xFF5E00C0` 实测 |

### 3.3 从未被验证过的（不能声称）

- 自定义 PL 设计的 AXI 从设备被 PS 成功读写
- 我们的 `0xA0000000` 窗口在真实硬件上可访问
- Linux 用户态程序控制我们的触觉矩阵

---

## 4. 与 AX7020 成果的严格区分（不得混用）

| 项 | AX7020 | KV260 |
|---|---|---|
| 器件 | `xc7z020`（纯 FPGA） | `xck26-sfvc784-2LV-c`（MPSoC，含 PS） |
| PS | 无 | 有（Cortex-A53） |
| 成果 | 16×16 触觉矩阵 RTL、I²C、PCA9685 初始化、21 项仿真、Vivado 实现全部通过 | RTL 移植/仿真/实现通过；**PS→PL 真实通信从未通过** |
| 证据位置 | `fpga/16x16/` | `KV260_Haptic_Port_20261101/` |

**KV260 的 RTL 是从 AX7020 移植并重新验证的（Phase 1A–1G-V），但"RTL 通过" ≠ "PS→PL 通路通过"。**

---

## 5. 对 Phase 1 的直接影响

因为不存在"旧的已验证 KV260 XSA"，**phase 1 要求的"新旧 XSA 对比"没有对比对象**。

能够做的、也是更有价值的对比是：

> **【我们 XSA 要求的 PS 配置】 vs 【板端实际运行的 PS 配置】**

已完成，见 `PHASE2D_FINAL_REPORT.md` 第 3 节。结论：
**我们 XSA 的 `psu_init` 从未被写入 PS**（PL0 分频器要求 11，实测 10）。

---

## 6. 检索产物清单

| 路径 | 内容 |
|---|---|
| `kv260_haptic_h0_20261101/evidence/board_state/` | H0 只读审计原始证据（uio / fpga state / overlay / xmutil / board model） |
| `kv260_2d/evidence/ps1/` | 本轮实测 PS 寄存器 + UIO 状态 |
| `fpga/16x16/vivado/*.xsa`, `*.bit` | **AX7020** 产物（不得当作 KV260 成果） |

---

## 7. 诚实结论

**"找回已验证的 KV260 PS→PL 通路"这件事无法完成，因为它从未存在过。**

之前每一个 KV260 的 PS→PL 尝试都挂死了（Phase 2B-Lite / 2B-HW / PL_DIAG round 1&2 / 2C-QSPI），
且每次都被如实记录为 FAIL。

因此 Phase 2 不能"复用已验证平台"，只能**先建立**一条已验证通路。
