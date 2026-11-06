// ============================================================================
// matrix_buffer_axi.v -- Matrix Buffer 的 AXI4-Lite 从机封装
//
// 工程   : AX7020 fpga/16x16  第二阶段（PS -> AXI -> FPGA Buffer 链路）
// 契约   : docs/MATRIX_BUFFER_INTERFACE_SPEC.md §3.1（寄存器映射）
// 核心   : 内部实例化 matrix_buffer（双 bank 帧缓冲，本阶段唯一 PL 功能件）
//
// 寄存器映射（字访问, 32bit, 小端字节通道; cell = word*4 + lane）:
//   0x000 CTRL   W/R : bit0 ENABLE(RW)  bit1 COMMIT(写1触发帧切换, 读恒0)
//   0x004 STATUS RO  : bit0 ACT_BANK    bit1 COMMIT_ACK(置位保持, 读清)
//                       bit2 ENABLE回显
//   0x100..0x1FF LEVEL_PEND[0..63] R/W : 写入 bank 回读（PS 写后校验）
//   0x200..0x2FF LEVEL_ACT [0..63] RO  : 显示 bank 读取（后续与 FSM 共享 rd 端口,
//                                        FSM 尚未实现, 本阶段 AXI 独占）
//   其余地址: 读 0, 写忽略, 均返回 OKAY
//
// 读时序: 寄存器 2 拍; 缓冲区 5 拍/字（4 字节流水驱动/采样拼装, 含 1 clk 读延迟）
// 写时序: PEND 窗口 4 拍/字（按 wstrb 逐字节写入）; 寄存器 1 拍
// ============================================================================

