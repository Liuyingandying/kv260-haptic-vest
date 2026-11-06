// ============================================================================
// tactile_v12_ctrl.v -- v1.2 系统集成控制器
//
// 职责（v1.1.1 单链之上新增的系统时序, 独立于 scheduler_fsm 的帧内状态机）:
//
//   commit_ack(PS 提交)
//     -> [恢复帧判定: failsafe 曾触发 -> 跳过 DD, 强制全刷]
//     -> Dirty Detector 启动（独占双读口 ~261 拍 ≈ 5.2us @50MHz）
//     <- dirty_valid
//     -> 锁存 dirty_bitmap_q / dirty_valid_q
//     -> frame_ready 脉冲启动 Scheduler（带 dirty/force_off 上下文）
//     <- frame_done
//     -> 回 IDLE
//
//   Failsafe 边界 A（超时无新帧）: force_off 0->1（trip）即使无 commit 也
//     主动发起一次 mask=FFFF/force_off=1 的全 16 块刷新 —— 16 片 PCA9685
//     全部收到 FULL_OFF 事务, 外设不会保持旧 PWM。
//   Failsafe 边界 B（恢复后 dirty=0）: force_off 1->0 发生在 commit_ack
//     （新帧提交清粘滞）——该帧若按 dirty 比较结果为 0 将保持 FULL_OFF,
//     错误; 故恢复帧强制 dirty_bitmap=FFFF 全刷一次, 之后恢复正常 dirty 策略。
//
//   dd_bypass=1: 跳过 DD, 每帧恒全刷（= v1.1 行为, 回退兼容）。
//
// 边界 C（上电首帧）: 复位后 act bank 全 0, 但外部 PCA9685 可能残留 PWM
//   （PL 重配/PS 重启场景）—— 首个 commit 强制 dirty_bitmap=FFFF 全刷一次
//   （写帧内容, 非 force_off）, 之后恢复正常 dirty 策略。
// 边界 A 增强: trip 边沿落在帧进行中（S_DD/S_WAIT）时置 trip_pending,
//   回 IDLE 后补发强制全断扫 —— 边沿检测跨状态不丢事件。
//
// 优先级: commit_ack 同拍压过 failsafe trip（新帧 = 系统复活, 撤销安全态,
//   与 failsafe_timer 内部 frame_valid 清 force_off 的语义一致）。
// ============================================================================

