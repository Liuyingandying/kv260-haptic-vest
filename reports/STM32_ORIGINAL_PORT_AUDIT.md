# STM32 原始工程恢复 与 KV260 硬件接口差异审计

**日期**：2026-11-02
**方式**：只读审计（未修改 STM32 代码、未修改 KV260 RTL/XDC、未综合、未烧录、未写寄存器）
**核心结论**：**原始 STM32 外设是一块已集成「TCA9548A + 16×PCA9685」的触觉阵列板，
上游确实只需 SDA/SCL/3.3V/GND 四根线。KV260 移植在初始化字节流上完全忠实，
但把「允许部分装配」改成了「必须 16 块全在线」—— 这是唯一实质性偏差。**

---

## Phase 0：工程定位

### 0.1 磁盘环境（搬家后）

```
Get-PSDrive -PSProvider FileSystem:
  C:  Win 11 Home Chi x64   171.30 GB used / 27.60 GB free
  D:  软件                  287.40 GB used / 78.60 GB free
  E:  文档                  262.90 GB used / 102.30 GB free
物理磁盘: 1 × KINGSTON SNV2S1000G (932 GB, GPT)
当前工作目录: E:\03_Academics\Competitions\嵌入式比赛
```

### 0.2 STM32 原始工程（唯一候选，高可信度）

```
E:\03_Academics\Competitions\嵌入式比赛\23_EEPROM\STM32CubeIDE\Appli\
```

