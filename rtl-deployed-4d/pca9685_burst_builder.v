// ============================================================================
// pca9685_burst_builder.v -- PCA9685 块级 64B auto-increment 突发事务组装器
//
// 工程   : AX7020 fpga/16x16 第九阶段
// 来源   : STM32 HapticVest256 每块刷新语义迁移（行为等价, 未复制代码）:
//   pca9685.c:192  PCA9685_WriteDuty16 = MODE1 重断言 + 64B 突发
//     事务 A: [{pca,1'b0}(W), 0x00(MODE1), 0x20(AI 重断言, ALLCALL 保持清除)] + STOP
//     事务 B: [{pca,1'b0}(W), 0x06(LED0_ON_L), 64B 连续] + STOP
//   pca9685.c:218  WriteDuty16Raw: 每通道 4 字节 {ON_L=0, ON_H=0, OFF_L, OFF_H}
//     OFF_H = {3'b000, full_off, duty[11:8]}: bit4 = FULL_OFF 全断位（level0 语义）
//     duty LUT 保证 full_off=1 时 duty=0 -> OFF_H=0x10, OFF_L=0x00, 与 STM32 一致
//
// 映射不变声明: 本模块不做任何 block->PCA 地址翻译, pca_addr 由调用方从
//   matrix_map_rom（HAPTIC_VEST_256_BLOCK_MAP 乱序表）取来原样使用。
//
// 使用: 先逐通道预装载（ch_wr/ch_idx/ch_duty/ch_full_off, 16 拍）,
//       再 block_trig 触发发送; 发送期间可预装载下一块（流水预留）。
// ============================================================================

