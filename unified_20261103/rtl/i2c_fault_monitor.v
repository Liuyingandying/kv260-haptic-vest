// ============================================================================
// i2c_fault_monitor.v -- per-block I2C 故障语义监视器 (v1.6, 显式 sideband 版)
//
// 权威语义 = 23_EEPROM haptic_matrix.h:27-77 + Service/AllOff/ServiceOffPending
//
// 判定输入 = **显式 sideband 元数据**(审计 Gate 3 要求, 禁止从地址反查):
//   sb_start     master 接受 START 后的归属脉冲
//   sb_blk[3:0]  事务所属块号(INIT=sequencer.cur_blk, runtime=sched.current_block)
//   sb_kind[2:0] 0=TCA_SELECT 1=PCA_INIT 2=PCA_MODE 3=PCA_BURST 4=ALL_OFF
//   sb_done      master 有效终结反馈后的脉冲(ACK/timeout 同拍锁存)
//   sb_ack_err   该事务的 ACK 结果
//
// 语义:
//   init 阶段(init_done=0): PCA 事务 NACK -> init_error_mask[b]=1,
//                            online_mask[b]=0 (TCA 事务 NACK 不判块)
//   runtime(init_done=1):    PCA 事务 NACK -> runtime_error_mask[b]=1,
//                            off_pending_mask[b]=1, fault_latched=1
//                            (阻断新 commit; AllOff/failsafe 重试不受阻)
//   重试成功: ALL_OFF kind 的 sb_done 且 !ack_err 且 off_pending[b]=1
//             -> off_pending[b]=0, off_retry_count++
//   online 从 0 起, 只有该块完整初始化成功才能置位。
//   首次故障置全矩阵 off_pending; raw fault_clear 不能直接解锁。
//   仅由 recovery 接纳 init_restart, 全初始化成功后解除 fault。
// ============================================================================
`timescale 1ns / 1ps

module i2c_fault_monitor (
    input  wire        clk,
    input  wire        rst_n,

    input  wire        init_done,        // 0=INIT 阶段, 1=runtime
    input  wire        fault_clear,      // compatibility port; recovery admits requests
    input  wire        init_restart, init_healthy,
    input  wire        sb_init_last, sb_init_global,

    input  wire        sb_start,
    input  wire [3:0]  sb_blk,
    input  wire [2:0]  sb_kind,
    input  wire        sb_done,
    input  wire        sb_ack_err,

    output reg  [15:0] online_mask,
    output reg  [15:0] init_error_mask,
    output reg  [15:0] runtime_error_mask,
    output reg  [15:0] off_pending_mask,
    output reg         fault_latched,
    output reg  [31:0] i2c_error_count,
    output reg  [31:0] off_retry_count
);

    reg  [3:0]  active_blk;
    reg  [2:0]  active_kind;
    reg active_init, active_init_last, active_init_global, reinit_armed;
    localparam [2:0] K_TCA_SELECT = 3'd0,
                     K_PCA_INIT   = 3'd1,
                     K_PCA_MODE   = 3'd2,
                     K_PCA_BURST  = 3'd3,
                     K_ALL_OFF    = 3'd4;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            online_mask        <= 16'h0000;
            init_error_mask    <= 16'h0000;
            runtime_error_mask <= 16'h0000;
            off_pending_mask   <= 16'h0000;
            fault_latched      <= 1'b0;
            i2c_error_count    <= 32'd0;
            off_retry_count    <= 32'd0;
            active_blk <= 0; active_kind <= 0;
            active_init <= 0; active_init_last <= 0; active_init_global <= 0;
            reinit_armed <= 0;
        end else begin
            // Raw software clear cannot certify recovery. The recovery controller
            // admits restart only after all digital FULL_OFF blocks completed.
            if (init_restart === 1'b1) begin
                online_mask <= 0; init_error_mask <= 0; reinit_armed <= 1;
            end
            if (reinit_armed && init_done && (init_healthy === 1'b1)) begin
                fault_latched <= 0; runtime_error_mask <= 0; reinit_armed <= 0;
            end

            // sideband 起始拍锁存归属元数据(原版嵌套错误: 被 fault_clear 吞掉)
            if (sb_start) begin
                active_blk  <= sb_blk;
                active_kind <= sb_kind;
                active_init <= !init_done;
                active_init_last <= (sb_init_last === 1'b1);
                active_init_global <= (sb_init_global === 1'b1);
            end

            if (sb_done && sb_ack_err) begin
                i2c_error_count <= i2c_error_count + 32'd1;
                if (active_init) begin
                    if (active_init_global) begin
                        init_error_mask <= 16'hFFFF; online_mask <= 0;
                    end else begin
                        init_error_mask[active_blk] <= 1'b1;
                        online_mask[active_blk] <= 1'b0;
                    end
                    off_pending_mask <= 16'hFFFF;
                    fault_latched <= 1'b1;
                end else begin
                    runtime_error_mask[active_blk] <= 1'b1;
                    if (!fault_latched) off_pending_mask <= 16'hFFFF;
                    else off_pending_mask[active_blk] <= 1'b1;
                    fault_latched                   <= 1'b1;
                end
                // TCA_SELECT NACK 亦按 active_blk 归属( INIT=sequencer.cur_blk,
                // runtime=sched.current_block —— v1.6 修订, 审计 Gate3 )
            end

            if (sb_done && !sb_ack_err && active_init && active_init_last &&
                !init_error_mask[active_blk]) online_mask[active_blk] <= 1'b1;

            // ---- ALL_OFF 重试成功: 清 off_pending ----
            if (sb_done && !sb_ack_err && !active_init && active_kind == K_ALL_OFF &&
                off_pending_mask[active_blk]) begin
                off_pending_mask[active_blk] <= 1'b0;
                off_retry_count              <= off_retry_count + 32'd1;
            end
        end
    end

endmodule