| 项 | 值 |
|---|---|
| MCU | **STM32N647X0HXQ**（STM32N6 系列，见链接脚本文件名） |
| 构建系统 | STM32CubeIDE（`.cproject` / `.project`，**无 .ioc**） |
| Git 仓库 | `E:\03_Academics\Competitions\嵌入式比赛\23_EEPROM`（**有 .git**） |
| 应用代码 | `Application\User\App\` |

**关键源码 SHA256：**

| 文件 | SHA256 |
|---|---|
| `haptic_matrix.c` | `ef28ed8800ff9fe10e407a474cd9849f965408fdb7375d66408d085d8084417f` |
| `haptic_matrix.h` | `678981322e9c8581ce7ef9c4d406fcf15219bcb15aeee35c6ce8e5f60c06884e` |
| `pca9685.c` | `ea8f0945fdcec0f06c2368d3a61431754a8170ee17cb4c888d5a25e5d087e9e2` |
| `pca9685.h` | `d560453f5a96247eb3be8ff635fd709687101ec5b08119af15a9dd9a28f3363d` |

**已排除的干扰项：**
`E:\03_Academics\Competitions\嵌入式比赛\intel submit\components\BSP\pca9685\pca9685.c`
（1,979 B，ESP32-S3 方案，属另一个执行控制实现，**不是**原始 STM32 工程）

### 0.2b 【已验证】全盘搜索与多副本识别（Phase 0 完整闭环）

**全盘文件名搜索（C:\ + D:\ + E:\，含隐藏文件）：**

| 搜索名 | 结果 |
|---|---|
| `haptic_matrix.c` | **1 个**（唯一） |
| `pca9685.c` | 2 个（原始工程 + `intel submit` ESP32 干扰项） |
| `pca9685.h` | 2 个（同上） |
| `HAPTIC_VEST_256_BLOCK_MAP*` | **0 个** —— 它是 `.c` 内符号，非文件名 |
| `HapticVest256*` | **0 个** —— 同上，是函数/类型名前缀 |

**仓库内多副本检查（含全部 untracked 目录）：**

全仓仅存在一份 haptic/pca 源码，路径与 SHA256 见 §0.2 表。
三个 untracked 备份目录经检查**均不含** haptic/pca 源码：

| 备份目录 | 含 haptic/pca 源码 |
|---|---|
| `Codex_Backup_Gray_UART_20260726` | ❌ 无 |
| `Codex_Backup_LCD_Before_800x480_20260726` | ❌ 无 |
| `Codex_Backup_MaixCAM_20260725` | ❌ 无 |

**Git 权威性确认：**

```
仓库      : E:\03_Academics\Competitions\嵌入式比赛\23_EEPROM
分支      : master
HEAD      : 38cd19dd2d39464a7e12d6590f6267ceb924a829
提交时间  : 2026-07-26 20:56:23 -0400
提交说明  : snapshot: STM32 MaixCAM UART and haptic control
Remote    : 无（纯本地仓库）
```

| 文件 | git tracked | 工作区状态 |
|---|---|---|
| `haptic_matrix.c` | ✅ yes | **clean** |
| `pca9685.c` | ✅ yes | **clean** |
| `haptic_matrix.h` | ✅ yes | **clean** |
| `pca9685.h` | ✅ yes | **clean** |

→ **四个关键源文件内容与 HEAD 提交逐字节一致，无本地漂移。
该工程为唯一权威版本，多副本识别无歧义，无需在差异间抉择。**

### 0.3 KV260 工程

| 角色 | 绝对路径 |
|---|---|
| 交付/报告根 | `E:\03_Academics\Competitions\嵌入式比赛\KV260_Haptic_Port_20261101` |
| **当前 1-channel debug 工程** | `E:\KVHAP_2F_DEBUG_1CH` |
| XSA 版本工程 | `E:\KVHAP_2F_IOBUF_FIX_R2_MI4` |
| 冻结 RTL | `...\KV260_Haptic_Port_20261101\rtl\` |
| 报告目录 | `...\KV260_Haptic_Port_20261101\reports\`（57 份 .md） |

---

## Phase 1：STM32 原始实现

### 1.1 【已验证】TCA9548A 确实存在，地址 0x70

`haptic_matrix.h:17`
```c
#define HAPTIC_VEST_TCA_ADDR_HAL   ((uint8_t)0xE0U)   // 7-bit 0x70
```

`haptic_matrix.h:16`
```c
#define HAPTIC_VEST_BLOCK_COUNT    16U
```

`haptic_matrix.h:40-44`
```c
typedef struct { uint8_t tca_channel; uint8_t pca_addr_hal; } HapticVest256BlockMap;
```

### 1.2 【已验证】16 块地址映射（乱序）

`haptic_matrix.c:24-29`（`HAPTIC_VEST_256_BLOCK_MAP`）
```c
{0U, 0x9AU}, {0U, 0x9CU}, {0U, 0x9EU}, {0U, 0x88U},
{1U, 0x92U}, {1U, 0x94U}, {1U, 0x96U}, {1U, 0x98U},
{2U, 0x80U}, {2U, 0x82U}, {2U, 0x84U}, {2U, 0x86U},
{3U, 0x8AU}, {3U, 0x8CU}, {3U, 0x8EU}, {3U, 0x90U}
```

HAL 8-bit → 7-bit（>>1）展开：

| 块 | TCA ch | HAL 地址 | **7-bit** |
|---|---|---|---|
| 0 | 0 | 0x9A | **0x4D** |
| 1 | 0 | 0x9C | **0x4E** |
| 2 | 0 | 0x9E | **0x4F** |
| 3 | 0 | 0x88 | **0x44** |
| 4 | 1 | 0x92 | **0x49** |
| 5 | 1 | 0x94 | **0x4A** |
| 6 | 1 | 0x96 | **0x4B** |
| 7 | 1 | 0x98 | **0x4C** |
| 8 | 2 | 0x80 | **0x40** |
| 9 | 2 | 0x82 | **0x41** |
| 10 | 2 | 0x84 | **0x42** |
| 11 | 2 | 0x86 | **0x43** |
| 12 | 3 | 0x8A | **0x45** |
| 13 | 3 | 0x8C | **0x46** |
| 14 | 3 | 0x8E | **0x47** |
| 15 | 3 | 0x90 | **0x48** |

### 1.3 【已验证】初始化流程

`haptic_matrix.c:403` `HapticVest256_Init()`：

1. `HapticVest256_MapIsValid()` 校验表
2. **`HapticVest256_DisableTca()` → 写 TCA @0x70 mask=0**（`L441`）
   - 失败 → `init_error_mask = 0xFFFF`、`fault_latched = 1`、直接返回（`L442-455`）
3. **逐块循环（不中断）**（`L455-478`）
   ```c
   for (block = 0U; block < HAPTIC_VEST_BLOCK_COUNT; block++) {
       status = HapticVest256_SelectTca(vest, MAP[block].tca_channel);
       if (status == HAL_OK) status = PCA9685_Init(..., MAP[block].pca_addr_hal, vest->pwm_hz);
       if (status == HAL_OK) vest->online_mask |= block_bit;
       else { vest->init_error_mask |= block_bit; vest->i2c_error_count++; }
   }
   ```
4. `HapticVest256_DisableTca()` 收尾
5. **判定（关键）**（`L488-500`）

### 1.4 【已验证】★ 部分装配是合法的

`haptic_matrix.c:488-500`
```c
/*
 * Partial populations are valid: only fail when no PCA9685 responded.
 * init_error_mask remains available for diagnostics.
 */
