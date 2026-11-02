# 21 项逐项验收表

**结果: 21/21 PASS, errors=0**（最终 revB 运行）

| # | 检查项 | 证据 | 结果 | 层级 |
|---|---|---|---|---|
| T1 | AXI 写入矩阵数据 | AXI CTRL.ENABLE + 256 cell writes | **PASS** | sim |
| T1b | AXI 256 格突发写 | 256 axi_write | **PASS** | sim |
| T2 | 双 Bank 提交 | COMMIT -> STATUS readback | **PASS** | sim |
| T3 | Scheduler 触发更新 | n_starts>0 (243) | **PASS** | sim |
| T4 | TCA9548 通道选择 | tca_sel=56 | **PASS** | sim |
| T4b | TCA 掩码单通道 | multi=0, single=45, deselect=1 | **PASS** | sim |
| T5 | PCA9685 MODE1 | mode1=55 | **PASS** | sim |
| T5b | PCA9685 PRE_SCALE | prescale=1 | **PASS** | sim |
| T6 | PWM 寄存器写入 | pwm_wr=385, blocks=ffff | **PASS** | sim |
| T7 | 指定通道 PWM 值 | blk=8 ch=1 ON=16 | **PASS** | sim (寄存器级) |
| T8a | OE# 复位默认关断 | oe_n=1 after reset | **PASS** | sim |
| T8a2 | OE 原因=RESET | reason=1 | **PASS** | sim |
| T8a3 | 复位后未 armed | armed=0 | **PASS** | sim |
| T8b | 无 SW 请求保持关断 | oe_n=1 | **PASS** | sim |
| T8c | 仅 init healthy 才使能 | 走 fail-closed 分支 | **PASS(仅安全侧)** | sim |
| T9a | 复位期间 OE 关断 | oe_n=1 | **PASS** | sim |
| T9b | 复位后 OE 关断 | oe_n=1 | **PASS** | sim |
| T10 | NACK 注入 | nacks=74 | **PASS** | sim |
| T10b | 故障强制关断 | oe_n=1 | **PASS** | sim |
| T11 | SDA 卡死不挂死 | oe_n=1, sim 正常结束 | **PASS** | sim |
| T12 | 故障恢复路径 | fault_clear 写入 | **PASS** | sim |

## 层级说明
- **sim** = RTL 在 XSim 中功能通过
- **寄存器级** = T7 验证的是 PCA9685 LEDn_ON/OFF 寄存器写入值，不是模拟 PWM 波形
- **真实硬件** = 全部 **NOT VERIFIED**，未接任何器件

## 未证明项
1. OE# 正向使能（T8c 走的是安全拒绝分支）
2. 物理 PWM 波形/50Hz
3. 任何真实硬件行为
