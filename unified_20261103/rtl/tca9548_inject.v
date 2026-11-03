// ============================================================================
// tca9548_inject.v -- TCA9548 选通事务注入器 (v1.4, 新增模块, 非冻结)
//
// 背景（真实硬件证据链, 见 docs/V1_4_REAL_HAPTIC_REPORT.md Phase 0）:
//   STM32 原系统每块刷新 = HapticVest256_SelectTca([0xE0, 1<<ch] 裸写 1 字节)
//   -> PCA9685_WriteDuty16([addr,0x00,0x20] + [addr,0x06,64B])。
//   FPGA 管线 v1.0~v1.3 的 burst builder 只有后两笔事务 —— TCA9548 上电
//   默认全通道关闭, 无选通事务时背心 16 片 PCA 全部无应答。
//
// 方案: 夹在 pca9685_burst_builder 与 i2c_master 字节流之间的透明注入器。
//   en=0: 纯直通（= v1.3 行为, 旧 TB 字节布局零影响）
//   en=1: 在每块首笔事务（MODE1 重申）前注入 [0xE0, 1<<tca] + STOP;
//         tca 由该笔事务首字节的 PCA 地址反查（表=matrix_map_rom 同源映射,
//         tb_tca_inject 以独立黄金表交叉核对 16/16）。
//
// 选通时机: armed 在每笔 LED0(寄存器 0x06)事务完成后置位 —— 每个块边界
//   恰好注入一次（与 STM32 每块先选通一致; STM32 另有同通道缓存跳过,
//   本模块恒重发, 每块多 ~0.1ms, 换取无状态）。
//
// 与冻结模块关系: 零改动。builder/i2c_master 的握手语义全部保持:
//   - 注入期间向 builder 回 b_ready=0（builder 字节流天然可停等）
//   - 注入事务的 master done 被吞掉（builder 的 done 只对应其自身事务）
//   - builder 的 cmd_start 被截获缓存, 在重放首字节时原样重发
//   - valid/ready 逐字节握手: 每状态恰好提交一个字节
// ============================================================================
`timescale 1ns / 1ps

module tca9548_inject (
    input  wire       clk,
    input  wire       rst_n,

    input  wire       en,              // 1 = 注入选通事务; 0 = 纯直通
    input  wire [3:0] sched_blk_i,
    input  wire       fs_i,
    output reg        sb_start_o,
    output reg  [3:0] sb_blk_o,
    output reg  [2:0] sb_kind_o,
    output reg        sb_done_o,
    output reg        sb_ack_err_o,
    // ---- builder 侧 ----
    input  wire       b_cmd_start,     // builder 事务起始脉冲（截获缓存）
    input  wire       b_valid,
    input  wire [7:0] b_data,
    input  wire       b_stop,
    output reg        b_ready,         // -> builder
    output wire       b_busy,          // -> builder（master busy | 注入中）
    output reg        b_done,          // -> builder（注入事务的 done 被吞）
    output wire       b_ack_err,
    // ---- i2c_master 侧 ----
    output reg        m_cmd_start,
    output reg        m_valid,
    output reg  [7:0] m_data,
    output reg        m_stop,
    input  wire       m_ready,
    input  wire       m_busy,
    input  wire       m_done,
    input  wire       m_ack_err,
    input  wire       m_start_accepted
);

    // ------------------------------------------------------------------------
    // PCA 7bit 地址 -> TCA 通道（= matrix_map_rom tca 表反查, 0x40..0x4F）
    //   ch0: 4D 4E 4F 44 | ch1: 49 4A 4B 4C | ch2: 40 41 42 43 | ch3: 45 46 47 48
    // ------------------------------------------------------------------------
    function [1:0] tca_of(input [6:0] pca);
        case (pca)
            7'h44, 7'h4D, 7'h4E, 7'h4F: tca_of = 2'd0;
            7'h49, 7'h4A, 7'h4B, 7'h4C: tca_of = 2'd1;
            7'h40, 7'h41, 7'h42, 7'h43: tca_of = 2'd2;
            default:                    tca_of = 2'd3;   // 45 46 47 48
        endcase
    endfunction

    // ------------------------------------------------------------------------
    // 状态: PASS 直通 / CAP 截字节(0=addr,1=reg) / TCA_A(TCA 事务首字节)
    //       / TCA_B(通道掩码+STOP) / TCA_WAIT(吞 done) / REP0 / REP1(重放)
    // ------------------------------------------------------------------------
    localparam S_PASS     = 3'd0,
               S_CAP      = 3'd1,
               S_TCA_A    = 3'd2,
               S_TCA_B    = 3'd3,
               S_TCA_WAIT = 3'd4,
               S_REP0     = 3'd5,
               S_REP1     = 3'd6;

    reg [2:0]  st;
    reg        armed;        // 1 = 下一笔事务是块首(MODE1), 注入选通
    reg        led_txn;      // 当前直通事务含 0x06 寄存器（LED0 突发）
    reg [1:0]  fwd_idx;
    reg [2:0]  fwd_kind;      // 直通态事务内字节序号
    reg        cap_cnt;      // 截获计数: 0=addr, 1=reg
    reg [7:0]  cap_addr;
    reg [7:0]  cap_reg;
    reg [1:0]  tca_sel;
    reg txn_active;
    reg [2:0] next_kind;
    // Only the arbitrator can certify that this runtime START reached master.
    wire accepted = m_start_accepted;
    assign b_ack_err = (m_ack_err === 1'b1);

    // 布防态的 cmd_start 周期转入截获（该周期不得直通, 防首字节竞速漏到主机）
    wire cap_start = (st == S_PASS) && en && armed && b_cmd_start;
    wire passthrough = (st == S_PASS) && !cap_start;
    function [3:0] blk_of(input [7:0] aw);
        case (aw[7:1])
            8'h4D, 8'h4E, 8'h4F, 8'h44: blk_of = 4'd0;
            8'h49, 8'h4A, 8'h4B, 8'h4C: blk_of = 4'd1;
            8'h40, 8'h41, 8'h42, 8'h43: blk_of = 4'd2;
            default:                    blk_of = 4'd3;
        endcase
    endfunction

    localparam [2:0] K_TCA_SELECT = 3'd0, K_PCA_MODE = 3'd2,
                     K_PCA_BURST  = 3'd3, K_ALL_OFF   = 3'd4;

    // ------------------------------------------------------------------------
    // 输出通路
    // ------------------------------------------------------------------------
    always @* begin
        if (passthrough) begin
            m_cmd_start = b_cmd_start;
            m_valid     = b_valid;
            m_data      = b_data;
            m_stop      = b_stop;
            b_ready     = m_ready;
            b_done      = m_done;
        end else begin
            m_cmd_start = 1'b0;
            m_valid     = 1'b0;
            m_data      = 8'h00;
            m_stop      = 1'b0;
            b_ready     = 1'b0;
            b_done      = 1'b0;
            case (st)
                S_CAP:     b_ready = 1'b1;              // 截获, 不转发
                S_TCA_A: begin                          // TCA 事务首字节
                    m_cmd_start = 1'b1;                 // 1 clk 脉冲(握手即迁移)
                    m_valid     = 1'b1;
                    m_data      = 8'hE0;                // TCA9548 HAL 地址 0x70<<1
                end
                S_TCA_B: begin                          // 通道掩码 + STOP
                    m_valid = 1'b1;
                    m_data  = 8'h01 << tca_sel;         // 1<<ch (TCA9548 控制字节)
                    m_stop  = 1'b1;
                end
                S_REP0: begin                           // 重放截获的 cmd_start+addr
                    m_cmd_start = 1'b1;
                    m_valid     = 1'b1;
                    m_data      = cap_addr;
                end
                S_REP1: begin                           // 重放寄存器字节
                    m_valid = 1'b1;
                    m_data  = cap_reg;
                end
                default: ;                              // TCA_WAIT: 全 0, 吞 done
            endcase
        end
        // Injected TCA failure terminates the blocked builder as well. Never
        // replay PCA bytes after failed channel selection or an early timeout.
        if (m_done && (m_ack_err === 1'b1)) b_done = 1'b1;
    end

    assign b_busy = m_busy || !passthrough;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            st       <= S_PASS;
            armed    <= 1'b1;          // 复位后首笔事务即块首
            led_txn  <= 1'b0;
            fwd_idx  <= 2'd0;
            fwd_kind <= 3'd2;
            cap_cnt  <= 1'b0;
            cap_addr <= 8'h00;
            cap_reg  <= 8'h00;
            tca_sel  <= 2'd0;
            sb_start_o <= 0; sb_done_o <= 0; sb_ack_err_o <= 0;
            sb_blk_o <= 0; sb_kind_o <= 0; txn_active <= 0; next_kind <= K_PCA_MODE;
        end else begin
            sb_start_o <= 0;
            sb_done_o <= 0;
            if (accepted) begin
                txn_active <= 1;
                sb_start_o <= 1;
                sb_blk_o <= sched_blk_i;
                sb_kind_o <= ((st==S_TCA_A) || (st==S_TCA_B) || (st==S_TCA_WAIT)) ?
                             K_TCA_SELECT : ((next_kind==K_PCA_BURST && fs_i) ? K_ALL_OFF : next_kind);
            end
            if (m_done && txn_active) begin
                txn_active <= 0;
                sb_done_o <= 1;
                sb_ack_err_o <= (m_ack_err === 1'b1);
                if (m_ack_err === 1'b1) next_kind <= K_PCA_MODE;
                else if (sb_kind_o==K_PCA_MODE) next_kind <= K_PCA_BURST;
                else if ((sb_kind_o==K_PCA_BURST) || (sb_kind_o==K_ALL_OFF))
                    next_kind <= K_PCA_MODE;
            end
            if (m_done && (m_ack_err === 1'b1)) begin
                st <= S_PASS; armed <= 1; led_txn <= 0; fwd_idx <= 0;
            end else case (st)
                // ---------------- 直通 ----------------
                S_PASS: begin
                    if (cap_start) begin
                        armed   <= 1'b0;               // 消费布防
                        cap_cnt <= 1'b0;
                        st      <= S_CAP;
                    end else begin
                        if (b_cmd_start)
                            fwd_idx <= 2'd0;
                        else if (b_valid && m_ready) begin
                            // LED0 突发判别: 事务内第 2 字节 = 0x06
                            if ((fwd_idx == 2'd1) && (b_data == 8'h06)) begin
                                led_txn <= 1'b1;
                            end
                            // 块边界布防: LED0 突发 stop 握手拍即置 armed ——
                            // 早于 builder 背靠背下一笔事务的 cmd_start
                            // （依赖 m_done 会因 done 迟到而漏注入, TB T2 实证）
                            if (b_stop) begin
                                armed   <= led_txn;
                                led_txn <= 1'b0;
                            end
                            fwd_idx <= fwd_idx + 2'd1;
                        end
                    end
                end

                // ---------------- 截获块首事务前两字节 ----------------
                S_CAP: begin
                    if (b_valid) begin
                        if (!cap_cnt) begin
                            cap_addr <= b_data;
                            tca_sel  <= tca_of(b_data[7:1]);
                            cap_cnt  <= 1'b1;
                        end else begin
                            cap_reg  <= b_data;
                            // 防御: 截获的非 MODE1 事务 -> 只重放不注入
                            st       <= (b_data != 8'h00) ? S_REP0 : S_TCA_A;
                        end
                    end
                end

                // ---------------- TCA 选通事务 ----------------
                S_TCA_A: begin
                    if (m_ready)                       // [0xE0] 已被主机接受
                        st <= S_TCA_B;
                end

                S_TCA_B: begin
                    if (m_ready)                       // [1<<ch]+STOP 已接受
                        st <= S_TCA_WAIT;
                end

                S_TCA_WAIT: begin
                    if (m_done) begin                  // 吞掉本注入事务的 done
                        st <= S_REP0;                  // 重放被截的 [addr, reg]
                    end
                end

                // ---------------- 重放被截字节 ----------------
                S_REP0: begin
                    if (m_ready)
                        st <= S_REP1;
                end

                S_REP1: begin
                    if (m_ready) begin
                        fwd_idx <= 2'd2;               // 事务内已转发 2 字节
                        st      <= S_PASS;
                    end
                end

                default: st <= S_PASS;
            endcase
        end
    end

endmodule
