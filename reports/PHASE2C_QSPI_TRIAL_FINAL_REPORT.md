# Phase 2C-QSPI 试启动 —— 最终报告（含硬性阻断结论）

**日期**：2026-11-02  **结论**：**QSPI Image B 写入成功且内容正确，但 K26 Image Selector 拒绝启动自定义镜像。
A/B 槽位方案对任意自定义 BOOT.BIN 不可用，需改用 SD 启动模式。**

---

## 0. 验收层级

| 层级 | 状态 |
|---|---|
| 1. 构建成功 | **是** |
| 2. QSPI 写入成功 | **是（逐字节 SHA256 验证通过）** |
| 3. Image B 启动成功 | **否 —— 硬性阻断（Image Selector 拒绝）** |
| 4. PS→PL 实际通信成功 | **未达成** |
| 5. 触觉控制链实际运行 | **未开始** |

---

## 1. 关键数据

| 项 | SHA256 |
|---|---|
| XSA `p2b_fix.xsa` | `a62bbbd4733a9508026f292e93f59e0c2e96947690bc578fa0ccaf267a17eb50` |
| BOOT.BIN（含看门狗） | `97107a21181437800ef06371fcec8cb493d132010adcd4de6f3bee310a2e71d9` |
| QSPI Image A 备份 | `de997410d1171f4ee2d0f19b2cdb621e87492dc5013be0b64ae3581e056542d3` |
| QSPI Image B 备份（写入前） | `de997410d1171f4ee2d0f19b2cdb621e87492dc5013be0b64ae3581e056542d3` |
| Persistent Register 备份 | `e6579b966a6dbf9c05ca36e3c42f5885c02aae6782e5dad7a407153fbd2504bf` |
| Persistent Backup 备份 | `e6579b966a6dbf9c05ca36e3c42f5885c02aae6782e5dad7a407153fbd2504bf` |

---

## 2. Bootgen 分区解析

```
the_ROM_image:
{
    [bootloader, destination_cpu=a53-0] fsbl.elf        (371,064 B, loaded 0x13cc8)
    [pmufw_image]                        pmufw.elf      (503,520 B, loaded 0x1fae0)
    [destination_device=pl]              p2b_fix.bit    (7,797,830 B)
    [destination_cpu=a53-0]              haptic_app.elf (148,656 B, text 36,436)
}
```
镜像 8,059,848 B，magic @0x24 = `58 4e 4c 58` (`XNLX`)，PMUFW 分区长度非零。

---

## 3. MTD 实际映射 与 工具一致性（无冲突）

实际分区（`/proc/mtd`）与 `image_update` 二进制硬编码完全一致：

| 功能 | 实际 MTD | 二进制内路径 | 一致 |
|---|---|---|---|
| Image A | mtd5 (0x00d00000) | `/dev/mtd5` | 是 |
| Image B | mtd7 (0x00d00000) | `/dev/mtd7` | 是 |
| Persistent Register | mtd2 | `/dev/mtd2` | 是 |
| Persistent Backup | mtd3 | `/dev/mtd3` | 是 |
| SHA256 | mtd14 | `/dev/mtd14` | 是 |

工具版本：`xlnx-image-update 1.1-0ubuntu2`，二进制 SHA256 `248b8a5f9251d3748f265e1bf6bdd802bff564e115baf4615b2a40c531aa7f15`。

**任务中提示的 mtd5/mtd7 vs mtd6/mtd8 冲突在本机不存在**，上游 `image_update.c` 的编号不适用于 KV260 BootFW。

---

## 4. QSPI 备份清单

目录：`/home/ubuntu/kv260_phase2c/qspi_backup/`
方法：`dd if=/dev/mtdXro`（只读设备，**未擦除、未写入**）

| 分区 | MTD | 大小 | SHA256 |
|---|---|---|---|
| Image A | mtd5 | 13,631,488 B | `de997410…` |
| Image B | mtd7 | 13,631,488 B | `de997410…` |
| Persistent | mtd2 | 131,072 B | `e6579b96…` |
| Persistent Backup | mtd3 | 131,072 B | `e6579b96…` |

---

## 5. Image B 更新日志（成功）

```
sudo xmutil bootfw_update -i /home/ubuntu/kv260_phase2c/BOOT.BIN.new
Marking last booted image as bootable
Reading Image file
Updating Image B
Marking target image as non bootable
Writing Image to Image B partition
Marking target image as non bootable and requested image
Clearing multiboot register value
... successfully updated to Image B partition
UPDATE_EXIT=0
```

更新后状态：
```
Image A: Bootable
Image B: Non Bootable
Requested Boot Image: Image B
Last Booted Image: Image A
ImageA Revision Info: K26-BootFW-01.02-06140626
ImageB Revision Info: Not defined          <-- 关键线索
```

### QSPI 内容回读验证
从 `/dev/mtd7ro` 读回前 8,059,848 字节：
```
97107a21181437800ef06371fcec8cb493d132010adcd4de6f3bee310a2e71d9  -
```
**与构建产物逐字节一致。QSPI 写入无误。**

---

## 6. MultiBootOffset 与启动结果

### 6.1 实测（软复位 + 断电重启，共 2 次）
```
Zynq MP First Stage Boot Loader
Release 2023.2   Mar 18 2024           <- 原厂 FSBL（不是我们的 2025.2）
MultiBootOffset: 0x40                  <- 0x200000 / 32KB = Image A
Reset Mode      : System Reset
Reset reason    : SOFT
Bootmode: QSPI_MODE
```

