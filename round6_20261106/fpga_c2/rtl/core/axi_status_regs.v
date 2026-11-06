// ============================================================================
// axi_status_regs.v -- 系统状态/计数 AXI4-Lite 从机（v1.1 新增, 非冻结模块）
//
// 寄存器（字访问, 10bit 地址）:
//   0x000 FRAME_COUNT RO : 完成帧扫描计数 [15:0]
//   0x004 BURST_COUNT RO : 块突发触发计数 [15:0]
//   0x008 FLAGS      RO  : bit0 i2c_ack_err(粘滞) bit1 sched_busy bit2 buf_busy
//                          bit3 bld_busy bit4 i2c_busy bit5 block_ready
//   0x00C SW_CTRL    W   : bit0 写 1 -> sw_commit 单拍脉冲（软件强制块交接, 调试）
// ---- v1.2 扩展（只增不改, 0x000~0x00C 语义/地址不变）----
//   0x010 DIRTY_BITMAP RO: 最近一次 DD 扫描位图 [15:0]（1=块脏需刷新）
//   0x014 DIRTY_COUNT  RO: 最近一次 DD 脏块计数 [4:0]
//   0x018 FAILSAFE     RO: bit0 fs_active(实时) bit1 recovery_seen(粘滞)
//                          bit2 fs_refresh_seen(粘滞, 超时强断帧发生过)
//   0x01C DD_STATUS    RO: bit0 dd_busy bit1 dd_active bit2 bypass
//   0x020 ERR          RO: {commit_err[7:0], drop_err[7:0], seq_err[7:0]}
//   0x024 SW_FS_CTRL   W/RW: bit0 -> 软件强制 force_off 粘滞位（板级 B4 确定性
//                          触发边界 A; 写 0 清除。与硬件 failsafe 输出相或）
//   0x028 INIT RO: healthy only (attempt_done && ONLINE=FFFF && INIT_ERROR=0)
//   0x050 TIMEOUT_COUNT RO: actual master timeout terminal count
//   0x054 RECOVERY RO: state[2:0], run[3], OFF[4], restart[5], no HW cutoff[6]
//
// 写/读引擎结构与 matrix_buffer_axi 同型（已验证）; 读地址一律用锁存值
// （v1.0.1 教训: ARADDR 握手后不保持）。
// ============================================================================