if ((vest->online_mask == 0U) || (status != HAL_OK))
{
    vest->enabled = 0U;  vest->failsafe_active = 1U;  vest->fault_latched = 1U;
    (void)HapticVest256_AllOff(vest);
    return HAL_ERROR;
}
return HAL_OK;
```

**原文注释明确：`Partial populations are valid: only fail when no PCA9685 responded.`**

### 1.5 【已验证】PCA9685 初始化字节序列

`pca9685.c:98-139` `PCA9685_Init()`：

| 顺序 | 寄存器 | 值 | 说明 |
|---|---|---|---|
| 1 | MODE1 (0x00) | `SLEEP\|AUTO_INC` = 0x30 | 睡眠中配置 |
| 2 | PRE_SCALE (0xFE) | `CalcPrescale(pwm_hz)` | 50 Hz → 121 |
| 3 | FULL_OFF (0xFD/0xFE/0xFF/0x00) | 0,0,0,0x10 | 全关 |
| 4 | MODE2 (0x01) | `OUTDRV` = 0x04 | 推挽输出 |
| 5 | MODE1 (0x00) | `AUTO_INC` = 0x20 | 唤醒 |
| 6 | MODE1 (0x00) | `RESTART\|AUTO_INC` = 0xA0 | 重启振荡器 |

**ALLCALL 处理**（`pca9685.c:100-103`）：
```c
/*
 * Keep the oscillator asleep while the output registers are made safe.
 * ALLCALL is deliberately left clear because the TCA9548 also uses 0x70.
 */