`timescale 1ns / 1ps

module pca9685_burst_builder (
    input  wire        clk,
    input  wire        rst_n,
    // ---- 通道预装载（每通道 1 拍, 共 16 拍）----
    input  wire        ch_wr,
    input  wire [3:0]  ch_idx,
    input  wire [11:0] ch_duty,
    input  wire        ch_full_off,
    // ---- 块触发 ----
    input  wire        block_trig,     // 1 拍: 16 通道装载完毕, 发送本块
    input  wire [6:0]  pca_addr,       // 本块 PCA9685 7bit 地址（乱序表来）
    input  wire        mode1_en,       // 1 = 事务 A（MODE1 重断言, STM32 默认）
    // ---- 到 i2c_master ----
    output reg         m_cmd_start,
    output reg         m_byte_valid,
    output reg  [7:0]  m_byte_data,
    output reg         m_byte_stop,
    input  wire        m_byte_ready,
    input  wire        m_busy,
    input  wire        m_done,
    input  wire        m_failed,        // 与 m_done 同拍: ACK/timeout/abort 错误
    // ---- 状态 ----
    output reg         busy,
    output reg         done,           // 1 拍: 整块成功完成或错误终结
    output reg         blk_done        // 1 拍: 整块成功完成或错误终结
);

    // ------------------------------------------------------------------------
    // 16 通道存储
    // ------------------------------------------------------------------------
    reg [11:0] duty_q [0:15];
    reg [15:0] foff_q;                   // 位 n = 通道 n 的 FULL_OFF 标志

    localparam [2:0] W_IDLE     = 3'd0,
                     W_WAITBUS  = 3'd1,
                     W_SETUP    = 3'd2,
                     W_SEND     = 3'd3,
                     W_WAITDONE = 3'd4;

    reg [2:0] bst;
    reg [6:0] idx;                       // 0..2 = 事务 A; 3..68 = 事务 B
    reg [6:0] pca_r;
    reg       in_a;                      // 正在发事务 A（MODE1）
    reg       mode1_r;

    // 字节生成（独立函数, 便于 TB 比对）
    //   事务 A: k0=地址W, k1=0x00(MODE1), k2=0x20(AI)
    //   事务 B: k3=地址W, k4=0x06(LED0_ON_L), k5..68 = 通道数据
    //     pos = k-5 (0..63): c = pos[6:2]（通道）, sub = pos[1:0]（字节内）
    function [7:0] byte_at(input [6:0] k);
        reg [6:0] pos;
        reg [3:0] c;
        reg [1:0] sub;
        begin
            if (in_a) begin
                case (k)
                    7'd0:    byte_at = {pca_r, 1'b0};
                    7'd1:    byte_at = 8'h00;                       // MODE1
                    default: byte_at = 8'h20;                       // AI 重断言
                endcase
            end else begin
                case (k)
                    7'd3:    byte_at = {pca_r, 1'b0};
                    7'd4:    byte_at = 8'h06;                       // LED0_ON_L
                    default: begin
                        pos = k - 7'd5;
                        c   = pos[6:2];
                        sub = pos[1:0];
                        case (sub)
                            2'd0:    byte_at = 8'h00;               // ON_L
                            2'd1:    byte_at = 8'h00;               // ON_H
                            2'd2:    byte_at = duty_q[c][7:0];      // OFF_L
                            default: byte_at = {3'b000, foff_q[c], duty_q[c][11:8]}; // OFF_H
                        endcase
                    end
                endcase
            end
        end
    endfunction

    function stop_at(input [6:0] k);
        stop_at = in_a ? (k == 7'd2) : (k == 7'd68);
    endfunction

    // ------------------------------------------------------------------------
    // 发送状态机（结构与 i2c_transaction_builder 同型: 已验证的握手时序）
    // ------------------------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bst          <= W_IDLE;
            idx          <= 7'd0;
            pca_r        <= 7'h00;
            in_a         <= 1'b0;
            mode1_r      <= 1'b0;
            m_cmd_start  <= 1'b0;
            m_byte_valid <= 1'b0;
            m_byte_data  <= 8'h00;
            m_byte_stop  <= 1'b0;
            busy         <= 1'b0;
            done         <= 1'b0;
            blk_done     <= 1'b0;
        end else begin
            done <= 1'b0;
            blk_done <= 1'b0;

            // 通道预装载（任意时刻可写, 含发送期间 -> 流水预留）
            if (ch_wr) begin
                duty_q[ch_idx] <= ch_duty;
                foff_q[ch_idx] <= ch_full_off;
            end

            // 早停可能发生在 SEND (含 TCA 注入错误), 不能只在 WAITDONE 接 done。
            if (m_done && (bst != W_IDLE) &&
                ((m_failed === 1'b1) || (bst != W_WAITDONE))) begin
                bst          <= W_IDLE;
                idx          <= 7'd0;
                in_a         <= 1'b0;
                m_cmd_start  <= 1'b0;
                m_byte_valid <= 1'b0;
                m_byte_stop  <= 1'b0;
                busy         <= 1'b0;
                done         <= 1'b1;
                blk_done     <= 1'b1;
            end else case (bst)
                W_IDLE: begin
                    if (block_trig) begin
                        pca_r   <= pca_addr;
                        mode1_r <= mode1_en;
                        in_a    <= mode1_en;   // 首事务 = MODE1（mode1_en=1 时）
                        busy    <= 1'b1;
                        bst     <= W_WAITBUS;
                    end
                end

                // 等主机空闲再发 START
                W_WAITBUS: begin
                    if (!m_busy) begin
                        $display("[BLD] t=%0t W_WAITBUS: master idle, cmd_start", $time);
                        m_cmd_start <= 1'b1;
                        bst         <= W_SETUP;
                    end
                end

                // cmd_start 保持 1 拍; 装载首字节（两事务首字节均为地址W）
                W_SETUP: begin
                    m_cmd_start  <= 1'b0;
                    // in_a 已由 W_IDLE/W_WAITDONE 正确设置:
                    //   MODE1 事务 in_a=1 (idx 0..2), 突发事务 in_a=0 (idx 3..68)
                    idx          <= in_a ? 7'd0 : 7'd3;
                    m_byte_data  <= {pca_r, 1'b0};
                    m_byte_stop  <= 1'b0;
                    m_byte_valid <= 1'b1;
                    bst          <= W_SEND;
                end

                // 字节流握手
                W_SEND: begin
                    if (m_byte_ready && m_byte_valid) begin
                        if (stop_at(idx)) begin
                            m_byte_valid <= 1'b0;
                            bst          <= W_WAITDONE;
                        end else begin
                            idx         <= idx + 7'd1;
                            m_byte_data <= byte_at(idx + 7'd1);
                            m_byte_stop <= stop_at(idx + 7'd1);
                        end
                    end
                end

                // 等主机 STOP 完成; A 后接 B, B 后结束
                W_WAITDONE: begin
                    if (m_done)
                        $display("[BLD] t=%0t W_WAITDONE saw m_done", $time);
                    if (m_done) begin
                        if (in_a) begin
                            in_a <= 1'b0;
                            bst  <= W_WAITBUS;
                        end else begin
                            busy <= 1'b0;
                            done <= 1'b1;
                            blk_done <= 1'b1;
                            bst  <= W_IDLE;
                        end
                    end
                end

                default: bst <= W_IDLE;
            endcase
        end
    end

endmodule