`timescale 1ns / 1ps

module matrix_buffer_axi (
    // ---- 全局 ----
    input  wire        s_axi_aclk,
    input  wire        s_axi_aresetn,     // 低有效复位（Zynq GP AXI 常规）

    // ---- 写地址通道 ----
    input  wire [9:0]  s_axi_awaddr,      // 字节地址, 0x000..0x3FF
    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,
    // ---- 写数据通道 ----
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,
    // ---- 写响应通道 ----
    output wire        s_axi_bvalid,
    output wire [1:0]  s_axi_bresp,       // 恒 OKAY
    input  wire        s_axi_bready,
    // ---- 读地址通道 ----
    input  wire [9:0]  s_axi_araddr,
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,
    // ---- 读数据通道 ----
    output wire        s_axi_rvalid,
    output wire [31:0] s_axi_rdata,
    output wire [1:0]  s_axi_rresp,       // 恒 OKAY
    input  wire        s_axi_rready,

    // ---- 调试/观测输出（后续 ILA）----
    output wire        act_bank_o,
    output wire        commit_ack_o,
    output wire        enable_o,
    // ---- v1.1 接口扩展: 扫描链读端口复用（Scheduler 优先, AXI ACT 窗口退让）----
    input  wire [7:0]  scan_rd_addr,
    input  wire        scan_rd_sel,   // 1 = 读端口归 Scheduler
    output wire [7:0]  scan_rd_data,
    // ---- v1.6 扩展: 整帧原子 commit 门控（向后兼容: commit_gate_en=0 立即提交）----
    input  wire        commit_gate_en,   // 1 = 启用门控; 0 = 直通(旧 TB 兼容)
    input  wire        sched_busy_i,     // Scheduler 非空闲
    input  wire        dd_busy_i,        // Dirty Detector 非空闲
    input  wire        init_busy_i,      // INIT sequencer 运行中
    input  wire        fault_latched_i,  // 故障闭锁: 屏蔽新 commit
    output wire        commit_req_o,
    output wire [15:0] active_gen_o,
    output wire [15:0] pending_gen_o,
    output wire [2:0]  commit_status_o, // bit0=pending bit1=write_locked bit2=drop_seen
    input  wire        commit_grant_i,   // 外部帧边界授予(帧 done 拍)
    input  wire        discard_pending_i, // fault recovery restart only; bank data retained
    // ---- v1.1.2 扩展: Dirty Detector 的 pend bank 读口复用（向后兼容追加）----
    input  wire [7:0]  dd_pend_addr,
    input  wire        dd_pend_sel,   // 1 = pend 读口归 Dirty Detector
    output wire [7:0]  dd_pend_data,
    input wire [7:0] audit_rd_addr_i,
    output wire [7:0] audit_rd_data_o
);

    // ------------------------------------------------------------------------
    // 地址译码
    // ------------------------------------------------------------------------
    reg [9:0] araddr_r; // declared before decode for Vivado/XSim strict compilation
    wire cmt_write, cmt_block;
    wire w_exec, w_is_pend, w_is_reg, w_is_ctrl0;
    reg cmt_pend_q;
    wire       aw_is_pend = (s_axi_awaddr[9:8] == 2'b01);              // 0x100..0x1FF
    wire       ar_is_reg  = (araddr_r[9:4] == 4'h0);                 // 0x000..0x007 寄存器区(v1.0.1: 用锁存地址, ARADDR 握手后不保持)
    wire       ar_is_pend = (s_axi_araddr[9:8] == 2'b01);
    wire       ar_is_act  = (s_axi_araddr[9:8] == 2'b10);              // 0x200..0x2FF

    // ------------------------------------------------------------------------
    // 内部 Matrix Buffer 实例（端口契约与 rtl/matrix_buffer.v 完全一致）
    // ------------------------------------------------------------------------
    wire       mb_wr_en;
    wire [7:0] mb_wr_addr;
    wire [7:0] mb_wr_data;
    wire       mb_commit;
    wire [7:0] mb_rd_addr;
    wire [7:0] mb_rd_data;
    wire [7:0] mb_pend_rd_addr;
    wire [7:0] mb_pend_rd_data;
    wire       mb_act_bank;
    wire       mb_commit_ack;

    reg        enable_r;

    matrix_buffer u_matrix_buffer (
        .clk          (s_axi_aclk),
        .rst_n        (s_axi_aresetn),
        .wr_en        (mb_wr_en),
        .wr_addr      (mb_wr_addr),
        .wr_data      (mb_wr_data),
        .commit       (mb_commit),
        .rd_addr      (mb_rd_addr),
        .rd_data      (mb_rd_data),
        .pend_rd_addr (mb_pend_rd_addr),
        .pend_rd_data (mb_pend_rd_data),
        .act_bank     (mb_act_bank),
        .commit_ack   (mb_commit_ack),
        .audit_rd_addr(audit_rd_addr_i),.audit_rd_data(audit_rd_data_o)
    );

    assign act_bank_o   = mb_act_bank;
    assign commit_ack_o = mb_commit_ack |
                          ((commit_gate_en === 1'b1) & cmt_write & !cmt_block);   // 挂起期间不假应答   // 挂起也应答(最新胜出)
    assign enable_o     = enable_r;
    assign scan_rd_data = mb_rd_data;

    // ------------------------------------------------------------------------
    // 写通道: AW/W 独立锁存 -> EXEC(1~4 拍) -> B
    // ------------------------------------------------------------------------
    localparam W_IDLE = 2'd0, W_EXEC = 2'd1, W_RESP = 2'd2;
    reg [1:0]  wstate;
    reg        aw_latched, w_latched;
    reg [9:0]  awaddr_r;
    reg [31:0] wdata_r;
    reg [3:0]  wstrb_r;
    reg [1:0]  wcnt;
    reg        bvalid_r;

    assign s_axi_awready = s_axi_aresetn & (wstate == W_IDLE) & ~aw_latched;
    assign s_axi_wready  = s_axi_aresetn & (wstate == W_IDLE) & ~w_latched;
    assign s_axi_bvalid  = bvalid_r;
    assign s_axi_bresp   = 2'b00;                          // OKAY

    // 写通道时序逻辑（单块: 通道锁存 + 状态机, 避免 MDRV 多驱动）
    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            aw_latched <= 1'b0;
            awaddr_r   <= 10'h0;
            w_latched  <= 1'b0;
            wdata_r    <= 32'h0;
            wstrb_r    <= 4'h0;
            wstate     <= W_IDLE;
            wcnt       <= 2'd0;
            bvalid_r   <= 1'b0;
            enable_r   <= 1'b0;
        end else begin
            // 通道锁存: ready 仅在 W_IDLE 且未锁存时为高,
            // W_RESP 清零同拍不可能发生新的锁存 (ready 已被 wstate 压低)
            if (s_axi_awvalid && s_axi_awready) begin
                awaddr_r   <= s_axi_awaddr;
                aw_latched <= 1'b1;
            end
            if (s_axi_wvalid && s_axi_wready) begin
                wdata_r   <= s_axi_wdata;
                wstrb_r   <= s_axi_wstrb;
                w_latched <= 1'b1;
            end

            case (wstate)
                W_IDLE: begin
                    if (aw_latched && w_latched) begin
                        wstate <= W_EXEC;
                        wcnt   <= 2'd0;
                    end
                end
                W_EXEC: begin
                    if (w_is_ctrl0)
                        enable_r <= wdata_r[0];            // CTRL.ENABLE 锁存(仅 0x000)
                    if (w_is_pend && (wcnt != 2'd3)) begin
                        wcnt <= wcnt + 2'd1;               // PEND: 继续下一字节
                    end else begin
                        wstate <= W_RESP;                  // 寄存器/无效/末字节
                    end
                end
                W_RESP: begin
                    if (!bvalid_r) begin
                        bvalid_r <= 1'b1;
                    end else if (s_axi_bready) begin
                        bvalid_r   <= 1'b0;
                        aw_latched <= 1'b0;                // 释放锁存, 回空闲
                        w_latched  <= 1'b0;
                        wstate     <= W_IDLE;
                    end
                end
                default: wstate <= W_IDLE;
            endcase
        end
    end

    // 写执行组合输出: PEND 窗口按 wstrb 逐字节透传给 matrix_buffer
    assign      w_exec      = (wstate == W_EXEC);
    assign      w_is_pend   = (awaddr_r[9:8] == 2'b01);
    assign      w_is_reg    = (awaddr_r[9:3] == 7'h00);            // 0x000..0x007
    assign      w_is_ctrl0  = w_is_reg & ~awaddr_r[2];            // 仅 0x000 CTRL 可写
    wire [7:0]  w_byte_addr = {awaddr_r[7:2], wcnt};               // cell 地址

    wire        cmt_pend_lock = (commit_gate_en === 1'b1) & cmt_pend_q;   // z-免疫
    // pending(待换 bank)期间 PEND 窗口写入被丢弃: 防止 grant 换 bank 后
    // 半帧落入新 pend bank(两 bank 语义下物理上不可能存第三份帧)。
    // PS 契约(方案 C): 写整帧前查 COMMIT_STATUS.bit1=0(pending_locked=0)。
    reg         drop_seen_q;
    assign mb_wr_en   = w_exec & w_is_pend & wstrb_r[wcnt] & !cmt_pend_lock;
    assign mb_wr_addr = w_byte_addr;
    assign mb_wr_data = wdata_r[wcnt*8 +: 8];
    // v1.6 整帧原子: CTRL.COMMIT -> 请求; 授予(帧边界/空闲)才真正换 bank。
    // 挂起期间新 COMMIT 仅刷新请求(最新 pending 胜出), B 响应照常。
    assign      cmt_write  = w_exec & w_is_ctrl0 & wdata_r[1];
    assign      cmt_block  = (commit_gate_en === 1'b1) &
                             ((sched_busy_i === 1'b1) | (dd_busy_i === 1'b1) |
                              (init_busy_i  === 1'b1) | (fault_latched_i === 1'b1));
    wire        cmt_grant_now = cmt_pend_q & commit_grant_i & !cmt_block;
    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn)             cmt_pend_q <= 1'b0;
        else if (discard_pending_i === 1'b1) cmt_pend_q <= 1'b0;
        else if (cmt_write)     cmt_pend_q <= 1'b1;
        else if (cmt_grant_now) cmt_pend_q <= 1'b0;
    end
    assign commit_req_o = cmt_write | cmt_pend_q;
    wire [31:0] commit_status_val = {29'h0, drop_seen_q, cmt_pend_lock, cmt_pend_q};
    reg  [15:0] act_gen_q, pend_gen_q;
    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            act_gen_q  <= 16'd0;
            pend_gen_q <= 16'd0;
        end else begin
            if (mb_commit)  act_gen_q  <= act_gen_q  + 16'd1;
            if (cmt_write)  pend_gen_q <= pend_gen_q + 16'd1;
        end
    end
    assign active_gen_o  = act_gen_q;
    assign pending_gen_o = pend_gen_q;
    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn)
            drop_seen_q <= 1'b0;
        else if (cmt_pend_lock && w_exec && w_is_pend)
            drop_seen_q <= 1'b1;        // 粘滞: PS 写早了(帧被丢弃)的痕迹
    end
    assign commit_status_o = {drop_seen_q, cmt_pend_lock, cmt_pend_q};
    assign mb_commit  = (commit_gate_en === 1'b1) ? cmt_grant_now : cmt_write;  // z-免疫(旧 TB 未接 v1.6 端口)

    // ------------------------------------------------------------------------
    // 读通道: AR 锁存 -> EXEC(寄存器 mux 1 拍 / 缓冲区 6 拍流水) -> R
    // 缓冲区字节流水: rcnt 0..3 驱动字节地址, rcnt 1..4 采样拼装
    //   (字节 j 地址在 rcnt==j 驱动 -> 数据在 rcnt==j+1 的时钟沿写入 ->
    //    rcnt==j+1 拍沿采样; rcnt==1..4 依次采 byte0..3)
    // ------------------------------------------------------------------------
    localparam R_IDLE = 3'd0, R_EXEC = 3'd1, R_BURST = 3'd2, R_VALID = 3'd3;
    reg [2:0]  rstate;
    reg [2:0]  rcnt;
    reg [31:0] rdata_r;
    reg        rvalid_r;
    reg        ack_sticky;
    reg        was_status;

    assign s_axi_arready = s_axi_aresetn & (rstate == R_IDLE);
    assign s_axi_rvalid  = rvalid_r;
    assign s_axi_rresp   = 2'b00;                          // OKAY
    assign s_axi_rdata   = rdata_r;

    // 缓冲区字节选择: ACT 窗口走显示 bank 读端口, PEND 窗口走写入 bank 读端口
    wire       r_buf_sel_act = (araddr_r[9:8] == 2'b10);
    wire [7:0] r_bus_byte    = r_buf_sel_act ? mb_rd_data : mb_pend_rd_data;
    wire [7:0] r_byte_addr   = {araddr_r[7:2], rcnt[1:0]};

    assign mb_rd_addr      = (scan_rd_sel === 1'b1) ? scan_rd_addr :
                             ((rstate == R_BURST) && r_buf_sel_act ? r_byte_addr : 8'h00);
    assign mb_pend_rd_addr = (dd_pend_sel === 1'b1) ? dd_pend_addr :
                             ((rstate == R_BURST) && !r_buf_sel_act ? r_byte_addr : 8'h00);
    assign dd_pend_data    = mb_pend_rd_data;

    // STATUS: {29'h0, ENABLE回显, COMMIT_ACK(sticky), ACT_BANK}
    wire [31:0] status_val = {29'h0, enable_r, ack_sticky, mb_act_bank};

    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            araddr_r   <= 10'h0;
            rstate     <= R_IDLE;
            rcnt       <= 3'd0;
            rdata_r    <= 32'h0;
            rvalid_r   <= 1'b0;
            ack_sticky <= 1'b0;
            was_status <= 1'b0;
        end else begin
            // commit_ack 脉冲置 sticky; STATUS 读完成清除
            if (mb_commit_ack)
                ack_sticky <= 1'b1;
            else if (rvalid_r && s_axi_rready && was_status)
                ack_sticky <= 1'b0;

            case (rstate)
                R_IDLE: begin
                    if (s_axi_arvalid && s_axi_arready) begin
                        araddr_r   <= s_axi_araddr;
                        was_status <= (s_axi_araddr[9:2] == 8'h01);   // 0x004
                        rcnt       <= 3'd0;
                        rstate     <= R_EXEC;
                    end
                end
                R_EXEC: begin
                    if (ar_is_reg) begin
                        case (araddr_r[3:2])
                        2'b00: rdata_r <= {31'h0, enable_r};   // 0x000 CTRL
                        2'b01: rdata_r <= status_val;          // 0x004 STATUS
                        2'b10: rdata_r <= commit_status_val;   // 0x008 COMMIT_STATUS (v1.6)
                        default: rdata_r <= 32'h0;
                        endcase
                        rvalid_r <= 1'b1;                                 // 寄存器读: 立即有效
                        rstate   <= R_VALID;
                    end else begin
                        rcnt   <= 3'd0;
                        rstate <= R_BURST;                                // 缓冲区流水
                    end
                end
                R_BURST: begin
                    // 采样窗: 字节 j 地址在 rcnt==j 驱动, 数据于下一拍沿写入,
                    // rcnt==j+1 拍沿可采 -> rcnt 1..4 依次采 byte0..3
                    if (rcnt >= 3'd1)
                        rdata_r <= {r_bus_byte, rdata_r[31:8]};       // 拼装(低位先入)
                    if (rcnt == 3'd4) begin
                        rvalid_r <= 1'b1;
                        rstate   <= R_VALID;
                    end else begin
                        rcnt <= rcnt + 3'd1;
                    end
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

endmodule
