# 单通道 PWM 端到端测试报告

**日期**：2026-11-02
**状态**：⚠️ **本阶段未执行——超出仿真范围**

---

## 为什么没做

Phase 4B 第 6 节要求的单通道 PWM 端到端测试，需要以下完整链路：

```
AXI 写矩阵 → matrix_buffer_axi → scheduler_fsm → pca9685_burst_builder
          → tca9548_inject → i2c_master → 总线 → TCA → PCA9685 PWM 寄存器
```

本阶段的 TB（`tb_system`）只实例化了**初始化子系统**：
`pca_init_sequencer` + `i2c_master` + `i2c_fault_monitor`。

**运行时路径未接入**，因此无法在此 TB 中完成 PWM 写入测试。

## 要完成该测试需要什么

1. 实例化整条 `kv260_haptic_pipeline`（含 AXI 从接口、矩阵缓冲、调度器、突发构建器）
2. 用 AXI BFM 写入一帧 256 字节矩阵
3. 触发 commit
4. 在 PCA 模型中捕获 `LED*_ON/OFF` 寄存器写入
5. 校验 50 Hz 对应 PRE_SCALE=121、占空比映射 `{0,1632,2448,3264,4080}`

这是一个独立的仿真工程项目，工作量远超本阶段范围。

## 已有证据（非仿真）

| 证据 | 来源 |
|---|---|
| 寄存器字节流正确性 | `tb_pca9685_burst_builder` 既有 TB（历史 PASS） |
| TCA 通道选通字节流 | `SOURCE_PARITY_AUDIT.md`：`[0xE0,1<<ch]+STOP` 与 STM32 字节级一致 |
| PWM 频率参数 | `pca_init_sequencer.v:87` `PRE_SCALE = 25e6/(50*4096)-1 = 121` → 50 Hz |
| 灰度映射 | `haptic_matrix.c` `{0,1632,2448,3264,4080}`；RTL 同表 |

## 结论

**该项如实标注为"未执行"，不计入验收通过项。**
建议下一阶段以独立的 pipeline 级 TB 覆盖（需 AXI BFM）。
