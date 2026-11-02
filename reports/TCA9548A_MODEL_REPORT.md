# TCA9548A 模型报告

**日期**：2026-11-02  **文件**：`sim/i2c_devices.sv` → `tca9548a_model`

---

## 1. 模型规格

| 项 | 值 | 依据 |
|---|---|---|
| 7-bit 地址 | **0x70** | `haptic_matrix.h:17`（`HAPTIC_VEST_TCA_ADDR_HAL = 0xE0`）；`pca_init_sequencer.v:81`（`TCA_ADDR_W = 8'hE0`） |
| 写地址字节 | `0xE0` | 同上 |
| 控制寄存器 | 1 字节通道掩码 | TI TCA9548A 数据手册 |
| 通道数 | 8（模型实现 8 bit，序列只用 0–3） | 数据手册 |

## 2. 实现要点

```verilog
module tca9548a_model #(parameter integer PRESENT = 1) (
    input  wire scl, sda,
    output reg  sda_pull,     // 1 = 拉低 SDA（ACK）
    output reg  [7:0] ch_mask,
    output reg  seen_ack,
    output reg  [7:0] last_byte
);
```

**单 `always @(scl or sda)` 块 + 阻塞赋值**，`scl_p/sda_p` 保存**上一次**电平用于边沿检测
（更新放在块末尾）—— 这是避免多块边沿驱动竞态的关键。

### 状态机

| 事件 | 动作 |
|---|---|
| START（SCL 高时 SDA 下降） | 复位位计数、置 `active = present` |
| SCL 上升，bit_i<8 | 移入 1 bit |
| SCL 上升，刚移完 8 bit | 判定 `ack_next`：地址字节比较 `sh[7:1]==0x70`；数据字节恒 ACK |
| SCL 下降，bit_i==8 且 ack_next | 拉低 SDA（输出 ACK） |
| SCL 上升（ACK 时钟后） | 地址匹配 → `got_addr=1, seen_ack=1`；不匹配 → `active=0`（不再响应） |
| 数据字节 | `ch_mask <= sh` |
| STOP（SCL 高时 SDA 上升） | 释放、清状态 |

## 3. 通道隔离机制

16 片 PCA 模型的 `ch_enable` 输入直接接 `ch_mask[CH]`：

```verilog
wire present = (PRESENT != 0) && ch_enable;   // pca9685_model 内
```

未选通通道的 PCA **完全不响应任何地址**（`active=0`），因此不会 ACK。
这正确复现了 TCA9548A 的物理隔离行为。

## 4. 仿真验证

| 证据 | 值 | 含义 |
|---|---|---|
| `TCA_ACK_SEEN` | **1** | TCA 至少正确 ACK 过一次地址字节 |
| `TCA_LAST_MASK` | **0x08** | 最后一次写入的通道掩码 = `1<<3` = CH3 |
| 16/16 PCA 在线 | `ONLINE_MASK=0xFFFF` | 四个通道切换全部成功 |
| TCA 不存在场景 | `ONLINE=0x0000` | 缺失时不会误报 |

`TCA_LAST_MASK=0x08` 与初始化顺序吻合：block 12–15 属 CH3（`tca_of(12..15)=3`），
是序列最后访问的通道，因此最后一次写掩码为 `0x08`。

## 5. 模型局限

- 未建模通道切换的建立/保持时间
- 未建模 TCA 自身复位引脚（模型中 `PRESENT=0` 等效于完全不响应）
- 未建模多通道同时选通的并联效应（序列不会这样用，`mask` 恒为单 bit）
