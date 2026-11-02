# RTL_CHANGELOG — Phase 4A 修改清单

**日期**：2026-11-02
**范围**：**仅 `i2c_master.v` 一个模块**。未触碰 pipeline / sequencer / matrix buffer / scheduler / OE 逻辑。
**回退路径**：原文件完整保留为 `rtl_4a/i2c_master_BASELINE.v`，SHA256 见清单。

---

## 修改 1 — 相位分频器位宽自适应

**位置**：`i2c_master.v` 参数区 / L52-60 / L87 / L96

基线：
```verilog
localparam integer TICKS = CLK_FREQ_HZ / (4 * I2C_FREQ_HZ);  // 默认 31
reg [7:0] div;                       // 相位内节拍计数（支持 TICKS<=256）
wire   tick = (mst != M_IDLE) && (div == TICKS - 1);
```

4A：
```verilog
localparam integer TICKS = (CLK_FREQ_HZ/(4*I2C_FREQ_HZ) < 1)
                         ? 1 : (CLK_FREQ_HZ/(4*I2C_FREQ_HZ));
localparam integer DIV_W = (TICKS > 65536) ? 24 : (TICKS > 256 ? 16 : 8);
reg [DIV_W-1:0] div;
wire   tick = (mst != M_IDLE) && (div == TICKS[DIV_W-1:0] - 1'b1);
```

**原因**：8-bit `div` 使 `TICKS>256` 时 `tick` 永不成立 → FSM 挂死 → 50/10 kHz 不可用。
**影响**：解锁 ≤ 94.7 kHz；400/100 kHz 行为不变（DIV_W 仍为 8）。

## 修改 2 — 独立 SCL 上升 / 卡低看门狗

**位置**：参数 `SCL_RISE_TIMEOUT_US`（默认 50）、L78-84、L114-115、L122、L136-137、L148-149、L160-171

```verilog
parameter integer SCL_RISE_TIMEOUT_US = 50,
...
reg [RISE_W-1:0] rise_count;
reg              scl_rise_timeout;
...
if ((scl_drv == 1'b0) && (scl_i == 1'b0) && (mst != M_IDLE)) begin
    if (rise_count >= RISE_TIMEOUT_CYCLES[RISE_W-1:0]) scl_rise_timeout <= 1'b1;
    else rise_count <= rise_count + 1'b1;
end else rise_count <= {RISE_W{1'b0}};
```
超时并入统一错误终结条件（与 `abort_req`、整笔 `TIMEOUT_CYCLES` 同级）。

**原因**：基线只有整笔 5 ms 粗超时，无法区分"上升太慢"与"器件不应答"。
**影响**：新增 `timeout_error` 触发源；正常工况不触发（50 µs 阈值 ≈ 25 kΩ × 1.7 nF）。

## 修改 3 — ACK 采样点后移

**位置**：`M_ACK` 状态 L268-280

基线：
```verilog
2'd2: begin
          if (scl_i) begin
              if (sda_i) ack_error <= 1'b1;   // 刚变高就采样
              phase <= 2'd3;
          end
          else phase <= 2'd2;
      end
```

4A：
```verilog
2'd2: begin
          if (scl_i) phase <= 2'd3;           // 只等 SCL 到 VIH
          else phase <= 2'd2;
      end
2'd3: begin
          if (sda_i) ack_error <= 1'b1;       // SCL 高维持 TICKS 后再采样
          scl_drv <= 1'b1;
          phase   <= 2'd0;
          ...
      end
```

**原因**：慢上升时 SDA 在 SCL 刚过 VIH 时可能尚未稳定。
**影响**：ACK 判定点推迟一个完整高电平窗口；对快上升无功能变化（仅多一个相位延迟）。

---

## 未修改项（明确声明）

| 文件 / 逻辑 | 状态 |
|---|---|
| `kv260_haptic_pipeline.v` | **未改** |
| `pca_init_sequencer.v` | **未改**（A/B 偏差修正来自第 76 轮，本轮未回退） |
| `matrix_buffer_axi.v` / `matrix_buffer.v` | **未改**（冻结协议） |
| `scheduler_fsm.v` / `pca9685_burst_builder.v` | **未改** |
| `tca9548_inject.v` | **未改** |
| `haptic_oe_guard.v` | **未改**（OE 保护未削弱） |
| `kv260_haptic_iobuf_wrapper.v` | **未改**（开漏结构保持） |
| `candidate_pmod.xdc` | **未改**（PULLUP TRUE 保留） |
| STM32 原始工程 | **只读，未改** |

---

## 兼容性

| 项 | 说明 |
|---|---|
| 400 kHz / 100 kHz 配置 | 行为与基线一致（DIV_W=8，看门狗不触发，ACK 多一拍延迟） |
| 接口端口 | **完全不变**，可直接替换原模块 |
| 参数默认值 | `SCL_RISE_TIMEOUT_US=50` 为新增参数，有默认值 |
| 资源增量 | +14~15 LUT、+14~22 FF |

## 回退方法

```
把 rtl_4a/i2c_master.v 替换回 rtl_4a/i2c_master_BASELINE.v 即可
（SHA256 b0282ba70f368f761036b52729b276478225fe388140a6065dd4d20b9f75ccae）
```