`timescale 1ns / 1ps

module axi_status_regs (
    input  wire        clk,
    input  wire        rst_n,
    // ---- AXI4-Lite ----
    input  wire        s_axi_awvalid,
    input  wire [9:0]  s_axi_awaddr,
    output wire        s_axi_awready,
    input  wire        s_axi_wvalid,
    input  wire [31:0] s_axi_wdata,
    output wire        s_axi_wready,
    output wire        s_axi_bvalid,
    output wire [1:0]  s_axi_bresp,
    input  wire        s_axi_bready,
    input  wire        s_axi_arvalid,
    input  wire [9:0]  s_axi_araddr,
    output wire        s_axi_arready,
    output wire        s_axi_rvalid,
    output wire [31:0] s_axi_rdata,
    output wire [1:0]  s_axi_rresp,
    input  wire        s_axi_rready,
    // ---- 系统状态输入 ----
    input  wire        frame_done_i,
    input  wire        burst_trig_i,
    input  wire        sched_busy_i,
    input  wire        buf_busy_i,
    input  wire        bld_busy_i,
    input  wire        i2c_busy_i,
    input  wire        block_ready_i,
    input  wire        i2c_ack_err_i,
    // ---- v1.2 状态输入 ----
    input  wire [15:0] dirty_bitmap_i,
    input  wire [4:0]  dirty_count_i,
    input  wire        dd_busy_i,
    input  wire        dd_active_i,
    input  wire        dd_bypass_i,
    input  wire        fs_active_i,
    input  wire        recovery_seen_i,
    input  wire        fs_refresh_seen_i,
    input  wire        first_frame_seen_i,
    input  wire [7:0]  seq_err_i,
    input  wire [7:0]  drop_err_i,
    input  wire [7:0]  commit_err_i,
    // ---- 软件控制输出 ----
    output reg         sw_commit_o,
    output reg         sw_force_off_o,
    output reg         sw_tca_en_o,      // v1.4: SW_CTRL bit1 -> TCA9548 选通注入
    output reg         fault_clear_o,    // v1.6: SW_CTRL bit2 -> 清 fault_latched
    // ---- v1.6 fault/init 状态输入 ----
    input  wire        init_done_i,
    input  wire [15:0] online_mask_i,
    input  wire [15:0] init_error_mask_i,
    input  wire [15:0] runtime_error_mask_i,
    input  wire [15:0] off_pending_mask_i,
    input  wire        fault_latched_i,
    input  wire [31:0] i2c_error_cnt_i,
    input  wire [31:0] off_retry_cnt_i,
    input  wire [15:0] active_gen_i,
    input  wire [15:0] pending_gen_i,
    input  wire [31:0] i2c_timeout_cnt_i,
    input  wire [31:0] i2c_abort_cnt_i,
    input  wire [31:0] i2c_nack_cnt_i,
    input  wire [31:0] i2c_terminal_flags_i,
    input  wire [31:0] recovery_status_i
);

    // ------------------------------------------------------------------------
    // 计数器
    // ------------------------------------------------------------------------
    reg [15:0] frame_cnt, burst_cnt;

    // ------------------------------------------------------------------------
    // 写通道: AW/W 独立锁存 -> 双锁存齐后接受（单拍 sw_commit）-> B
    // ------------------------------------------------------------------------
    localparam W_IDLE = 2'd0, W_EXEC = 2'd1, W_RESP = 2'd2;
    reg [1:0]  wstate;
    reg        aw_latched, w_latched;
    reg [9:0]  awaddr_r;                           // v1.2: 写地址译码（0x00C/0x024）
    reg [31:0] wdata_r;
    reg        bvalid_r;

    assign s_axi_awready = rst_n & (wstate == W_IDLE) & ~aw_latched;
    assign s_axi_wready  = rst_n & (wstate == W_IDLE) & ~w_latched;
    assign s_axi_bvalid  = bvalid_r;
    assign s_axi_bresp   = 2'b00;                       // OKAY

    // 双锁存齐 = 写接受点（后到通道的握手沿即接受沿）
    wire wr_accept = (wstate == W_IDLE) && aw_latched && w_latched;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            aw_latched <= 1'b0;
            w_latched  <= 1'b0;
            awaddr_r   <= 10'h0;
            wdata_r    <= 32'h0;
            wstate     <= W_IDLE;
            bvalid_r   <= 1'b0;
            sw_commit_o<= 1'b0;
            sw_tca_en_o<= 1'b0;
            sw_force_off_o <= 1'b0;
            fault_clear_o  <= 1'b0;
        end else begin
            if (s_axi_awvalid && s_axi_awready) begin
                aw_latched <= 1'b1;
                awaddr_r   <= s_axi_awaddr;
            end
            if (s_axi_wvalid && s_axi_wready) begin
                w_latched <= 1'b1;
                wdata_r <= s_axi_wdata;
            end

            case (wstate)
                W_IDLE: begin
                    if (wr_accept) begin
                        case (awaddr_r[5:2])
                            4'd3: begin
                                sw_commit_o    <= wdata_r[0]; // SW_CTRL: bit0 脉冲
                                sw_tca_en_o    <= wdata_r[1]; // v1.4: bit1 电平
                                fault_clear_o  <= wdata_r[2]; // v1.6: bit2 脉冲
                            end
                            4'd9: sw_force_off_o <= wdata_r[0]; // SW_FS_CTRL
                            default: ;                               // RO 地址忽略
                        endcase
                        wstate <= W_EXEC;
                    end
                end
                W_EXEC: begin
                    fault_clear_o <= 1'b0;
                    sw_commit_o <= 1'b0;                // 单拍脉冲
                    bvalid_r    <= 1'b1;
                    wstate      <= W_RESP;
                end
                W_RESP: begin
                    if (s_axi_bready) begin
                        bvalid_r   <= 1'b0;
                        aw_latched <= 1'b0;
                        w_latched  <= 1'b0;
                        wstate     <= W_IDLE;
                    end
                end
                default: wstate <= W_IDLE;
            endcase
        end
    end

    // ------------------------------------------------------------------------
    // 读通道: AR 锁存 -> 单拍寄存器 mux -> R（地址一律锁存, v1.0.1 教训）
    // ------------------------------------------------------------------------
    localparam R_IDLE = 2'd0, R_EXEC = 2'd1, R_VALID = 2'd2;
    reg [1:0]  rstate;
    reg [9:0]  araddr_r;
    reg [31:0] rdata_r;
    reg        rvalid_r;

    assign s_axi_arready = rst_n & (rstate == R_IDLE);
    assign s_axi_rvalid  = rvalid_r;
    assign s_axi_rresp   = 2'b00;
    assign s_axi_rdata   = rdata_r;

    wire [15:0] flags_val = {10'h000, block_ready_i, i2c_busy_i,
                             bld_busy_i, buf_busy_i, sched_busy_i,
                             i2c_ack_err_i};   // bit0=ack, bit1=sched, bit2=buf,
                                               // bit3=bld, bit4=i2c, bit5=ready

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            araddr_r <= 10'h0;
            rstate   <= R_IDLE;
            rdata_r  <= 32'h0;
            rvalid_r <= 1'b0;
        end else begin
            case (rstate)
                R_IDLE: begin
                    if (s_axi_arvalid && s_axi_arready) begin
                        araddr_r <= s_axi_araddr;       // 锁存（v1.0.1 教训）
                        rstate   <= R_EXEC;
                    end
                end
                R_EXEC: begin
                    case (araddr_r[6:2])
                        4'd0: rdata_r <= {16'h0000, frame_cnt};
                        4'd1: rdata_r <= {16'h0000, burst_cnt};
                        4'd2: rdata_r <= {16'h0000, flags_val};
                        4'd3: rdata_r <= {31'h0, sw_tca_en_o};   // SW_CTRL 回显(bit1)
                        // ---- v1.2 ----
                        4'd4: rdata_r <= {16'h0000, dirty_bitmap_i};
                        4'd5: rdata_r <= {27'h0, dirty_count_i};
                        4'd6: rdata_r <= {28'h0, first_frame_seen_i,
                                          fs_refresh_seen_i,
                                          recovery_seen_i, fs_active_i};
                        4'd7: rdata_r <= {29'h0, dd_bypass_i,
                                          dd_active_i, dd_busy_i};
                        4'd8: rdata_r <= {commit_err_i, drop_err_i, seq_err_i};
4'd9: rdata_r <= {31'h0, sw_force_off_o};
                        5'd10: rdata_r <= {31'h0, init_done_i};
                        5'd11: rdata_r <= {16'h0000, online_mask_i};
                        5'd12: rdata_r <= {16'h0000, init_error_mask_i};
                        5'd13: rdata_r <= {16'h0000, runtime_error_mask_i};
                        5'd14: rdata_r <= {16'h0000, off_pending_mask_i};
                        5'd15: rdata_r <= {31'h0, fault_latched_i};
                        5'd16: rdata_r <= i2c_error_cnt_i;
                        5'd17: rdata_r <= off_retry_cnt_i;
                        5'd18: rdata_r <= {16'h0000, active_gen_i};
                        5'd19: rdata_r <= {16'h0000, pending_gen_i};
                        5'd20: rdata_r <= i2c_timeout_cnt_i; // 0x050
                        5'd21: rdata_r <= recovery_status_i; // 0x054, bit6: no HW cutoff
                        5'd22: rdata_r <= i2c_abort_cnt_i; // 0x058 controlled abort terminals, resident
                        5'd23: rdata_r <= i2c_nack_cnt_i; // 0x05C actual observed NACK terminals
                        5'd24: rdata_r <= i2c_terminal_flags_i; // 0x060 [0]NACK [1]timeout [2]abort [3]aggregate error
                        default: rdata_r <= 32'h0;
                    endcase
                    rvalid_r <= 1'b1;
                    rstate   <= R_VALID;
                end
                R_VALID: begin
                    if (s_axi_rready) begin
                        rvalid_r <= 1'b0;
                        rstate   <= R_IDLE;
                    end
                end
                default: rstate <= R_IDLE;
            endcase
        end
    end

    // ------------------------------------------------------------------------
    // 计数器
    // ------------------------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            frame_cnt <= 16'd0;
            burst_cnt <= 16'd0;
        end else begin
            if (frame_done_i) frame_cnt <= frame_cnt + 16'd1;
            if (burst_trig_i) burst_cnt <= burst_cnt + 16'd1;
        end
    end

endmodule
