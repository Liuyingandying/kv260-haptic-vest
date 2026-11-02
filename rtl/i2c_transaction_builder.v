// ============================================================================
// i2c_transaction_builder.v -- Scheduler 通道数据 -> I2C 字节序列组装器
//
// 工程   : AX7020 fpga/16x16 第八阶段（I2C Master 第一阶段）
//
// 事务 1（可选, sel_needed=1）: TCA9548 通道选通
//   [0xE0(0x70<<1|W), mask=1<<tca_ch] + STOP
// 事务 2: PCA9685 单通道 LED0 写（本阶段验证用, 寄存器自 0x06 auto-increment）
//   [{pca_addr,1'b0}(W), 0x06, ON_L=0, ON_H=0, OFF_L, OFF_H] + STOP
//   OFF_H = {3'b000, full_off, channel_data[11:8]}:
//     full_off=1 -> bit4=FULL_OFF 位(0x10, PCA9685 全断语义, 非 0 占空比)
//     full_off=0 -> 12bit duty 低 12 位
//
// 与 i2c_master 握手: 等待主机空闲 -> cmd_start 脉冲 -> 逐字节 valid/ready
// -> 事务末字节带 byte_stop -> 等 m_done; 两个事务之间自动再起 START。
// ============================================================================

`timescale 1ns / 1ps

module i2c_transaction_builder (
    input  wire        clk,
    input  wire        rst_n,
    // ---- 来自 Scheduler（每通道）----
    input  wire        trig,           // 1 拍: 为本通道组装并发出事务
    input  wire [3:0]  block_id,       // 保留: 块级调度（本阶段字节生成不使用）
    input  wire [3:0]  tca_ch,
    input  wire [6:0]  pca_addr,
    input  wire [11:0] channel_data,
    input  wire        full_off,
    input  wire        sel_needed,     // TCA 通道相对上一块变化时置 1
    // ---- 到 i2c_master ----
    output reg         m_cmd_start,
    output reg         m_byte_valid,
    output reg  [7:0]  m_byte_data,
    output reg         m_byte_stop,
    input  wire        m_byte_ready,
    input  wire        m_busy,
    input  wire        m_done,
    // ---- 状态 ----
    output reg         busy,
    output reg         done            // 1 拍: 全部事务完成
);

    localparam B_IDLE     = 3'd0,
               B_WAITBUS  = 3'd1,
               B_STARTP   = 3'd2,
               B_SEND     = 3'd3,
               B_WAITDONE = 3'd4;

    reg [2:0] bst;
    reg [3:0] idx;                       // 0..1 = SEL 字节, 2..7 = PCA 字节
    reg       in_sel;                    // 当前在发 SEL 事务
    reg       sel_pend;
    reg [3:0] tca_r;
    reg [6:0] pca_r;
    reg [11:0] data_r;
    reg       foff_r;

    // 字节表（独立函数, 便于比对）
    function [7:0] byte_at(input [3:0] k);
        begin
            if (in_sel)
                byte_at = (k == 4'd0) ? 8'hE0 : (8'h01 << tca_r);
            else begin
                case (k)
                    4'd2:    byte_at = {pca_r, 1'b0};              // 地址+W
                    4'd3:    byte_at = 8'h06;                      // LED0_ON_L
                    4'd4:    byte_at = 8'h00;                      // ON_L
                    4'd5:    byte_at = 8'h00;                      // ON_H
                    4'd6:    byte_at = data_r[7:0];                // OFF_L
                    default: byte_at = {3'b000, foff_r, data_r[11:8]}; // OFF_H(+FULL_OFF)
                endcase
            end
        end
    endfunction

    function stop_at(input [3:0] k);
        stop_at = in_sel ? (k == 4'd1) : (k == 4'd7);
    endfunction

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bst          <= B_IDLE;
            idx          <= 4'd0;
            in_sel       <= 1'b0;
            sel_pend     <= 1'b0;
            tca_r        <= 4'd0;
            pca_r        <= 7'h00;
            data_r       <= 12'h000;
            foff_r       <= 1'b0;
            m_cmd_start  <= 1'b0;
            m_byte_valid <= 1'b0;
            m_byte_data  <= 8'h00;
            m_byte_stop  <= 1'b0;
            busy         <= 1'b0;
            done         <= 1'b0;
        end else begin
            done <= 1'b0;

            case (bst)
                B_IDLE: begin
                    if (trig) begin
                        tca_r      <= tca_ch;
                        pca_r      <= pca_addr;
                        data_r     <= channel_data;
                        foff_r     <= full_off;
                        sel_pend   <= sel_needed;
                        busy       <= 1'b1;
                        bst        <= B_WAITBUS;
                    end
                end

                // 等主机空闲再发 START
                B_WAITBUS: begin
                    if (!m_busy) begin
                        m_cmd_start <= 1'b1;
                        bst         <= B_STARTP;
                    end
                end

                // cmd_start 保持 1 拍后清除; 装载首字节
                B_STARTP: begin
                    m_cmd_start <= 1'b0;
                    in_sel      <= sel_pend;
                    idx         <= sel_pend ? 4'd0 : 4'd2;
                    m_byte_data <= sel_pend ? 8'hE0 : {pca_r, 1'b0};
                    m_byte_stop <= 1'b0;
                    m_byte_valid<= 1'b1;
                    bst         <= B_SEND;
                end

                // 字节流握手: 主机取走 1 字节后推进; 事务末字节带 stop
                B_SEND: begin
                    if (m_byte_ready && m_byte_valid) begin
                        if (stop_at(idx)) begin
                            m_byte_valid <= 1'b0;
                            bst          <= B_WAITDONE;
                        end else begin
                            idx         <= idx + 4'd1;
                            m_byte_data <= byte_at(idx + 4'd1);
                            m_byte_stop <= stop_at(idx + 4'd1);
                        end
                    end
                end

                // 等主机 STOP 完成; SEL 后接 PCA 事务, PCA 后结束
                B_WAITDONE: begin
                    if (m_done) begin
                        if (in_sel) begin
                            sel_pend <= 1'b0;
                            bst      <= B_WAITBUS;
                        end else begin
                            busy <= 1'b0;
                            done <= 1'b1;
                            bst  <= B_IDLE;
                        end
                    end
                end

                default: bst <= B_IDLE;
            endcase
        end
    end

endmodule
