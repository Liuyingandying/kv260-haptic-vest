# Phase 2B-Lite：KV260 PS 侧最小控制程序与 AXI 冒烟测试

**日期**：2026-11-01  **结果**：软件已交付并编译通过；**AXI 冒烟仿真失败，发现阻断级 RTL 缺陷**

---

## 0. 结论先行

| 项目 | 状态 |
|---|---|
| 工具链 / 运行环境核查 | ✅ 完成 |
| `haptic_regs.h` / `haptic_axi_smoke.c` 编写 | ✅ 完成（寄存器映射全部取自 RTL 源码，未猜测） |
| **交叉编译** | ✅ **通过**（ELF64 AArch64，707,632 B） |
| **AXI 冒烟仿真** | ❌ **FAIL —— 发现冻结 RTL 的地址译码缺陷** |
| XSA 导出 | ⛔ **主动不导出**（见 §6，设计存在已知安全缺陷，不应交付） |
| Vitis Platform | ⛔ 未建立（决定走 Linux 用户态 MMIO，不需要） |
| PS/PL 真实硬件通信 | **NOT VERIFIED** |
| 外部引脚 | ✅ 未使用任何未经确认的映射 |

---

## 1. 软件环境核查

| 项 | 结果 |
|---|---|
| Vivado / Vitis | **2025.2**（SW Build 6299465） |
| Vitis `vitis.bat` | 存在 |
| `aarch64-linux-gnu-gcc.exe` | **存在**（GCC 13.4.0） |
| `aarch64-none-elf-gcc.exe`（裸机） | **不存在** |
| Phase 2A XSA | **不存在**（Phase 2A 只做到 route_design，未导出） |
| 可复用 Vitis Platform | **无**（工程内 0 个 workspace/platform 目录） |
| KV260 运行环境 | **Linux**（H0 审计实测：PL = `k26-starter-kits`，DT model = `ZynqMP KV260 revB`） |

### 方案选择：**Linux 用户态 MMIO（`/dev/mem`）**

理由：
- 板端已有 Linux，无需搭建裸机启动系统（符合"不要为了使用裸机而额外搭建一整套启动系统"）
- 板端**无编译器**（早期已确认），但支持交叉编译的静态二进制
- Vitis 2025.2 未安装裸机工具链 `aarch64-none-elf-gcc`，走裸机需额外安装

---

## 2. 寄存器映射（全部取自 RTL 源码，逐条标注出处）

`phase2b/haptic_regs.h`：

| 基址 | 区域 | RTL 出处 |
|---|---|---|
| 0x0000 | matrix_buffer_axi | `rtl/matrix_buffer_axi.v` |
| 0x1000 | axi_status_regs | `rtl/axi_status_regs.v`（读映射在 L204 `case(araddr_r[6:2])`） |
| 0x2000 | OE 控制 | `rtl/kv260_haptic_top.v` L108-L133 |
| **0x3000** | **未映射** | **禁止访问**（`sel_*` 全 false → ready 恒 0 → AXI 永久等待） |

### matrix_buffer（0x0000）
| 偏移 | 属性 | 含义 |
|---|---|---|
| 0x000 | RW | CTRL: bit0 ENABLE, bit1 COMMIT(W1) |
| 0x004 | RO | STATUS: bit0 ACT_BANK, bit1 COMMIT_ACK(读清) |
| 0x100–0x1FF | W | 待写 bank（每 4 字节一字，单元地址 = `{awaddr[7:2], wcnt}`） |
| 0x200–0x2FF | RO | 活动 bank 读窗口 |

### axi_status_regs（0x1000，字节偏移 = index×4）
| 偏移 | idx | 读含义 | 写含义 |
|---|---|---|---|
| 0x00C | 3 | bit1 = sw_tca_en 回显 | bit0 commit脉冲 / bit1 tca_en / bit2 fault_clear脉冲 |
| 0x024 | 9 | bit0 = sw_force_off | bit0 = sw_force_off |
| 0x028 | 10 | init_done | — |
| 0x02C | 11 | online_mask | — |
| 0x03C | 15 | fault_latched | — |
| 0x054 | 21 | recovery_status（bit6 = 无硬件关断） | — |

