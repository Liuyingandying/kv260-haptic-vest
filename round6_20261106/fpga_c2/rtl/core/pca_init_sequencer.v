// ============================================================================
// pca_init_sequencer.v -- PCA9685 上电初始化序列器 + I2C 主控仲裁 (v1.6)
//
// 权威序列 = 23_EEPROM pca9685.c PCA9685_Init(88) + CalcPrescale(26-53)
//   + WriteFullOffRaw(218)。字节流由小位宽事务描述确定性生成:
//
//   idx 0..1    TCA9548 mask=0      [E0, 00]        (ALLCALL 隔离)
//   每块 b=0..15, 块内偏移 k (块步长 83):
//     k=0..1    TCA select          [E0, 1<<tca(b)]   (tca9548_inject 同格式)
//     k=2..4    MODE1=SLEEP|AI      [pca|W, 00, 30]   (pca9685.c:104)
//     k=5..7    PRE_SCALE=121       [pca|W, FE, 79]   (CalcPrescale(50)=121)
//     k=8..73   FULL_OFF 64B        [pca|W, 06, {00,00,00,10}x16] (218-266)
//     k=74..76  MODE2=OUTDRV        [pca|W, 01, 04]   (pca9685.c:123)
//     k=77..79  MODE1=AI            [pca|W, 00, 20]   (pca9685.c:130-135 唤醒)
//     (1ms 延时, k=80 前)                             (pca9685.c:137)
//     k=80..82  MODE1=RESTART|AI    [pca|W, 00, A0]   (pca9685.c:138)
//
//   PRE_SCALE = (25e6/(50*4096)) - 1 = 122 - 1 = 121 (0x79), 即 50Hz。
//
// FSM 严格对齐 pca9685_burst_builder 已验证握手模板 (W_WAITBUS→W_SETUP→
//   W_SEND→W_WAITDONE): cmd_start 在 !m_busy 拍寄存置 1、下一拍清 0(严格
//   1 拍, i2c_master 仅 M_IDLE 采样); 首字节在 cmd_start 清零拍上 valid;
//   逐字节 valid/ready 握手; 末字节 ACK 后等 master done 脉冲再进下一段。
//   事务表: tn=0 为 mask=0; tn>=1: b=(tn-1)/7, s=(tn-1)%7, 113 笔事务,
//   s=5(wake) 完成后插 1ms 延时再发 s=6(RESTART)。
//
// 仲裁: 与 runtime 注入器共用 i2c_master。init 期间 i2c 侧归本模块,
//   runtime 注入器 ready 压 0(builder 停等无丢失), done 压 0(防 phantom);
//   init 完成后纯直通(与 v1.5 逐拍等价)。
// sideband 输出供 i2c_fault_monitor 显式归属(禁止地址反查):
//   sb_start=事务首字节拍 1 拍脉冲; sb_done=master done 拍次拍 1 拍脉冲
//   (与 sb_ack_err 同拍, 均经 init_busy 多路选择透传); 块号经 cur_blk 输出。
//   sb_kind: 0=TCA_SELECT 1=PCA_INIT
// ============================================================================
`timescale 1ns / 1ps

module pca_init_sequencer #(
    parameter integer CLK_FREQ = 50_000_000,
    parameter integer PWM_HZ   = 50,
    parameter [15:0] TARGET_MASK = 16'hffff,
    parameter integer STOP_ON_ERROR = 0
)(
    input  wire       clk,
    input  wire       rst_n,
    input  wire       start,            // 顶层接 1'b1
    input  wire       restart,          // accepted only when idle

    output wire       init_busy,
    output wire       init_done,        // attempt ended, not a healthy certificate
    output wire [3:0] cur_blk,
    output wire       cur_pca_txn,

    output reg        sb_start,
    output reg        sb_done,
    output reg        sb_ack_err,
    output reg  [2:0] sb_kind,          // 0=TCA_SELECT 1=PCA_INIT
    output wire       sb_init_last,
    output wire       sb_init_global,

    // ---- runtime 侧(来自 tca9548_inject 输出) ----
    input  wire       r_cmd_start,
    input  wire       r_valid,
    input  wire [7:0] r_data,
    input  wire       r_stop,
    output reg        r_ready,          // init 期间压 0
    output reg        r_done_gated,     // init 期间压 0(防 phantom done)

    // ---- i2c_master 侧 ----
    output reg        m_cmd_start,
    output reg        m_valid,
    output reg  [7:0] m_data,
    output reg        m_stop,
    input  wire       m_ready,
    input  wire       m_busy,
    input  wire       m_done,
    input  wire       m_ack_err
);

    // Phase 1F: DLY_1MS must fit the counter width. At 50 MHz it is 50_000
// (fits 16 bit); at KV260 96.968727 MHz it is 96_968 which OVERFLOWS a 16-bit
// counter and silently produced ~324 us instead of 1 ms. dly is now 32 bit.
localparam integer DLY_1MS   = CLK_FREQ / 1000;                     // 50000 @50MHz, 96968 @96.97MHz
    localparam [7:0]  TCA_ADDR_W = 8'hE0;                               // 0x70<<1
    localparam integer BLK_BYTES = 83;
    localparam integer BLKS      = 16;
    localparam integer TOTAL     = 2 + BLKS * BLK_BYTES;                // 1330
    localparam integer TXNS      = 1 + BLKS * 7;                        // 113
    localparam [6:0]  TXN_LAST   = TXNS - 1;                            // 112
    localparam [7:0]  PRE_SCALE  = (25_000_000 / (PWM_HZ * 4096)) - 1;  // 121

    function [7:0] pca_w(input [3:0] b);        // = matrix_map_rom 乱序表
        case (b)
            4'd0: pca_w = {7'h4D,1'b0};  4'd1: pca_w = {7'h4E,1'b0};
            4'd2: pca_w = {7'h4F,1'b0};  4'd3: pca_w = {7'h44,1'b0};
            4'd4: pca_w = {7'h49,1'b0};  4'd5: pca_w = {7'h4A,1'b0};
            4'd6: pca_w = {7'h4B,1'b0};  4'd7: pca_w = {7'h4C,1'b0};
            4'd8: pca_w = {7'h40,1'b0};  4'd9: pca_w = {7'h41,1'b0};
            4'd10: pca_w = {7'h42,1'b0}; 4'd11: pca_w = {7'h43,1'b0};
            4'd12: pca_w = {7'h45,1'b0}; 4'd13: pca_w = {7'h46,1'b0};
            4'd14: pca_w = {7'h47,1'b0}; default: pca_w = {7'h48,1'b0};
        endcase
    endfunction

    function [1:0] tca_of(input [3:0] b);       // 块 0~3->ch0, 4~7->ch1...
        tca_of = b[3:2];
    endfunction

    // 全序字节流
    function [7:0] byte_at(input integer idx);
        integer t, k;
        reg [7:0] pw;
        begin
            if (idx < 2) begin
                byte_at = (idx == 0) ? TCA_ADDR_W : 8'h00;   // mask=0
            end else begin
                t = (idx - 2) / BLK_BYTES;
                k = (idx - 2) % BLK_BYTES;
                pw = pca_w(t[3:0]);
                if (k == 0)       byte_at = TCA_ADDR_W;
                else if (k == 1)  byte_at = 8'h01 << tca_of(t[3:0]);  // 1<<ch
                else if (k == 2)  byte_at = pw;
                else if (k == 3)  byte_at = 8'h00;
                else if (k == 4)  byte_at = 8'h30;           // SLEEP|AI
                else if (k == 5)  byte_at = pw;
                else if (k == 6)  byte_at = 8'hFE;
                else if (k == 7)  byte_at = {1'b0, PRE_SCALE};
                else if (k == 8)  byte_at = pw;
                else if (k == 9)  byte_at = 8'h06;
                else if (k >= 10 && k <= 73)
                    byte_at = (((k - 10) % 4) == 3) ? 8'h10 : 8'h00;  // FULL_OFF
                else if (k == 74) byte_at = pw;
                else if (k == 75) byte_at = 8'h01;
                else if (k == 76) byte_at = 8'h04;           // OUTDRV
                else if (k == 77) byte_at = pw;
                else if (k == 78) byte_at = 8'h00;
                else if (k == 79) byte_at = 8'h20;           // AI(唤醒)
                else if (k == 80) byte_at = pw;
                else if (k == 81) byte_at = 8'h00;
                else byte_at = 8'hA0;                        // RESTART|AI
            end
        end
    endfunction

    // 事务表: tn=0 mask=0; tn>=1 -> 块 b, 段 s (7 段/块)
    function integer txn_start_of(input integer tn);
        integer b, s;
        begin
            if (tn == 0) txn_start_of = 0;
            else begin
                b = (tn - 1) / 7;
                s = (tn - 1) % 7;
                case (s)
                    3'd0:    txn_start_of = 2 + b*BLK_BYTES + 0;   // TCA sel
                    3'd1:    txn_start_of = 2 + b*BLK_BYTES + 2;   // SLEEP
                    3'd2:    txn_start_of = 2 + b*BLK_BYTES + 5;   // PRE_SCALE
                    3'd3:    txn_start_of = 2 + b*BLK_BYTES + 8;   // FULL_OFF
                    3'd4:    txn_start_of = 2 + b*BLK_BYTES + 74;  // MODE2
                    3'd5:    txn_start_of = 2 + b*BLK_BYTES + 77;  // wake
                    default: txn_start_of = 2 + b*BLK_BYTES + 80;  // RESTART
                endcase
            end
        end
    endfunction

    function integer txn_len_of(input integer tn);
        integer s;
        begin
            if (tn == 0) txn_len_of = 2;
            else begin
                s = (tn - 1) % 7;
                case (s)
                    3'd3:    txn_len_of = 66;
                    3'd0:    txn_len_of = 2;
                    default: txn_len_of = 3;
                endcase
            end
        end
    endfunction

    function [3:0] txn_blk_of(input integer tn);
        begin
            txn_blk_of = (tn == 0) ? 4'd0 : (((tn - 1) / 7) & 4'hF);
        end
    endfunction

    function txn_is_tca(input integer tn);      // sb_kind 选择
        begin
            if (tn == 0)               txn_is_tca = 1'b1;
            else                       txn_is_tca = (((tn - 1) % 7) == 0);
        end
    endfunction

    function txn_delay_after(input integer tn); // wake(s=5) 后插 1ms
        begin
            if (tn == 0)               txn_delay_after = 1'b0;
            else                       txn_delay_after = (((tn - 1) % 7) == 5);
        end
    endfunction

    // The integer-index functions above remain the golden sequence reference.
    // Active hardware uses a transaction descriptor, avoiding /7 and /83 chains.
    function [7:0] descriptor_byte(input g, input [3:0] b, input [2:0] s, input [6:0] bj);
        begin
            if (g) descriptor_byte = (bj == 7'd0) ? TCA_ADDR_W : 8'h00;
            else if (s == 3'd0) descriptor_byte = (bj == 7'd0) ? TCA_ADDR_W : (8'h01 << tca_of(b));
            else if (bj == 7'd0) descriptor_byte = pca_w(b);
            else if (bj == 7'd1) begin
                case (s)
                    3'd2: descriptor_byte = 8'hFE;
                    3'd3: descriptor_byte = 8'h06;
                    3'd4: descriptor_byte = 8'h01;
                    default: descriptor_byte = 8'h00;
                endcase
            end else begin
                case (s)
                    3'd1: descriptor_byte = 8'h30;
                    3'd2: descriptor_byte = PRE_SCALE;
                    3'd3: descriptor_byte = (bj[1:0] == 2'd1) ? 8'h10 : 8'h00;
                    3'd4: descriptor_byte = 8'h04;
                    3'd5: descriptor_byte = 8'h20;
                    default: descriptor_byte = 8'hA0;
                endcase
            end
        end
    endfunction

    // ------------------------------------------------------------------------
    // 状态机(builder 同型) + 输出寄存
    // ------------------------------------------------------------------------
    localparam [2:0] S_IDLE     = 3'd0,
                     S_WAITBUS  = 3'd1,
                     S_SETUP    = 3'd2,
                     S_SEND     = 3'd3,
                     S_WAITDONE = 3'd4,
                     S_DELAY    = 3'd5,
                     S_TAIL     = 3'd6;

    reg [2:0]  st;
    reg        done_q;         // init 完成粘滞(复位清)
    reg [6:0]  tn;             // 事务号 0..112
    reg        global_q;       // first mask=0 transaction
    reg [3:0]  blk_desc;       // current descriptor block, independent of owner latch
    reg [2:0]  seg_q;          // block transaction segment 0..6
    reg [6:0]  j;              // 段内字节 0..65
    reg [31:0] dly;              // Phase 1F: was [15:0] (overflowed at 96.968727 MHz)
    reg        cmd_q, val_q, stop_q;
    reg [7:0]  dat_q;
    reg        sbstart_q, sbdone_q, sback_q;
    reg [3:0]  blk_q;

    wire ini    = (st != S_IDLE);
    wire is_tca = global_q || (seg_q == 3'd0);
    wire [6:0] last_j = is_tca ? 7'd1 : ((seg_q == 3'd3) ? 7'd65 : 7'd2);

    assign init_busy   = ini;
    assign init_done   = done_q;
    assign sb_init_last = !global_q && (seg_q == 3'd6);
    assign sb_init_global = global_q;
    assign cur_blk     = blk_q;
    assign cur_pca_txn = ini && !is_tca && ((st == S_SEND) || (st == S_WAITDONE));

    always @* begin
        sb_start   = sbstart_q;
        sb_done    = sbdone_q;
        sb_ack_err = sback_q;
        sb_kind    = is_tca ? 3'd0 : 3'd1;
    end

    // 输出仲裁: init 期间 FSM 驱动 master 并压制 runtime 侧; 完成后纯直通
    always @* begin
        if (ini) begin
            m_cmd_start  = cmd_q;
            m_valid      = val_q;
            m_data       = dat_q;
            m_stop       = stop_q;
            r_ready      = 1'b0;
            r_done_gated = 1'b0;
        end else begin
            m_cmd_start  = r_cmd_start;
            m_valid      = r_valid;
            m_data       = r_data;
            m_stop       = r_stop;
            r_ready      = m_ready;
            r_done_gated = m_done;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            st        <= S_IDLE;
            done_q    <= 1'b0;
            tn        <= 7'd0;
            global_q  <= 1'b1;
            blk_desc  <= 4'd0;
            seg_q     <= 3'd0;
            j         <= 7'd0;
            dly       <= 32'd0;
            cmd_q     <= 1'b0;
            val_q     <= 1'b0;
            stop_q    <= 1'b0;
            dat_q     <= 8'h00;
            sbstart_q <= 1'b0;
            sbdone_q  <= 1'b0;
            sback_q   <= 1'b0;
            blk_q     <= 4'd0;
        end else begin
            sbstart_q <= 1'b0;
            sbdone_q  <= 1'b0;

            // A NACK/timeout may finish before the byte producer reaches WAITDONE.
            if (((st == S_SEND) || (st == S_WAITDONE)) && m_done) begin
                val_q <= 1'b0;
                cmd_q <= 1'b0;
                sbdone_q <= 1'b1;
                sback_q <= m_ack_err;
                // A failed TCA selection must not reach PCA; a failed FULL_OFF
                // must not reach wake/RESTART. Recovery owns subsequent traffic.
                // Phase 2F-DEBUG: with MIN_ONLINE_BLOCKS < 16, an absent block must not
                // abort the whole bring-up. Record the error (sb_ack_err keeps doing that)
                // and continue with the next block. With all 16 present this path is never
                // taken, so the production sequence is unchanged.
                if (m_ack_err && STOP_ON_ERROR) st <= S_TAIL;
                else if (m_ack_err) begin
                    if (global_q) begin
                        tn       <= 7'd1;
                        global_q <= 1'b0;
                        blk_desc <= 4'd0;
                        seg_q    <= 3'd0;
                        st       <= S_WAITBUS;
                    end else if (blk_desc >= 4'd15) begin
                        st <= S_TAIL;
                    end else begin
                        tn       <= 7'd1 + ((blk_desc + 4'd1) * 7'd7);
                        blk_desc <= blk_desc + 4'd1;
                        seg_q    <= 3'd0;
                        st       <= S_WAITBUS;
                    end
                end
                else if (tn == TXN_LAST) st <= S_TAIL;
                else if (!global_q && (seg_q == 3'd5)) begin
                    dly <= DLY_1MS; st <= S_DELAY;
                end else begin
                    tn <= tn + 7'd1; st <= S_WAITBUS;
                    if (global_q) begin
                        global_q <= 1'b0;
                        blk_desc <= 4'd0;
                        seg_q <= 3'd0;
                    end else if (seg_q == 3'd6) begin
                        blk_desc <= blk_desc + 4'd1;
                        seg_q <= 3'd0;
                    end else begin
                        seg_q <= seg_q + 3'd1;
                    end
                end
            end else case (st)
                S_IDLE: begin
                    if ((restart === 1'b1) || (!done_q && start)) begin
                        done_q <= 1'b0;
                        tn <= 7'd0;
                        global_q <= 1'b1;
                        blk_desc <= 4'd0;
                        seg_q <= 3'd0;
                        st <= S_WAITBUS;
                    end
                end

                // 等主机空闲再发 START(builder W_WAITBUS 同型)
                S_WAITBUS: begin
                    // Unselected physical branches are disabled, not online.
                    // Skip before START, so D1 never probes CH0/CH2.
                    if (!global_q && !TARGET_MASK[blk_desc]) begin
                        if(blk_desc==15)st<=S_TAIL;
                        else begin
                            blk_desc<=blk_desc+1;seg_q<=0;
                            tn<=7'd1+((blk_desc+4'd1)*7'd7);
                        end
                    end else if (!m_busy) begin
                        cmd_q <= 1'b1;
                        blk_q <= blk_desc;
                        st    <= S_SETUP;
                    end
                end

                // cmd_start 保持 1 拍(上拍已寄存), 本拍装载首字节
                S_SETUP: begin
                    cmd_q     <= 1'b0;
                    val_q     <= 1'b1;
                    dat_q     <= descriptor_byte(global_q, blk_desc, seg_q, 7'd0);
                    stop_q    <= 1'b0;                   // 最短段 2B
                    j         <= 7'd0;
                    sbstart_q <= 1'b1;
                    st        <= S_SEND;
                end

                // 字节流握手(builder W_SEND 同型)
                S_SEND: begin
                    if (val_q && m_ready) begin
                        if (j == last_j) begin
                            val_q <= 1'b0;
                            st    <= S_WAITDONE;
                        end else begin
                            j      <= j + 7'd1;
                            dat_q  <= descriptor_byte(global_q, blk_desc, seg_q, j + 7'd1);
                            stop_q <= (j + 7'd1 == last_j);
                        end
                    end
                end

                // 等主机 STOP 完成(builder W_WAITDONE 同型)
                S_WAITDONE: begin
                    // terminal handler above owns all master results
                end

                S_DELAY: begin
                    if (dly == 32'd0) begin
                        tn <= tn + 7'd1;
                        seg_q <= seg_q + 3'd1;
                        st <= S_WAITBUS;
                    end else begin
                        dly <= dly - 32'd1;
                    end
                end

                S_TAIL: begin
                    done_q <= 1'b1; // final result remains INIT-owned through this cycle
                    st <= S_IDLE;
                end

                default: st <= S_IDLE;
            endcase
        end
    end

endmodule
