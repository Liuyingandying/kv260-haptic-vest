// ============================================================================
// failsafe_timer.v -- 硬件帧看门狗: 无有效帧超时强制全断 (v1.2 安全机制)
//
// 工程   : AX7020 fpga/16x16 第四阶段 (v1.2)
// 背景   : STM32 原系统在服务循环内做 "500ms 无帧 -> AllOff" 兜底。该检查与
//          帧消费跑在同一个 CPU 上: 主循环卡死 / RTOS 死锁 / HardFault /
//          I2C 等待挂死时, 兜底代码与故障同源同灭。本模块将该机制下沉为
//          FPGA 纯硬件: 独立计数、独立判定, 唯一信任输入是 frame_valid。
//
// 语义（与 STM32 原机制对齐 + 两处硬件强化）:
//   frame_valid=1       -> 计数器清零, force_off 立即恢复 0（新帧即恢复）
//   持续无有效帧        -> 计数器每时钟递增（与任何软件/总线状态无关）
//   计满 TIMEOUT_MS     -> 超时成立, 于刷新安全边界置 force_off=1（粘滞）
//   新帧到达            -> force_off 撤销（同时重新布防）
//
// 硬件强化 1 —— 安全边界置位:
//   超时恰逢 refresh_active=1（最后一帧的刷新仍在途）时, 置位推迟到刷新
//   结束, 避免 force_off 的消费方在 I2C 半事务中途抢总线造成寄存器撕裂。
//   推迟上限 DEFER_MS: 若刷新管线卡死（如 I2C 总线 SDA 卡低导致 builder
//   永久 busy、scheduler 停在 S_NEXT）, 到限强制置位 —— 看门狗不可被
//   卡死的管线永久屏蔽（这正是软件兜底最容易被饿死的场景）。
//
// 硬件强化 2 —— 粘滞输出:
//   force_off 置位后保持, 直到下一帧; 计数器饱和不回绕。超时沿与帧沿
//   同拍冲突时帧优先（收到新帧 = 系统复活, 撤销安全态）。
//
// 复位行为:
//   force_off=0, 计数器自复位释放起计时。首帧前管线为空（未向 PCA9685
//   写过任何 duty, 上电默认全断）, 无不安全窗口; 且避免 "复位后即
//   force_off=1 -> 门蔽 frame_ready -> 恢复后首帧被丢" 的竞态。若 500ms
//   内始终无首帧, 看门狗照样触发（安全默认成立）。
//
// 集成（v1.2.1 计划, Scheduler 主体零改动）:
//   frame_valid   <- mb_commit_ack（原始提交脉冲, 非门蔽后信号）
//   refresh_active<- sched_busy
//   force_off     -> scheduler_fsm 向后兼容端口追加 force_off_i
//                    （v1.1 busy_o/blk_go_i 同款追加范式）, 在 OUTPUT 拍
//                    作 current_full_off |= force_off_i 数据掩蔽 —— 不停
//                    扫描、零丢帧; 可选并行驱动 PCA9685 OE# 引脚做硬件
//                    级全断（不经 I2C, 总线挂死仍生效）。
// ============================================================================

`timescale 1ns / 1ps

module failsafe_timer #(
    parameter integer CLK_FREQ   = 50_000_000,   // 时钟频率 Hz
    parameter integer TIMEOUT_MS = 500,          // 无帧超时阈值 ms（0 = 立即超时）
    parameter integer DEFER_MS   = 50            // 超时后等待刷新安全边界的宽限 ms
                                                 //   （约 2 个 40Hz 帧周期; 管线
                                                 //    卡死防御, 防看门狗被屏蔽）
)(
    input  wire        clk,
    input  wire        rst_n,        // 低有效（与既有模块一致）

    input  wire        frame_valid,  // 有效帧事件: 电平或 1 拍脉冲均可
                                     //   （顶层接 mb_commit_ack 原始信号）
    input  wire        refresh_active, // 刷新进行中（顶层接 sched_busy）:
                                     //   超时置位推迟至其撤销（安全边界）
    output reg         force_off     // 1 = 强制全断（粘滞, 新帧撤销）
);

    // ------------------------------------------------------------------------
    // 阈值换算与位宽
    // 注: 先除后乘（CLK_FREQ/1000 = kHz 值）避免 32 位 integer 溢出 —— 直接写
    // TIMEOUT_MS*CLK_FREQ/1000 时 500ms@50MHz = 250 亿 > 2^31, 溢出成负值,
    // 看门狗将永不触发（tb T3 用例曾就此抓出）。要求 CLK_FREQ 为 1000 整数倍。
    // ------------------------------------------------------------------------
    localparam integer TIMEOUT_CYCLES = TIMEOUT_MS * (CLK_FREQ / 1000); // 默认 25e6 = 500ms @50MHz
    localparam integer DEFER_CYCLES   = DEFER_MS  * (CLK_FREQ / 1000);  // 默认 2.5e6 = 50ms
    localparam integer CNT_W = $clog2(TIMEOUT_CYCLES + 1);
    localparam integer DFR_W = $clog2(DEFER_CYCLES + 1);

    reg [CNT_W-1:0] cnt;         // 无帧计时（到点饱和, 不回绕）
    reg [DFR_W-1:0] defer_cnt;   // 超时后等待安全边界的宽限计时（同上）

    // 超时与置位条件
    wire timeout_reached = (cnt >= TIMEOUT_CYCLES);
    wire defer_expired   = (defer_cnt >= DEFER_CYCLES);
    // 置位三条件: 超时 且 未在强制态 且 (刷新已停 或 宽限到限)
    wire trip            = timeout_reached && !force_off &&
                           (!refresh_active || defer_expired);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cnt       <= {CNT_W{1'b0}};
            defer_cnt <= {DFR_W{1'b0}};
            force_off <= 1'b0;   // 见头部 "复位行为": 首帧前管线为空, 无安全窗口
        end else if (frame_valid) begin
            // 新帧最高优先级: 清计时、撤销强制态（重新布防）
            cnt       <= {CNT_W{1'b0}};
            defer_cnt <= {DFR_W{1'b0}};
            force_off <= 1'b0;
        end else begin
            // 宽限计时: 仅超时后且刷新仍活跃时推进, 到限饱和
            if (timeout_reached && refresh_active && !defer_expired)
                defer_cnt <= defer_cnt + 1'b1;
            // 无帧自由计数, 到点饱和
            if (!timeout_reached)
                cnt <= cnt + 1'b1;
            // 粘滞置位（一旦置位, 在下一帧前保持）
            if (trip)
                force_off <= 1'b1;
        end
    end

endmodule