### OE 控制（0x2000）
| 位 | 含义 |
|---|---|
| W bit0 | SW_OE_REQ（1 = 请求使能） |
| W bit1 | SW_OE_CLR（1 = 强制请求归零） |
| R bit0 | sw_oe_req |
| R bit1 | **oe_n（1 = OE# 高 = 输出关断）** |
| R bit2 | armed |

---

## 3. 软件交付与编译

| 文件 | 说明 |
|---|---|
| `phase2b/haptic_regs.h` | 寄存器映射 + 安全守卫宏 `HAPTIC_ADDR_IS_SAFE()` |
| `phase2b/haptic_axi_smoke.c` | 冒烟测试主程序（每步输出地址/期望/实际/PASS-FAIL） |
| `phase2b/Makefile` | 交叉编译配置 |
| `phase2b/haptic_axi_smoke` | **交叉编译产物** |

编译结果：
```
ELF magic : ELF   class=2 (64-bit)   machine=183 (AArch64)
size      : 707,632 B (static)
sha256    : 576267ad7db2fa9415a2e3611cb0b8cb40a2cfe046ee46dbcd78123d765c2b44
```

程序内置的安全措施：
- 每次访问前过 `HAPTIC_ADDR_IS_SAFE()`，**0xA0003000 永不访问**（并有主动拒绝演示）
- 所有轮询有硬上限 `POLL_LIMIT`，AXI 挂起时报 TIMEOUT 而非卡死
- **从不写 `OE_CTRL` 的使能位**，只读回校验 `oe_n` 仍为 1
- 不假定 PCA9685 已连接

---

## 4. AXI 冒烟仿真 —— **FAIL，并发现阻断级 RTL 缺陷**

仿真台 `phase2b/tb_axi_smoke.v` 用 PS 的 AXI 主控时序重放与 C 程序**完全相同的寄存器序列**，时钟取 Phase 2A 实测的 90,908,180 Hz。

### 4.1 实测结果（11 项检查，6 项 FAIL）

```
ST_ONLINE_MASK   = 0xxxxxxxxx          <- 读到 X
OE_CTRL raw      = 0x00000000  oe_n=0  <- 复位后应为 oe_n=1
COMMIT poll iters=200000 (超时)  MB_STATUS=0x00000004   <- COMMIT_ACK 从未置位
OE_CTRL final    = 0x00000001  sw_req=1  <- 我从未写过 OE 寄存器！
```

### 4.2 根因（已在冻结 RTL 中确认）

`rtl/kv260_haptic_top.v` L90–L92：

```verilog
wire sel_ctrl = (s_axi_awaddr[13:12] == 2'b00) | (s_axi_araddr[13:12] == 2'b00);
wire sel_sts  = (s_axi_awaddr[13:12] == 2'b01) | (s_axi_araddr[13:12] == 2'b01);
wire sel_oe   = (s_axi_awaddr[13:12] == 2'b10) | (s_axi_araddr[13:12] == 2'b10);
```

**写地址位与读地址位被 OR 在一起**，而不是分别译码。

后果链条（与实测完全吻合）：
1. 读操作后 `s_axi_araddr`/内部锁存仍停在上一次读地址（如 0x2000）
2. 随后对 0x0000 的写：`sel_ctrl=1`（正确）**且 `sel_oe=1`**（错误，因为 araddr 还是 0x2000）
3. L108 `oe_wr_hit = awvalid && wvalid && sel_oe` 成立 → **误写 OE 控制寄存器**
4. 写入值 3（ENABLE|COMMIT）的 bit0 = 1 → **`SW_OE_REQ` 被置 1**
5. 同理，读 0x2000 时 `sel_ctrl` 也为真 → 读 mux 优先选 `ctrl_rdata`（未驱动）→ **读到 X**
6. 对 0x0000 的写被 OE 逻辑吞掉 → `matrix_buffer_axi` 收不到 COMMIT → **COMMIT_ACK 永不置位**

### 4.3 严重性：**安全相关**

- 软件执行一次**合法的矩阵写入**，就可能**意外置位 `SW_OE_REQ`**（请求使能 PCA9685 输出）
- 虽然 OE 守卫还要求 `init_healthy`，但**安全设计不能依赖第二道闸门来掩盖第一道闸门的错误**
- 该缺陷**同样存在于 Phase 2A 的 BD 集成中**（BD 只是把同一模块接起来）

### 4.4 为什么此前的测试没发现