**两次都启动 Image A，从未出现我们 FSBL 的任何输出。**

### 6.2 MultiBootOffset 解码

| 镜像 | QSPI 起始偏移 | 32KB 扇区数 | MultiBootOffset |
|---|---|---|---|
| Image A | 0x00200000 | 0x40 | **0x40**（实测值） |
| Image B | 0x00F80000 | **0x1F0** | 0x1F0（期望值，任务书参考值正确） |
| Recovery | 0x01E00000 | 0x3C0 | — |

实测 `0x40` 确认启动的是 Image A。

---

## 7. 硬性阻断根因（决定性证据）

### 7.1 镜像内 BootFW 元数据缺失

| 镜像 | offset 112 (0x70) 处内容 |
|---|---|
| 出厂 Image A（备份） | **`K26-BootFW-01.02-06140626`** |
| 出厂 Image B（写入前备份） | **`K26-BootFW…`** |
| **我们写入的 Image B** | **无 `K26-BootFW` 字符串**，只有 `XNLX`（offset 36 与 87264） |

`xmutil bootfw_status` 的 **`ImageB Revision Info: Not defined`** 就是这个位置读不到 BootFW 元数据。

### 7.2 Persistent Register 结构（实测）
```
41 42 55 4d | 01 00 00 00 | 04 00 00 00 | b9 bc b2 ae
00 01 00 01 | 00 00 20 00 | 00 00 f8 00 | 00 00 e0 01
```
`ABUM` magic；`0x00200000` = Image A 偏移、`0x00F80000` = Image B 偏移、`0x01E00000` = Recovery 偏移 —— 与 MTD 布局完全吻合。

### 7.3 结论

**K26 BootFW 的 A/B Image Selector 只接受"合法 BootFW 镜像"（带 offset 112 的 BootFW 元数据/
revision 标识）。它会对目标分区做校验，校验失败时不进行试启动，直接回落 Image A。**

因此：

> **通过 `xmutil bootfw_update` 向 Inactive Slot 写入任意自定义 BOOT.BIN 的路径，在 K26 BootFW
> 上不可用。这是设计约束，不是操作错误。**

我们已把所有安全前置（备份、SHA 校验、非活动槽位、看门狗）都做对，卡在 BootFW 的镜像校验上。

---

## 8. A/B 回退结果

- **Image A 完好无损**（Bootable，可正常启动 Ubuntu）
- **Image B 为 Non Bootable**，Selector 忽略它
- **未执行 `bootfw_update -v`**（未把裸机应用标记为已验证）
- 板卡当前稳定运行 Ubuntu，`uptime` 正常
- Image B 备份完整保存在 SD 卡上，可随时恢复出厂内容

**安全结论：板卡未被变砖，Image A 完全可用。**

---

## 9. 下一步方案（按推荐度）

### 方案 A（推荐，最快）：SD 卡启动模式
1. SD 卡上**已有** `BOOT.BIN`（我们构建的，`F:\BOOT.BIN`，8,059,848 B）
2. 把 KV260 的启动模式开关 **SW1 切到 SD 启动**
3. 重新上电 → ROM 直接从 SD 读我们的 BOOT.BIN → **完全绕过 A/B Selector**
4. 保留 Image A/B 不动，随时把开关切回 QSPI 即可恢复原状

**这是 KV260 上启动自定义镜像的标准做法，风险最低。**

### 方案 B：构建合法 BootFW 镜像
按 AMD 的 BootFW 打包流程（不是普通 `bootgen`）生成带 BootFW 元数据的镜像。
工作量大，需要对 BootFW 制作流程有完整了解。

### 方案 C：JTAG 启动 + 调试
用 JTAG 加载 FSBL/应用，不依赖任何启动介质。
但不验证完整启动路径，且此前本项目管理要求禁止 JTAG。

---

## 10. 未解决问题

| # | 问题 | 等级 |
|---|---|---|
| 1 | **BootFW Selector 拒绝自定义镜像（本阶段硬阻断）** | **阻断** |
| 2 | PS→PL AXI 通路仍未验证 | 高（原定目标） |
| 3 | 看门狗有效性未实测（应用没跑起来） | 中 |
| 4 | Image B 现为无效镜像（Selector 忽略，无害）；可随时用备份恢复 | 低 |
| 5 | J2 针号映射 / Bank45 VCCO 仍 UNKNOWN | 高 |

---

## 11. 交付物

| 文件 | 说明 |
|---|---|
| `phase2c/boot/BOOT.BIN.wdt` | 含看门狗镜像（已写入 Image B 并验证） |
| `phase2c/boot/haptic_app_wdt.elf` | 应用 ELF |
| `phase2c/src/main_wdt.c` | 应用源码（含 PS SWDT0 看门狗） |
| `kv260_2cqspi/evidence/` | 全部原始输出（audit/tool/mir/bak/upd/imgen/boot/chk/meta） |
| `kv260_2cqspi/evidence/boot2_imageB.log` | 断电重启后完整启动日志 |
| `reports/PHASE2C_QSPI_TRIAL_FINAL_REPORT.md` | 本报告 |

## 12. 边界

- 未修改冻结 RTL
- 未修改 AX7020 原工程
- 未用 `dd`/`flashcp` 手工写 QSPI（`dd` 仅用于**只读**备份）
- 未擦除整个 QSPI、未指定 MTD 编号
- **Image A 未受任何影响**
- 未执行 `-v` 验证标记
- 未连接 PCA9685 / 执行器 / V+
- 未修改供电
