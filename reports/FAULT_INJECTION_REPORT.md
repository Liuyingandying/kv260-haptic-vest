# 故障注入报告

**日期**：2026-11-02
**结论**：**7 个故障场景全部符合预期，无 FSM 死锁，无错误启用输出。**

---

## 结果矩阵

| 场景 | done | ONLINE_MASK | INIT_ERR | 在线数 | fault | i2c_err |
|---|---|---|---|---|---|---|
| `S1_16online` | 1 | 0xffff | 0x0000 | 16 | 0 | 0 |
| `S2_1online` | 1 | 0x0001 | 0xfffe | 1 | 1 | 15 |
| `S3_8online` | 1 | 0x00ff | 0xff00 | 8 | 1 | 8 |
| `S4_ch2_offline` | 1 | 0xf0ff | 0x0f00 | 12 | 1 | 4 |
| `S5_first_nack` | 1 | 0xfffe | 0x0001 | 15 | 1 | 1 |
| `S6_no_tca` | 1 | 0x0000 | 0xffff | 0 | 1 | 17 |
| `S7_all_nack` | 1 | 0x0000 | 0xffff | 0 | 1 | 16 |

## 逐项分析

### S5 — 第一片 NACK（关键）

配置：`PRESENT_MASK = 0xFFFE`（仅 block 0 缺失，即地址 0x4D / TCA CH0）

结果：`ONLINE_MASK = 0xFFFE`（15 片在线）、`INIT_ERR_MASK = 0x0001`、`I2C_ERROR_COUNT = 1`

**这直接验证了 Phase 2F 引入的"遇 NACK 继续扫描"修复**：
- 修复前：第一笔 NACK → `S_TAIL` → 整个初始化终止 → 仅 1 块被探测
- 修复后：跳过该块 → 继续下一块 → **其余 15 片全部成功**

### S6 — TCA 不存在（关键安全属性）

配置：`TCA_PRESENT = 0`

结果：`ONLINE_MASK = 0x0000`、`INIT_ERR_MASK = 0xFFFF`、`I2C_ERROR_COUNT = 17`

**安全属性正确**：TCA 缺失时：
- 不会误报任何 PCA 在线
- `fault_latched = 1` 阻止输出使能

这对应 STM32 原始行为：`HapticVest256_DisableTca()` 失败即置 `init_error_mask=0xFFFF` 并 fault。

### S4 — CH2 全部离线

配置：`PRESENT_MASK = 0xF0FF`（block 8–11 缺失，即 CH2 上的 0x40–0x43）

结果：`ONLINE_MASK = 0xF0FF`（12 片在线）

**其他三个通道完全不受影响** —— 验证 TCA 通道隔离正确。

### S7 — 全部 NACK

`ONLINE_MASK = 0x0000`、`fault_latched = 1`（不启用输出）。

### 时序类故障

| 故障 | 验证位置 | 结果 |
|---|---|---|
| SDA 持续拉低 | Phase 4A `tb_i2c_4a` | 走统一超时路径，`hung=0` |
| SCL 无法上升 | Phase 4A `tb_i2c_4a`（tr=80/200 µs） | **`tmo_err=1`、`hung=0`** |
| 中途复位 | ❌ **未覆盖** | 未做复位注入 |

## 未覆盖项

- **中途复位**（reset during transaction）
- **SDA/SCL 短路到地或电源**
- OE 门控在故障下的行为（`haptic_oe_guard` 未在本 TB 实例化）
