// ============================================================================
// block_refresh_buffer.v -- 块级刷新缓冲: Scheduler 通道流 -> PCA9685 块突发
//
// 工程   : AX7020 fpga/16x16 第十阶段（系统级连接）
//
// 职责   : 采集 Scheduler 的逐通道刷新数据（block 0~15 × channel 0~15 顺序流）,
//          按块缓存; block_commit 时整块交接给 pca9685_burst_builder
//          （16 拍 ch_wr 预装载 + block_trig 触发 64B 突发）。
//
// 双缓冲防污染（核心设计）:
//   cap 侧: 持续采集新块（首个通道定义 block_id 与 pca_addr）
//   hold 侧: 已提交的完整块, 卸载期间保持稳定
//   - burst 进行中到达的新块数据只进 cap, 不可能污染 hold -> I2C 波形稳定
//   - builder 完成时若 cap 已集齐且之前 commit 过（pend_swap）-> 自动交接下一块
//
// 块切换/异常策略（最新帧优先）:
//   - 不完整块被新块的通道覆盖 -> 丢弃旧块, seq_err 计数, 以新块重新采集
//   - 完整未提交块被新块覆盖   -> drop_err 计数, 以新块重新采集
//   - 未集齐时收到 commit      -> commit_err 计数, 不触发突发
//   上述计数为粘滞统计, 供上层（AXI STATUS/演示）读取。
//
// 附加输入 pca_addr_in: 随通道流捕获（上层从 map ROM 取, 与 block_id 同源）,
//   卸载时驱动 builder.pca_addr —— 保持 builder 接口与地址表不变。
// ============================================================================