Phase 1B/1D/1E/1G-V 的测试台**始终按"先写后读、且地址单调递增"的顺序访问**，
读地址与写地址的区域位恰好相近或相同，OR 后不产生跨区域误命中。
只有本次按**真实软件访问模式**（先读 0x2000 再写 0x0000）才暴露。

**这也是"21 项 PASS"的又一个局限：测试序列未覆盖真实访问交错。**

---

## 5. 冻结 RTL 处理：**不修改**

本阶段明令禁止修改冻结的 21 个 RTL 文件，且该问题属"真正影响功能或硬件安全"，
故按规则**立即暂停，只记录不修复**。

最小修复建议（**未实施**）：
```verilog
wire sel_ctrl_aw = (s_axi_awaddr[13:12] == 2'b00);
wire sel_sts_aw  = (s_axi_awaddr[13:12] == 2'b01);
wire sel_oe_aw   = (s_axi_awaddr[13:12] == 2'b10);
wire sel_ctrl_ar = (s_axi_araddr[13:12] == 2'b00);
wire sel_sts_ar  = (s_axi_araddr[13:12] == 2'b01);
wire sel_oe_ar   = (s_axi_araddr[13:12] == 2'b10);
// 写通道用 *_aw，读通道用 *_ar，响应 mux 按各自方向选
```
修复后必须重跑：Phase 1B 21 项、Phase 1G-V 全回归、本 AXI 冒烟。

---

## 6. XSA / Platform / 调试镜像

| 项 | 决定 | 理由 |
|---|---|---|
| **XSA 导出** | ⛔ **主动不导出** | 已知地址译码安全缺陷，导出 XSA 等于把一个"合法写入可能误使能输出"的设计交付出去，不合理 |
| Vitis Platform | ⛔ 未建立 | 选择 Linux 用户态 MMIO，不需要 Vitis platform |
| AXI Debug Wrapper | ⚠️ **方案可行但未构建** | 见下 |

### 调试镜像可行性评估（§4 要求）

**结论：技术上完全可以构建一个"零外部 PL 引脚"的安全调试镜像**，做法：

```
kv260_haptic_debug_wrapper
├─ 只保留 AXI 从口（接 BD SmartConnect）+ aclk + aresetn
├─ haptic_top 的 i2c_scl_pin / i2c_sda_pin → 内部接 1'b1（总线释放/空闲，等效上拉）
├─ haptic_top 的 wdog_heartbeat / oe_n → 内部悬空（不引出，不接任何引脚）
└─ 不暴露任何 PACKAGE_PIN，不使用任何未经确认的映射
```

这样生成的 bitstream **外部 PL IOB = 0**，不触碰 J2，OE 物理上不会输出到任何引脚。

**但本阶段未构建**，因为：
1. 地址译码缺陷未修复，构建出的镜像在功能上就是错的；
2. 修复前构建调试镜像没有意义。

---

## 7. 三层结论严格区分

| 层级 | 状态 | 依据 |
|---|---|---|
| **软件编译通过** | ✅ **PASS** | 交叉编译 exit=0，ELF64/AArch64，707,632 B |
| **AXI 仿真通过** | ❌ **FAIL** | 11 项检查 6 项失败，根因是冻结 RTL 地址译码缺陷 |
| **PS/PL 实际硬件通信** | ⛔ **NOT VERIFIED** | 未加载 bitstream、未在板上运行、XSA 未导出 |

**三者互不推导。**

---

## 8. 剩余问题与建议

| # | 问题 | 严重性 |
|---|---|---|
| 1 | **地址译码 OR 缺陷 → 合法写可误置 SW_OE_REQ** | **阻断（安全）** |
| 2 | 修复需改冻结 RTL，须解冻并重跑全回归 | 流程 |
| 3 | 读 0x2000 返回 X（同源缺陷） | 功能 |
| 4 | COMMIT_ACK 不可达（同源缺陷） | 功能 |
| 5 | J2 针号 / Bank45 VCCO 仍 UNKNOWN | 硬件阻塞 |
| 6 | XSA 未导出 | 待缺陷修复后 |

**建议顺序**：解冻 `kv260_haptic_top.v` → 修正读写译码分离 → 重跑 21 项 + 1G-V 全回归 + AXI 冒烟 → 导出 XSA → 才谈上板。
