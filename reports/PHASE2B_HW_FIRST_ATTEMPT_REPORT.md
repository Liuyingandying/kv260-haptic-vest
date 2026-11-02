# Phase 2B 首次真实硬件上板尝试报告

**日期**：2026-11-01 22:00 左右  **结果**：**bitstream 加载成功，但首次 AXI 访问导致系统挂死，板卡需断电重启**

---

## 0. 结论先行

| 步骤 | 结果 |
|---|---|
| 网络恢复（以太网传输通道） | ✅ **成功** |
| 包构建（bit.bin + dtbo + shell.json） | ✅ **成功** |
| 传输到板卡（8,510,464 B） | ✅ **成功，SHA256 完全匹配** |
| `xmutil` 安装识别 | ✅ **成功** |
| **`xmutil loadapp`** | ✅ **成功**（"Loaded with slot_handle 0"） |
| `fpga_manager` 状态 | ✅ `operating` |
| DT overlay | ✅ `kv260-haptic-debug_image_1` |
| **首次 AXI 访问（读 0xA0000000）** | ❌ **挂死，系统失去响应** |
| 串口恢复尝试（6 次 Ctrl-C/唤醒） | ❌ **无任何响应** |
| **板卡现状** | ⛔ **需断电重启** |

**PS/PL 真实通信：FAIL（挂死，不是返回值错误）**

---

## 1. 成功的部分（有完整证据）

### 1.1 网络
- 板端 `eth0` UP、carrier=1、MAC `00:0a:35:2b:a2:bb`
- **问题**：NetworkManager 把 eth0 置为 `disconnected` 并刷新手动 IP
- **解法**：`nmcli device set eth0 managed no` + `ip addr add 169.254.91.200/16`
- PC 侧 `以太网 3`（Realtek USB GbE, MAC `00-E0-4C-17-4F-B0`）169.254.91.130/16
- 连通后 TCP 传速很快（8.5 MB 仅 0.1 s）

### 1.2 包构建
| 文件 | 大小 | 说明 |
|---|---|---|
| `kv260-haptic-debug.bit.bin` | 7,797,692 B | 由 `bootgen -process_bitstream bin` 生成 |
| `kv260-haptic-debug.dtbo` | 477 B | **手工最简 overlay（见 §3 缺陷分析）** |
| `shell.json` | 57 B | `{"shell_type":"XRT_FLAT","num_slots":"1"}` |
| `pkg.tar` | 8,510,464 B | sha256 `c44e0efe…` |

### 1.3 传输与安装（全链路校验通过）
```
板端: sha256sum /tmp/pkg.tar
c44e0efe0ca1c932a525a9b3e42505bbbcc7f47b91dda963198baf3e1b69472e  /tmp/pkg.tar
PC 端: c44e0efe0ca1c932a525a9b3e42505bbbcc7f47b91dda963198baf3e1b69472e
>>> MATCH
```
装到 `/lib/firmware/xilinx/kv260-haptic-debug/` 后 `xmutil listapps` 能识别。

### 1.4 加载（关键成功证据）
```
sudo xmutil unloadapp        -> remove from slot 0 returns: 0 (Ok)
sudo xmutil loadapp kv260-haptic-debug
  -> [ 4031.421373] OF: overlay: WARNING: memory leak will occur if overlay removed, property: /__symbols__/kv260_haptic_debug
  -> kv260-haptic-debug: Loaded with slot_handle 0
fpga_manager/fpga0/state     -> operating
overlays/                    -> kv260-haptic-debug_image_1
xmutil listapps              -> kv260-haptic-debug  XRT_FLAT  ...  0->0,
```
**bitstream 确实被 FPGA Manager 接受并加载成功。**

---

## 2. 失败点：首次 AXI 访问挂死

执行 `sudo ./haptic_axi_smoke` 后：
- 程序**无任何输出**（连第一行 `printf` banner 都没返回）
- 串口命令 180 s 超时，被 Ctrl-C 中断
- 之后再串口发送 Ctrl-C / CR / 唤醒共 6 次，**收到 0 字节**
- 主机侧 `COM15` 设备状态仍为 OK → **是板卡侧不响应，不是串口问题**

**这不是"读到错误值"，而是整个 CPU 卡在总线访问上**。ZynqMP 对**无响应 PL 从设备**的 AXI 访问会一直等待（若 AFI/超时未使能），导致进程乃至系统卡死。

---

## 3. 缺陷分析（为什么会挂）

### 3.1 最可能原因：PL 处于复位/未时钟状态，SmartConnect 不响应

我们的 BD：
```
pl_clk0 ──> axi_sc/aclk, haptic/aclk, maxihpm0_fpd_aclk, rst_pl/slowest_sync_clk
pl_resetn0 ──> rst_pl/ext_reset_in
rst_pl/peripheral_aresetn ──> axi_sc/aresetn, haptic/aresetn
```