`timescale 1ns / 1ps

module block_refresh_buffer (
    input  wire        clk,
    input  wire        rst_n,
    // ---- 来自 Scheduler（逐通道, out_valid 对应 ch_valid）----
    input  wire        ch_valid,
    input  wire [3:0]  block_id,
    input  wire [3:0]  channel_id,
    input  wire [11:0] pwm_value,
    input  wire        full_off,
    input  wire [6:0]  pca_addr_in,   // 与通道流同源（map ROM 乱序表）
    // ---- 块提交 ----
    input  wire        block_commit,  // 1 拍: 请求把 cap 完整块交接给 builder
    // ---- 来自 builder（占用/完成, 防 mid-burst 污染的关键）----
    input  wire        bld_busy,      // builder 忙（含 MODE1/burst 两事务）期间禁止 swap
    input  wire        bld_done,      // 1 拍: 接 builder.blk_done（整块完成才释放 hold）
    // ---- 到 pca9685_burst_builder ----
    output reg         ch_wr,         // 16 拍预装载流（burst_start 的装载段）
    output reg  [3:0]  ch_idx,
    output reg  [11:0] ch_duty,
    output reg         ch_full_off,
    output reg         block_trig,    // 1 拍: 触发 64B 突发（burst_start）
    output reg  [6:0]  bld_pca_addr,  // builder.pca_addr（当前 hold 块）
    // ---- 状态 ----
    output reg  [3:0]  ready_block_id,// cap 完整时 = 待提交块号; 否则 hold 块号
    output reg         block_ready,   // cap 已集齐 16 通道（电平）
    output wire        busy,          // 卸载进行中
    output reg  [7:0]  seq_err,       // 不完整块被覆盖
    output reg  [7:0]  drop_err,      // 完整未提交块被覆盖
    output reg  [7:0]  commit_err     // 未集齐时收到 commit
);

    // ------------------------------------------------------------------------
    // 采集侧（cap）
    // ------------------------------------------------------------------------
    reg [191:0] cap_duty_v;              // 16 x 12bit, ch n @ [12n+11:12n]
    reg [15:0]  cap_foff_v;
    reg [15:0]  cap_mask;                // 通道就绪掩码
    reg [3:0]   cap_blk;
    reg [6:0]   cap_pca;
    reg         cap_active;

    // ------------------------------------------------------------------------
    // 保持侧（hold, 已提交待发/发送中）
    // ------------------------------------------------------------------------
    reg [191:0] hold_duty_v;
    reg [15:0]  hold_foff_v;
    reg [3:0]   hold_blk;
    reg [6:0]   hold_pca;
    reg         hold_valid;
    reg         pend_swap;               // builder 忙时收到 commit 的排队标志

    wire cap_complete = cap_active && (cap_mask == 16'hFFFF);

    // ------------------------------------------------------------------------
    // 卸载引擎: 16 拍 ch_wr 预装载 + 1 拍 block_trig
    // ------------------------------------------------------------------------
    localparam [1:0] U_IDLE   = 2'd0,
                     U_STREAM = 2'd1,
                     U_TRIG   = 2'd2;

    reg [1:0] ust;
    reg [3:0] ul_idx;

    assign busy = (ust != U_IDLE);

    // 交接触发（严格防 mid-burst 污染）:
    //   hold_valid 从 swap 起保持到 bld_done（builder 全程读自己的存储副本）
    //   -> builder 忙期间 commit 只排队（pend）, 完成后才允许 swap
    wire commit_fire = block_commit && cap_complete && !hold_valid
                       && (ust == U_IDLE) && !bld_busy;
    wire auto_fire   = bld_done && pend_swap && cap_complete
                       && (ust == U_IDLE) && !bld_busy;
    wire start_unload = commit_fire | auto_fire;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ust         <= U_IDLE;
            ul_idx      <= 4'd0;
            ch_wr       <= 1'b0;
            ch_idx      <= 4'd0;
            ch_duty     <= 12'h000;
            ch_full_off <= 1'b0;
            block_trig  <= 1'b0;
            bld_pca_addr<= 7'h00;
            hold_valid  <= 1'b0;
            pend_swap   <= 1'b0;
            seq_err     <= 8'd0;
            drop_err    <= 8'd0;
            commit_err  <= 8'd0;
            cap_active  <= 1'b0;
            cap_mask    <= 16'h0000;
            cap_blk     <= 4'd0;
            cap_pca     <= 7'h00;
        end else begin
            // ---- 卸载引擎 ----
            case (ust)
                U_IDLE: begin
                    ch_wr      <= 1'b0;
                    block_trig <= 1'b0;
                    if (start_unload) begin
                        // 首拍: 新 hold 的 ch0（hold 正被 cap 覆盖, 取 cap 值等价）
                        ust         <= U_STREAM;
                        ul_idx      <= 4'd0;
                        ch_wr       <= 1'b1;
                        ch_idx      <= 4'd0;
                        ch_duty     <= cap_duty_v[11:0];
                        ch_full_off <= cap_foff_v[0];
                    end
                end

                U_STREAM: begin
                    if (ul_idx == 4'd15) begin
                        ust        <= U_TRIG;
                        ch_wr      <= 1'b0;
                        block_trig <= 1'b1;              // 触发 64B 突发
                    end else begin
                        ul_idx      <= ul_idx + 4'd1;
                        ch_idx      <= ul_idx + 4'd1;
                        ch_duty     <= hold_duty_v[(ul_idx + 4'd1) * 12 +: 12];
                        ch_full_off <= hold_foff_v[ul_idx + 4'd1];
                    end
                end

                U_TRIG: begin
                    block_trig <= 1'b0;
                    ust        <= U_IDLE;
                    // hold_valid 与 pend 的释放统一在 bld_done 时刻处理:
                    // builder 从 trig 到 done 期间仍持有本块副本
                end
                default: ust <= U_IDLE;
            endcase

            // ---- 交接/swap（含卸载首拍驱动）----
            if (start_unload) begin
                hold_duty_v <= cap_duty_v;
                hold_foff_v <= cap_foff_v;
                hold_blk    <= cap_blk;
                hold_pca    <= cap_pca;
                hold_valid  <= 1'b1;                 // 占用直至 bld_done
                bld_pca_addr<= cap_pca;
                cap_mask    <= 16'd0;
                cap_active  <= 1'b0;
            end else if (bld_done) begin
                hold_valid <= 1'b0;                  // builder 已完成, hold 释放
            end

            // ---- commit 采样 / pend 生命周期 ----
            if (block_commit) begin
                if (cap_complete) begin
                    if (!commit_fire)
                        pend_swap <= 1'b1;           // builder 忙: 排队
                    // commit_fire 的 swap 已在上方处理
                end else begin
                    commit_err <= commit_err + 8'd1; // 未集齐: 拒绝并计数
                end
            end else if (auto_fire) begin
                pend_swap <= 1'b0;                   // 排队块已交接
            end

            // ---- 通道采集（置于最后: 块切换/新块首通道覆盖 swap 的清零）----
            if (ch_valid) begin
                if (!cap_active || (block_id != cap_blk)) begin
                    // 块切换: 旧块按完整性计为 seq_err / drop_err（最新帧优先）
                    if (cap_active && cap_complete)
                        drop_err <= drop_err + 8'd1;
                    else if (cap_active)
                        seq_err  <= seq_err + 8'd1;
                    cap_active <= 1'b1;
                    cap_blk    <= block_id;
                    cap_pca    <= pca_addr_in;
                    cap_mask   <= 16'd0;
                end
                cap_duty_v[channel_id * 12 +: 12] <= pwm_value;
                cap_foff_v[channel_id]            <= full_off;
                cap_mask                          <= (cap_active && block_id == cap_blk)
                                                      ? (cap_mask | (16'd1 << channel_id))
                                                      : (16'd1 << channel_id);
            end
        end
    end

    // ------------------------------------------------------------------------
    // 状态输出
    // ------------------------------------------------------------------------
    always @* begin
        block_ready    = cap_complete;
        ready_block_id = cap_complete ? cap_blk :
                         (hold_valid ? hold_blk : cap_blk);
    end

endmodule