```
→ TCA9548A 与 PCA9685 ALLCALL **共用 0x70**，故刻意不置 ALLCALL 位。

### 1.6 【已验证】I²C 初始化与地址格式

- HAL 使用 **8-bit 地址**（`pca9685.h:11-12`：`PCA9685 default 7-bit base 0x40, default HAL address 0x80`）
- STM32 侧 I²C 外设：**I2C4**（`README_INTEGRATION.md:92` `extern I2C_HandleTypeDef hi2c4;`）
- Timing `0x10707DBC`（Fast 档，约 250–400 kHz，见 AX7020 迁移报告 L41）

### 1.7 【已验证】PWM 参数

`haptic_matrix.h:24-25`
```c
#define PCA9685_PWM_FULL_SCALE    4095U
#define PCA9685_PWM_SAFE_MAX      4080U
```
`haptic_matrix.c` 灰度映射：`{0U, 1632U, 2448U, 3264U, 4080U}`（5 级）
默认 `pwm_hz = 50`（`haptic_matrix.c:417`）

---

## Phase 2：KV260 工程审计

### 2.1 当前部署

| 项 | 值 |
|---|---|
| App | `kv260-haptic-1ch`（PL_FLAT） |
| 本地 bitstream | `E:\KVHAP_2F_DEBUG_1CH\kv260_i2c_bringup.runs\impl_1\kv260_i2c_bringup_top.bit`<br>7,797,833 B，SHA256 `4383e1994ea501f4…` |
| bit.bin SHA256 | `9f089fecf86c59fccbfce3f6ad214eb1c5a1204173422be7f4e20f81b5223195` |
| 包 SHA256 | `8da07635b83f12ec5354dc24d3fd6130dbc1def94cd6b3f7ad9613619ae03175` |

### 2.2 【已验证】KV260 的 TCA / PCA 映射与 STM32 逐项一致

`pca_init_sequencer.v` 的 `pca_w()` 表：块 0=0x4D, 1=0x4E, 2=0x4F, 3=0x44, 4=0x49 … 15=0x48；
`tca_of(b) = b[3:2]` → 块 0–3→ch0, 4–7→ch1, 8–11→ch2, 12–15→ch3。

**与 §1.2 的 STM32 表逐条相同 ✅**

### 2.3 【已验证】KV260 的初始化字节流与 STM32 一致

`pca_init_sequencer.v` `descriptor_byte()`：

| 段 | KV260 字节 | STM32 对应 | 一致 |
|---|---|---|---|
| s=1 | `0x30` | `MODE1_SLEEP\|AUTO_INC` = 0x30 | ✅ |
| s=2 | `PRE_SCALE` | `CalcPrescale()` | ✅ |
| s=3 | `bj[1:0]==1 ? 0x10 : 0x00` | FULL_OFF 序列 | ✅ |
| s=4 | `0x04` | `MODE2_OUTDRV` = 0x04 | ✅ |
| s=5 | `0x20` | `AUTO_INC` = 0x20 | ✅ |
| s=6 | `0xA0` | `RESTART\|AUTO_INC` = 0xA0 | ✅ |
| 全局 | `g=1` → `0xE0` mask=0 | `DisableTca()` | ✅ |

### 2.4 【已验证】★ KV260 的唯一实质性偏差：init_healthy 判据

`kv260_haptic_pipeline.v:194`（**移植原样**）
```verilog
assign init_healthy = init_done && (online_mask==16'hFFFF) && (init_err_mask==0);
```

对比 STM32 `haptic_matrix.c:492`：
```c
if ((vest->online_mask == 0U) || (status != HAL_OK))   // 只有"一个都没有"才失败
```

| | STM32 原始 | KV260 移植 |
|---|---|---|
| 判据 | `online_mask != 0` | `online_mask == 0xFFFF` **且** `init_err_mask == 0` |
| 部分装配 | **合法** | **判为故障** |

**这是移植时把"允许部分装配"提升成了"必须全装配"，属于偏差。**

### 2.5 【已验证】第二处偏差：遇 NACK 是否中断

| | STM32 | KV260 原版 |
|---|---|---|
| 行为 | 逐块循环，**记录错误但继续** | `if (m_ack_err \|\| (tn == TXN_LAST)) st <= S_TAIL;`<br>**第一个 NACK 即终止整个序列** |

`pca_init_sequencer.v:314-316`、`393-396`。

### 2.6 本轮已做的两处 debug 修正（在 `E:\KVHAP_2F_DEBUG_1CH`）

| 改动 | 位置 | 作用 |
|---|---|---|
| A：NACK 不终止，跳到下一块 | `pca_init_sequencer.v:320-338` | **恢复 STM32 的"逐块继续"行为** |
| B：`MIN_ONLINE_BLOCKS = 1` | `kv260_haptic_pipeline.v:194-205` | **恢复 STM32 的"部分装配合法"** |

> 注：改动 B 的 `MIN_ONLINE_BLOCKS=1` 正是复现 STM32 原始语义；若设回 16 才等价于原 KV260 行为。

---

## Phase 3：对照与结论

### 3.1 逐项对照表

| # | 项 | STM32 原始 | KV260 当前 | 一致 |
|---|---|---|---|---|
| 1 | I²C 上游线数 | **4 根**（SCL/SDA/3.3V/GND） | 4 根（J2.1/J2.2/3.3V/GND） | ✅ |
| 2 | TCA9548A 位置 | **在外设阵列板上** | 设计假定在外部 | ✅ |
| 3 | TCA 地址 | 0x70（HAL 0xE0） | 0x70（`8'hE0`） | ✅ |
| 4 | PCA9685 数量/位置 | **16 片，同阵列板** | 假定 16 片在外部 | ✅ |
| 5 | PCA 7-bit 地址表 | 0x4D/4E/4F/44…48 | **完全相同** | ✅ |
| 6 | TCA 通道分配 | 0–3 → 每通道 4 片 | **完全相同** | ✅ |
| 7 | 256 执行器映射 | `HapticVest256_SourceIndex` | `matrix_map_rom` | ✅ |
| 8 | 初始化字节流 | MODE1→PRE→FULLOFF→MODE2→MODE1→RESTART | **完全相同** | ✅ |
| 9 | ALLCALL 处理 | 刻意清除（避免 0x70 冲突） | 发 mask=0 隔离 | ✅ |
| 10 | PWM 频率 | 50 Hz，PRE_SCALE 计算 | `PWM_HZ=50` | ✅ |
| 11 | **部分装配判据** | `online_mask != 0` | `online_mask == 0xFFFF` | ❌ **偏差** |
| 12 | **NACK 后行为** | 继续下一块 | 立即终止 | ❌ **偏差** |
| 13 | OE / RESET / GPIO | `PCA9685_AllOff` + 无独立 OE 脚 | `OE_CTRL` 软件门控 | ⚠️ 实现方式不同 |
| 14 | 故障恢复 | `failsafe_ms` 超时全断 | `failsafe_timer` 500 ms | ✅ 等价 |

### 3.2 回答七个问题

**Q1：原始 STM32 硬件为什么只需要四根上游接口线？**

【已验证】因为 TCA9548A 与 16 片 PCA9685 **全部集成在触觉阵列板内**，上游只暴露一条 I²C 总线 + 逻辑电源 + 地。
证据：`fpga/16x16/docs/HARDWARE_INTERFACE_MANUAL.md:14-18`（拓扑图）、`:48-49`（SCL/SDA → TCA9548）、`:55`（3.3V → TCA VDD、PCA VCC）。

**Q2：TCA9548A 是否本来就在外设板上？**

【已验证】是。证据 `AX7020_Migration_Feasibility_Report.md:51` 原文：
> 迁移后需要**新做的物理连接只有 2 组线**：① AX7020 的 I2C（2 根…）→ **触觉阵列的 TCA9548**；② …

「触觉阵列的 TCA9548」= 属于阵列板。

**Q3：PCA9685 是否集成在同一块板上？**

【已推断，高可信】是。`HARDWARE_INTERFACE_MANUAL.md:19-20` 显示 4×PCA9685 × 4 通道 → 256 执行器全部在同一"阵列"内；`:56` 说明执行器电源（V+）在 PCA 板上单独引。
**未取得 PCB 原理图/丝印照片，故标为"推断"。**

**Q4：KV260 是否遗漏了原 STM32 的某些控制功能？**

【已验证】**没有遗漏功能，但收紧了两条判定**：
- 遗漏项：无
- 收紧项：① 部分装配判据（偏差 11）；② NACK 后继续（偏差 12）
- 差异项（非遗漏）：STM32 用 `PCA9685_AllOff` 直接写寄存器关断；KV260 额外加了软件 `OE_CTRL` 门控 + `safety_off` 状态机 —— **增强而非缺失**

**Q5：当前 KV260 是否错误地假设需要额外购买 TCA9548A？**

【已验证】**这个假设来自我此前几轮的误判，已撤回。**
- KV260 的 RTL **没有**"需要外购 TCA"的设计；它只是**假定外设板上有 TCA**，这与 STM32 原始一致
- 我之前建议"加一个 TCA9548A 模块"是**为了让单件调试通过**，不是原始架构要求
- **正确理解**：TCA9548A 应在触觉阵列板内，不应外接

**Q6：目前 16 个块全部 NACK 的最可能原因？**

按证据强度排序：

| 排序 | 原因 | 支持证据 | 等级 |
|---|---|---|---|
| **1** | **当前外接的不是原始阵列板**，只是 1 个散装 TCA 模块 + 1 个 PCA9685，且 16 片 PCA 中最多只有 1 片在场 | 用户自述接线为"TCA9548A 模块"+"PCA9685 四线"；`INIT_ERR_MASK=0xFFFF` 全失败 | **推断（强）** |
| 2 | TCA9548A 未应答（未接好/未供电/地址非 0x70/未共地） | `Init` 第一步 `DisableTca` 失败即置 `init_error_mask=0xFFFF`，与观测完全一致 | **推断（强）** |
| 3 | 唯一那片 PCA9685 不在 16 个期望地址/通道上（出厂 0x40 需在 **CH2**） | 块 8 = 0x40/ch2；若接在 CH0 则永不命中 | **推断** |
| 4 | PCA9685 逻辑电源未接 | — | 未知 |
| 5 | RTL 的 init 判据偏差导致整体判失败 | 已由 debug 版 A/B 两处修正排除（仍全 NACK，故不是主因） | **已验证排除** |

**关键推论**：debug 版已把"遇 NACK 继续"和"允许部分装配"都修好了，结果仍是 16 块全 NACK
→ **说明连 1 片 PCA9685 都没有应答**（若有一片在 CH2@0x40，`ONLINE_MASK` 的 bit8 会置位）。

**Q7：最小修复路线？**

【建议，未执行】
1. **不要再外购 TCA** —— 恢复原始架构认知：TCA + 16×PCA 属阵列板
2. 若目标只是**点亮 1 路振动**，用已构建的 `kv260-haptic-1ch`（已含 A/B 两处修正）：
   - 硬件：TCA9548A @0x70（A0/A1/A2 接地、RST 拉高、3.3V、共地）+ **1 片 PCA9685 接 CH2、保持出厂 0x40**
   - 期望：`ONLINE_MASK = 0x0100`（bit8）、`INIT_DONE=1`、`init_healthy=1`
3. 若目标是对齐原始 256 路验收：需完整的「TCA9548A + 16×PCA9685」阵列板
4. **不建议**把 `MIN_ONLINE_BLOCKS` 长期设为 1 用于正式验收 —— 那会偏离"全阵列健康目标"
   （`HARDWARE_INTERFACE_MANUAL.md:37`：`INIT=1、ONLINE=FFFF、FAULT=0、I2C_ERROR=0`）

---

## Phase 4：证据等级汇总

| 结论 | 等级 | 依据 |
|---|---|---|
| STM32 工程位于 `23_EEPROM\STM32CubeIDE\Appli` | **已验证** | 文件存在 + SHA256 |
| STM32 使用 TCA9548A @0x70 | **已验证** | `haptic_matrix.h:17` |
| 16 块地址乱序表 | **已验证** | `haptic_matrix.c:24-29` |
| 部分装配合法 | **已验证** | `haptic_matrix.c:488-500` 注释 + 代码 |
| 上游 4 根线 | **已验证** | `HARDWARE_INTERFACE_MANUAL.md:14,48,55` |
| TCA 属阵列板自带 | **已验证** | `AX7020_Migration_Feasibility_Report.md:51` |
| PCA9685 同板 | **推断（高）** | 拓扑图；无 PCB 文件 |
| KV260 init_healthy 判据偏差 | **已验证** | `kv260_haptic_pipeline.v:194` vs `haptic_matrix.c:492` |
| 当前全 NACK = 器件未应答 | **推断（强）** | debug 修正后仍全失败 |
| 具体是哪一件未应答 | **未知** | 需万用表/逻辑分析仪 |

---

## 边界（严格遵守）

- ✅ 未修改 STM32 原始代码（4 个关键文件 SHA256 已记录，未写入）
- ✅ 未修改 KV260 RTL/XDC（本轮仅读取 `E:\KVHAP_2F_DEBUG_1CH\rtl_r2`）
- ✅ 未重新综合或实现
- ✅ 未烧录 bitstream
- ✅ 未写任何硬件寄存器
- ✅ 未执行 fault_clear、未打开 OE、未提交振动帧
- ✅ 未增加外接电阻