**Kria 平台的已知要求**：`xmutil loadapp` 加载自定义 app 时，**PL 复位需要由 firmware driver 显式解除**，
并且依赖 `.dtbo` 中描述复位资源（`firmware-name`、复位节点等）。
我们的 dtbo 是**手工写的最简 overlay**：

```dts
fragment@0 {
    target = <&fpga_full>;
    __overlay__ {
        kv260_haptic_debug: kv260-haptic-debug@0 {
            compatible = "generic-uio";
            status = "okay";
        };
    };
};
```

它**没有描述任何复位控制**，`xmutil` 只做位流加载，**没有解除 PL 复位**。
结果：`peripheral_aresetn` 保持有效（低）→ SmartConnect 处于复位 → **不响应 AXI** → CPU 卡死。

### 3.2 次可能原因：`pl_clk0` 未使能
若 PL0 时钟未被 PS 使能，PL 逻辑无时钟，同样不响应。

### 3.3 我们已经知道的风险（事前记录过，但低估了）
Phase 2B-Lite/FIX 报告里记录过 "0xA0003000 未映射会挂死 AXI"。
实际上 **0xA0000000 也挂了** —— 因为整个 PL 不响应，与地址无关。这一点事前判断不足。

---

## 4. 恢复步骤（需要人工执行）

**板卡现已挂死，必须断电重启：**

1. 断开 KV260 电源（或按复位键，若板上有）
2. 等待 5 s 后重新上电
3. 板卡会按启动配置自动加载默认 app（`k26-starter-kits`），**不会损坏**
4. 我们安装的 `kv260-haptic-debug` app **仍在 `/lib/firmware/xilinx/` 中**，但**不会被自动加载**

**恢复后建议立即验证：**
```bash
xmutil listapps            # 应显示 k26-starter-kits 0->0,
cat /sys/class/fpga_manager/fpga0/state
```

---

## 5. 下次上板前必须做的修正

| # | 修正项 | 说明 |
|---|---|---|
| 1 | **用官方 Kria app 模板生成 dtbo** | 不要手写最简 overlay。应包含 `firmware-name` 与复位/时钟节点，参考 `k26-starter-kits` 的 dtbo 结构 |
| 2 | **加载后先验证 PL 已脱离复位** | 例如读 `dbg_online_mask` 前，先用 `devmem` 读取一个**确定安全**的地址；或先确认 `pl_resetn0` 状态 |
| 3 | **给 AXI 访问加外部超时保护** | 用 `timeout` 命令包裹；或改用 `devmem` 单次读（也有同样风险，但至少能定位） |
| 4 | **先做"PL 活着"的最小证明** | 不用 AXI，改用 PS 可读的 PL 状态（如通过 `generic-uio` 或直接用 `devmem` 读一个已在基 DT 中映射的 PL 地址） |
| 5 | **确认 PSU__FPGA_PL0_ENABLE 在加载后确实生效** | 由固件/DT 保证，不能只靠 Vivado 配置 |
| 6 | **准备带独立看门狗的启动方案** | 或者先在 SD 卡上做还原备份（更稳妥） |

---

## 6. 证据文件

| 目录 | 内容 |
|---|---|
| `kv260_p2bfinal_hw/evidence/pre1` | 板端初始状态（apps/firmware/tools/state/net） |
| `evidence/pre2` | eth0 与 sshd 状态 |
| `evidence/ip1..ip3` | 网络配置过程（含 NetworkManager 问题定位） |
| `evidence/recv2/recv3` | 接收端部署 |
| `evidence/inst1` | **SHA256 校验匹配（关键证据）** |
| `evidence/inst2` | **app 安装 + xmutil 识别** |
| `evidence/load1` | **loadapp 成功证据（关键证据）** |
| `evidence/smoke1` | 冒烟测试挂起（空输出） |
| `evidence/diag1` | 串口探测失败 |
| `D:\DevCache\Temp\kv260_hw_load` | 包构建工作目录（.bit.bin / dtbo / pkg.tar） |

---

## 7. 边界声明

- ✅ 未修改冻结 RTL
- ✅ 未修改 AX7020 原工程
- ✅ **仅加载了我们自己的调试镜像**，未烧写 Flash/SD
- ✅ 未接 PCA9685 / 执行器
- ✅ 未修改供电（**但现在需要人工断电重启才能恢复**）
- ❌ **本次操作导致板卡挂死** —— 这是真实后果，如实记录

---

## 8. 诚实结论

**"PS/PL 真实硬件通信通过" 未达成。**
达成的层级是：
1. RTL 回归通过 ✅
2. Vivado 实现通过 ✅
3. 调试镜像生成成功 ✅
4. **镜像加载到板卡成功 ✅（新达成）**
5. PS/PL 真实通信 ❌ **失败（挂死）**

第 4 项是本次真实的进展；第 5 项失败，根因指向 **dtbo 缺少复位/时钟描述导致 PL 未脱离复位**。
