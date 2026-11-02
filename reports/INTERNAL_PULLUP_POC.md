# INTERNAL_PULLUP_POC — 内部上拉低速通信可行性验证

**日期**：2026-11-02
**结论**：**仿真层面可行 —— 现有 FSM 已具备"自适应 SCL 拉伸"能力，
无需改上拉即可在 25 kΩ + 1.2 nF 下完成 START/地址/ACK/STOP。
本轮仅做代码修改与仿真，未接触硬件。**

---

## B/C 阶段做了什么

### 改动 1：相位分频器位宽自适应（解锁低速）

基线 `reg [7:0] div` 导致 TICKS>256 时 `tick` 永不成立，FSM 挂死。
改为按 TICKS 自动取宽：

```verilog
localparam integer TICKS = (CLK_FREQ_HZ/(4*I2C_FREQ_HZ) < 1) ? 1 : (CLK_FREQ_HZ/(4*I2C_FREQ_HZ));
localparam integer DIV_W = (TICKS > 65536) ? 24 : (TICKS > 256 ? 16 : 8);
reg [DIV_W-1:0] div;
wire tick = (mst != M_IDLE) && (div == TICKS[DIV_W-1:0] - 1'b1);
```

| 目标频率 | TICKS | DIV_W | 基线能否表示 |
|---|---|---|---|
| 400 kHz | 60 | 8 | ✅ |
| 100 kHz | 242 | 8 | ✅ |
| 50 kHz | 484 | 16 | ❌ → ✅ **已解锁** |
| 10 kHz | 2424 | 16 | ❌ → ✅ **已解锁** |

### 改动 2：独立 SCL 上升/卡低看门狗

```verilog
parameter integer SCL_RISE_TIMEOUT_US = 50,
...
if ((scl_drv == 1'b0) && (scl_i == 1'b0) && (mst != M_IDLE)) begin
    if (rise_count >= RISE_TIMEOUT_CYCLES[RISE_W-1:0]) scl_rise_timeout <= 1'b1;
    else rise_count <= rise_count + 1'b1;
end else rise_count <= {RISE_W{1'b0}};
```
超时后走统一错误终结路径（`timeout_error=1`、`scl_drv/sda_drv<=0`、`done` 脉冲），**不挂死**。

### 改动 3：ACK 采样点后移一个完整高电平窗口

基线在 `scl_i` **刚变高那一刻**就采 SDA；慢上升时 SDA 尚未稳定。
改为在 phase3 采样（即 SCL 高电平维持 TICKS 之后）：

```verilog
2'd2: begin if (scl_i) phase <= 2'd3; else phase <= 2'd2; end   // 只等 SCL 到 VIH
2'd3: begin
          if (sda_i) ack_error <= 1'b1;   // 此时 SCL/SDA 均已稳定 TICKS
          scl_drv <= 1'b1; phase <= 2'd0;
      end
```

---

## 未做的改动（明确记录）

| 项 | 为何不改 |
|---|---|
| SDA 推挽驱动 | ❌ 违反开漏原则，明令禁止 |
| 加外接电阻 | ❌ 本轮硬性禁止 |
| 降频以外的电气补偿 | ❌ 无依据 |

---

## FSMs 时序行为复查（Phase C 要求逐项）

| 检查项 | 基线行为 | 4A 后 |
|---|---|---|
| START | `M_START` 等 `scl_i` 高才拉低 SCL | 未变，已正确 |
| STOP | `M_STOP` phase2/3 等 `scl_i` 高 | 未变，已正确 |
| 数据发送 | `M_SEND` phase2 等 `scl_i` 高 | 未变，已正确 |
| **ACK/NACK 采样** | phase2 采样（过早） | **phase3 采样（已修正）** |
| 数据读取 | 未实现（仅写） | — |
| 时钟拉伸兼容 | 已有（phase2 等待） | 保留 + 新增超时保护 |
| **SCL 上升超时** | 仅整笔 5 ms 粗超时 | **独立 50 µs 看门狗** |

---

## 关键发现：现有设计本来就能容忍慢上升

仿真显示（详见 SIMULATION_REPORT）：

| 上拉 | 电容 | tr | 标称 | **实测 SCL** | ACK |
|---|---|---|---|---|---|
| 25 kΩ | 100 pF | 3 µs | 400 kHz | 202 kHz | ✅ |
| 25 kΩ | 200 pF | 6 µs | 400 kHz | 124 kHz | ✅ |
| 25 kΩ | 400 pF | 12 µs | 400 kHz | 70.4 kHz | ✅ |
| 25 kΩ | **1.2 nF** | **36 µs** | 400 kHz | **26.1 kHz** | ✅ |
| 50 kΩ | 400 pF | 24 µs | 400 kHz | 38.4 kHz | ✅ |

**原因**：`M_SEND`/`M_ACK` 的 phase2 会阻塞等待 `scl_i` 变高，
等效于把每个 SCL 周期延长一个上升时间 → 时钟自动降速跟上。

→ **内部 25 kΩ 并非"绝对无法工作"**，只是会把实际 SCL 频率拉低到几十 kHz。

---

## 那么硬件全 NACK 的原因是什么？

本轮无法下结论，但可排除的方向：

| 假设 | 状态 | 依据 |
|---|---|---|
| 上拉不足导致电气失效 | ❌ **已排除（仿真）** | FSM 自适应拉伸，25k/1.2nF 仍 ACK |
| RTL 判据偏差 | ❌ 已排除（第 76 轮已修 A/B） | debug 版仍全 NACK |
| FSM 挂死 | ❌ 已排除（仿真 hung=0） | — |
| **驱动板未真正连通/未供电** | ⬜ **最可能，未验证** | 无器件 ACK，含 TCA 本身 |
| SCL/SDA 接反或未共地 | ⬜ 未验证 | — |

**结论：问题更可能在"器件是否真的在总线上"，而不是时序参数。**
需下一轮上板用逻辑分析仪/万用表确认，本轮禁止上板。