`timescale 1ns / 1ps

module tactile_v12_ctrl (
    input  wire        clk,
    input  wire        rst_n,

    // ---- 来自 Matrix Buffer AXI 封装 ----
    input  wire        commit_ack,       // 1 拍: PS 帧提交（换 bank 完成）
    // ---- 与 Failsafe Timer ----
    input  wire        fs_force_off,     // 粘滞强制态
    output wire        frame_valid_fs,   // -> failsafe.frame_valid（= commit_ack）
    // ---- 与 Dirty Detector ----
    output reg         dd_start,         // 1 拍: 启动 DD 扫描
    input  wire        dd_busy,
    input  wire        dd_dirty_valid,   // 1 拍: 扫描结果有效
    input  wire [15:0] dd_dirty_bitmap,
    output wire        dd_active,        // 1 = DD 拥有 Matrix Buffer 读口
    // ---- 到 Scheduler FSM v1.2 ----
    output reg         frame_ready,      // 1 拍: 启动帧扫描
    output reg         sched_dirty_valid,
    output reg  [15:0] sched_dirty_bitmap,
    output reg         sched_force_off,
    input  wire        frame_done,       // 1 拍: 帧扫描完成
    input  wire        sched_busy,
    // ---- 观测/配置 ----
    input  wire        dd_bypass,        // 1 = 跳过 DD 恒全刷（v1.1 回退）
    output reg         recovery_frame,   // 粘滞观测: 恢复帧发生过
    output reg         fs_refresh_frame, // 粘滞观测: 超时强制全断帧发生过
    output reg         first_frame_seen  // 粘滞观测: 上电首帧强制全刷发生过
);

    // ------------------------------------------------------------------------
    // 状态: IDLE -> DD_RUN -> SCHED -> WAIT -> IDLE
    //   另: failsafe trip 在 IDLE 直接进入 SCHED（跳过 DD, mask=FFFF+force_off）
    //   trip 发生在帧进行中(S_WAIT/S_DD)时置 trip_pending, 回 IDLE 后补发
    //   强制全断扫（边沿检测跨状态不丢事件）
    // ------------------------------------------------------------------------
    localparam S_IDLE  = 2'd0,
               S_DD    = 2'd1,
               S_SCHED = 2'd2,
               S_WAIT  = 2'd3;

    reg [1:0]  st;
    reg        fs_d;               // fs_force_off 前拍采样（恢复判定）
    reg        force_full_q;       // 本帧强制全刷（恢复帧 / bypass 帧）
    reg [15:0] bitmap_q;
    reg        force_off_q;        // 本帧 force_off 上下文
    reg        trip_pending;       // 非空闲态错过的 trip 边沿（IDLE 补服务）
    reg        first_frame_q;      // 上电首帧标志（首帧强制全刷, 防 PCA 残留 PWM）

    wire trip = fs_force_off & ~fs_d;    // failsafe 0->1 上升沿

    assign frame_valid_fs = commit_ack;
    assign dd_active      = dd_busy || (st == S_DD);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            st                <= S_IDLE;
            fs_d              <= 1'b0;
            force_full_q      <= 1'b0;
            bitmap_q          <= 16'hFFFF;
            force_off_q       <= 1'b0;
            dd_start          <= 1'b0;
            frame_ready       <= 1'b0;
            sched_dirty_valid <= 1'b0;
            sched_dirty_bitmap<= 16'hFFFF;
            sched_force_off   <= 1'b0;
            recovery_frame    <= 1'b0;
            fs_refresh_frame  <= 1'b0;
            first_frame_seen  <= 1'b0;
            trip_pending      <= 1'b0;
            first_frame_q     <= 1'b1;
        end else begin
            dd_start     <= 1'b0;
            frame_ready  <= 1'b0;
            fs_d         <= fs_force_off;
            // trip 边沿可能落在 DD/SCHED/WAIT 期间: 先挂起, IDLE 补服务
            if (trip)
                trip_pending <= 1'b1;

            case (st)
                S_IDLE: begin
                    // 边界 A: failsafe 超时触发（无新帧也必须主动全断刷新）
                    if ((trip || trip_pending) && !commit_ack) begin
                        trip_pending      <= 1'b0;
                        force_full_q      <= 1'b1;
                        bitmap_q          <= 16'hFFFF;
                        force_off_q       <= 1'b1;
                        sched_dirty_valid <= 1'b1;
                        sched_dirty_bitmap<= 16'hFFFF;
                        sched_force_off   <= 1'b1;
                        fs_refresh_frame  <= 1'b1;
                        frame_ready       <= 1'b1;
                        st                <= S_SCHED;
                    end
                    // 正常帧提交
                    else if (commit_ack) begin
                        trip_pending <= 1'b0;   // 帧复活优先, 恢复帧覆盖安全需求
                        first_frame_q<= 1'b0;
                        // 边界 B: 恢复帧（commit 前 failsafe 处于触发态）
                        // -> 强制全刷, 跳过 DD
                        if (fs_d) begin
                            force_full_q      <= 1'b1;
                            bitmap_q          <= 16'hFFFF;
                            force_off_q       <= 1'b0;
                            sched_dirty_valid <= 1'b1;
                            sched_dirty_bitmap<= 16'hFFFF;
                            sched_force_off   <= 1'b0;
                            recovery_frame    <= 1'b1;
                            frame_ready       <= 1'b1;
                            st                <= S_SCHED;
                        end
                        // 上电首帧: act bank 为复位全 0, 但外部 PCA9685 可能残留
                        // PWM（PL 重配/PS 重启场景）-> 强制全刷一次, 跳过 DD
                        else if (first_frame_q) begin
                            force_full_q      <= 1'b1;
                            bitmap_q          <= 16'hFFFF;
                            force_off_q       <= 1'b0;
                            sched_dirty_valid <= 1'b1;
                            sched_dirty_bitmap<= 16'hFFFF;
                            sched_force_off   <= 1'b0;
                            first_frame_seen  <= 1'b1;
                            frame_ready       <= 1'b1;
                            st                <= S_SCHED;
                        end
                        // bypass: 恒全刷（v1.1 行为, dv=0 即全刷）
                        else if (dd_bypass) begin
                            force_full_q      <= 1'b1;
                            bitmap_q          <= 16'hFFFF;
                            force_off_q       <= 1'b0;
                            sched_dirty_valid <= 1'b0;
                            sched_dirty_bitmap<= 16'hFFFF;
                            sched_force_off   <= 1'b0;
                            frame_ready       <= 1'b1;
                            st                <= S_SCHED;
                        end
                        // 正常: Dirty Detector 先行
                        else begin
                            dd_start     <= 1'b1;
                            force_full_q <= 1'b0;
                            st           <= S_DD;
                        end
                    end
                end

                // Dirty Detector 扫描中（独占双读口; Scheduler 保持 IDLE）
                S_DD: begin
                    if (dd_dirty_valid) begin
                        bitmap_q          <= dd_dirty_bitmap;
                        force_off_q       <= 1'b0;
                        sched_dirty_valid <= 1'b1;
                        sched_dirty_bitmap<= dd_dirty_bitmap;
                        sched_force_off   <= 1'b0;
                        frame_ready       <= 1'b1;
                        st                <= S_SCHED;
                    end
                end

                // 帧扫描进行中
                S_SCHED: begin
                    frame_ready <= 1'b0;
                    st          <= S_WAIT;
                end

                // 等帧完成（流控下 ~24.8ms/帧; 无从机 NACK 不影响时序）
                S_WAIT: begin
                    if (frame_done) begin
                        sched_dirty_valid <= 1'b0;
                        sched_force_off   <= 1'b0;
                        force_full_q      <= 1'b0;
                        st                <= S_IDLE;
                    end
                end

                default: st <= S_IDLE;
            endcase
        end
    end

endmodule
