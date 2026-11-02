# Phase 2C 报告 —— 独立启动镜像构建

**日期**：2026-11-01  **结果**：**BOOT.BIN 构建完成并写入 SD（SHA 校验通过）；SD 启动待执行（卡需插回板卡）**

---

## 0. 状态总览

| 阶段 | 状态 |
|---|---|
| 2C-0 环境检查 | 完成 |
| 2C-1 启动平台构建（Platform/FSBL/PMUFW/App/BOOT.BIN） | **完成** |
| 2C-2 SD 写入 | **完成 + SHA 回读校验通过** |
| 2C-2 SD 启动 | **待执行**（SD 卡当前在 PC 读卡器，板卡检测不到） |
| 2C-3 PS→PL AXI 验证 | **待执行**（依赖 2C-2） |

---

## 1. 环境与工具链

| 项 | 结果 |
|---|---|
| Vivado / Vitis | **2025.2**（SW Build 6299465 / 6298600） |
| `xsct.bat` | 存在，**但 XSCT 在 2025.2 已弃用**（每次运行打印 deprecated 警告） |
| `bootgen.bat` | 存在，工作正常 |
| **`aarch64-none-elf-gcc`** | **存在**：`E:\vivado\2025.2\gnu\aarch64\nt\aarch64-none\bin\`，GCC **13.3.0** |
| PetaLinux | **未安装**（不需要，走裸机方案） |

> **纠正上一轮的错误判断**：此前认为裸机工具链不存在，实际是检查路径写错（少了 `nt\`），工具链是完整的。

---

## 2. XSA

| 项 | 值 |
|---|---|
| 路径 | `D:\DevCache\Temp\kv260_p2bfinal\p2b_fix.xsa` |
| 大小 | 1,212,109 B |
| **SHA256** | **`a62bbbd4733a9508026f292e93f59e0c2e96947690bc578fa0ccaf267a17eb50`** |
| 内容 | `p2b_fix.bit`、`psu_init.c/h/tcl`、`p2b_fix_bd.hwh`、`sysdef.xml` —— 完整 |

该 XSA 为**带 POR 复位**的版本（`proc_sys_reset.ext_reset_in` 接常量 0，不依赖 `pl_resetn0`）。

---

## 3. 启动镜像组成

| 分区 | 文件 | 大小 | 说明 |
|---|---|---|---|
| bootloader | `fsbl.elf` | 371,064 B | Vitis 从 XSA 生成，加载长度 0x13cc8 |
| PMUFW | `pmufw.elf` | 503,520 B | 加载长度 **0x1fae0** |
| PL | `p2b_fix.bit` | 7,797,830 B | 我们自己的 haptic 设计，**Bonded IOB = 0** |
| App | `haptic_app.elf` | 137,568 B | 裸机 A53，text 33,993 / data 264 / bss 21,267 |

### BIF

```
the_ROM_image:
{
    [bootloader, destination_cpu=a53-0] fsbl.elf
    [pmufw_image]                        pmufw.elf
    [destination_device=pl]              p2b_fix.bit
    [destination_cpu=a53-0]              haptic_app.elf
}
```

### 构建过程中的一个真实修复
首次 bif 用 `[destination_cpu=pmu]` → `pmufw_length = 0x00000000`，**PMUFW 实际未被打包**。
改用 `[pmufw_image]` 后 `pmufw_length = 0x0001fae0` 正常。若不修复，PMU 固件缺失会导致启动异常。

---

## 4. BOOT.BIN

| 项 | 值 |
|---|---|
| 文件 | `phase2c/boot/BOOT.BIN` |
| 大小 | **8,059,848 B** |
| **SHA256** | **`403e410c56c7af83216407b02b67fc82a0bae77df4c5128871b64694c80ad402`** |
| 大小核算 | 81,096(fsbl) + 129,760(pmfuw) + 7,797,830(bit) + ~34,257(app) ≈ 8.04 MB + 头部 ≈ **8.06 MB ✓** |

### 内容验证（二进制检索特征串）
| 特征串 | 存在 |
|---|---|
| `KV260 Haptic -- Phase 2C` | 是 |
| `2C RESULT` | 是 |
| `FIRST AXI READ SURVIVED` | 是 |
| `CRL_APB PL0_REF_CTRL` | 是 |

**四个分区都确实在 BOOT.BIN 内。**

---

## 5. SD 卡写入与校验

### 目标卡识别
| 项 | 值 |
|---|---|
| 磁盘号 | **Disk 1** |
| 型号 | Mass Storage Device（USB 读卡器） |
| 容量 | **29.12 GB**（可移动介质） |
| 序列号 | `121220160204` |
| 分区 1 | **F:**，FAT32，1024 MB，Active/boot，卷标 `system-boot` |
| 分区 2 | 28,800 MB（ext4 根文件系统，Windows 不可见） |

### 安全性
- **只写入 F: 分区，未触碰 Disk 0（系统 NVMe，931 GB，`IsSystem=True`）**
- F: 原有 `image.fit` / `boot.scr.uimg` / `meta-data` / `network-config` **全部保留未改动**
- **纯新增一个 `BOOT.BIN`，可 100% 回退（删除即可）**

### 写入结果
| 项 | 值 |
|---|---|
| 写入路径 | `F:\BOOT.BIN` |
| 写入大小 | 8,059,848 B |
| **回读 SHA256** | **`403e410c56c7af83216407b02b67fc82a0bae77df4c5128871b64694c80ad402`** |
| 与源文件一致 | **MATCH** |

### 重要发现：SD 卡原本没有 BOOT.BIN
写入前 F: 只有 `image.fit`、`boot.scr.uimg`、`meta-data`、`network-config`。
说明 **KV260 的 BOOT.BIN 在 QSPI Flash**，SD 只提供 Linux 载荷 —— 这正是我们要摆脱的启动路径。

---

## 6. 串口启动日志

### 6.1 板卡当前状态
板卡处于 **U-Boot 提示符**：

```
<INTERRUPT>
ZynqMP>
U-Boot 2023.01 (May 10 2024 - 06:24:11 +0000)
aarch64-xilinx-linux-gcc (GCC) 12.2.0
```

### 6.2 U-Boot 无法识别 SD 卡

```
ZynqMP> mmc list
arasan_sdhci mmc@ff170000: Sdhci card detect state not stable
ZynqMP> mmc dev
MMC Device 0 not found
ZynqMP> ls mmc 0:1
** Bad device specification mmc 0 **
```

**原因**：SD 卡此刻仍在 PC 读卡器中，**板卡上没有卡**。

### 6.3 好消息：不需要改启动模式开关
板卡停在 U-Boot 提示符，可直接从 U-Boot 加载我们的 BOOT.BIN：

```
ZynqMP> fatload mmc 0:1 0x2000000 BOOT.BIN
ZynqMP> bootm 0x2000000
```

这比物理切换 SW1 启动模式开关安全，且完全可逆（失败只是回到 U-Boot）。

---

## 7. PS→PL AXI 实测结果

**尚未执行**（依赖 SD 卡插回板卡 + 启动我们的镜像）。

### 已编译进 BOOT.BIN 的应用逻辑（7 阶段、带保护）

| 阶段 | 内容 |
|---|---|
| STAGE 1 | CPU 运行、UART 存活 |
| STAGE 2 | 读 `CRL_APB PL0_REF_CTRL (0xFF5E00C0)`，打印 CLKACT(bit24) —— **PS 寄存器，永不挂死** |
| STAGE 3 | 打印"即将触碰 0xA0000000，若挂死说明 PS→PL 仍不通" |
| STAGE 4 | **单次** `Xil_In32(MB_CTRL)`；成功则打印 `FIRST AXI READ SURVIVED` |
| STAGE 5 | 读 MB_STATUS / ST_INIT_DONE / ST_ONLINE / ST_FAULT / OE_CTRL；校验 `oe_n==1`（输出关断） |
| STAGE 6 | 矩阵写入 `MB_PEND0=3` → COMMIT → 轮询 COMMIT_ACK（10 万次上限） |
| STAGE 7 | 收尾校验 `oe_n` 仍为 1、`sw_oe_req` 仍为 0 |

**安全设计**：
- **绝不访问 `0xA0003000`**（未映射，访问必挂死）
- 不写 OE 使能位，只读回校验
- 单次受保护读取，失败即停，不循环重试

### 关于"AXI 故障恢复机制"

按要求明确记录：**用户态或裸机软件的 `timeout` 都不能中止阻塞在无响应 AXI 总线上的 MMIO 访问**。
本应用的对策不是"软件超时保护"，而是：
1. **只做单次访问**，不循环探测
2. AXI 读置于 STAGE 4，之前所有输出（STAGE 1-3）已刷出，**挂死时能立刻定位**
3. 依赖**自建 XSA 的 PS 配置**（FSBL 按我们的设计配置 PS）—— 这才是根本解法

---

## 8. 未解决问题与下一步

| # | 问题 | 下一步 |
|---|---|---|
| 1 | **SD 卡在 PC 读卡器里**，板卡检测不到 | **把卡插回 KV260**，重上电 |
| 2 | BOOT.BIN 启动方式 | 在 U-Boot 执行 `fatload mmc 0:1 0x2000000 BOOT.BIN` + `bootm 0x2000000`；或把 SW1 设为 SD 启动 |
| 3 | PS→PL AXI 通路未验证 | SD 启动后看串口 STAGE 4 |
| 4 | XSCT 2025.2 已弃用，`app build` 静默不产出 ELF | 已用**手工 gcc + BSP** 绕过（`-specs=nosys.specs` 解决 `_exit` 未定义）；长期建议迁 `vitis -s` Python API |
| 5 | RTL `CLK_FREQ_HZ` 与真实 PL0 频率 | 本 XSA 的 wrapper 已改为 **100_000_000**（与实测 100 MHz 一致）；I2C 需在 100 MHz 下重算 TICKS 才能 ≤400 kHz |
| 6 | J2 针号映射仍 UNKNOWN | 阻塞真实外设接线 |

---

## 9. 交付物

| 文件 | 大小 |
|---|---|
| `phase2c/boot/BOOT.BIN` | 8,059,848 B |
| `phase2c/boot/boot.bif` | 229 B |
| `phase2c/boot/fsbl.elf` | 371,064 B |
| `phase2c/boot/pmufw.elf` | 503,520 B |
| `phase2c/boot/haptic_app.elf` | 137,568 B |
| `phase2c/src/main.c` | 4,785 B |
| `reports/PHASE2C_FINAL_REPORT.md` | 本文件 |

---

## 10. 边界

- 未修改冻结 RTL
- 未修改 AX7020 原工程
- 未连接 PCA9685 / 执行器 / V+
- **未向系统磁盘（Disk 0, NVMe）写入任何数据**
- SD 卡仅**新增** BOOT.BIN，原内容完整保留，可完全回退
- 未执行 JTAG
