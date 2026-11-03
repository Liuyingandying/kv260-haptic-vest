// ============================================================================
// tactile_render_top.v -- 触觉渲染管线完整顶层（v1.1 全管线集成）
//
// 集成 8 个冻结模块（v1.0.1-rtl-fix 基线 + v1.1 向后兼容接口扩展）为完整数据链:
//
//   PS(GP0.M00) ─s_axi_ctrl─▶ matrix_buffer_axi ─commit_ack(frame_ready)─▶ scheduler_fsm
//   PS(GP0.M01) ─s_axi_sts──▶ axi_status_regs                            │ 逐通道 out_valid
//                                                                        ▼
//                              block_refresh_buffer ──16拍+trig──▶ pca9685_burst_builder
//   matrix_map_rom ◀─map_addr─┘(经 scheduler)                            │ m_* 字节流
//   matrix_buffer  ◀─rd_addr──┘(scan 读端口, v1.1 扩展)                   ▼
//   duty_lut_rom  ◀─level────┘                                      i2c_master
//
// I2C uses real open-drain pins. P0 faults contain the digital pipeline and
// actively retry FULL_OFF; no independent actuator power/OE output is implemented.
// ============================================================================

`timescale 1ns / 1ps

module kv260_haptic_pipeline #(
    parameter integer CLK_FREQ_HZ = 96_968_727,   // KV260 PL0 (psu_pl_clk0)
    parameter integer I2C_FREQ_HZ = 400_000,
    parameter integer I2C_TIMEOUT_US = 5000,
    parameter integer FAULT_RETRY_MS = 10,
    parameter integer FS_TIMEOUT_MS = 500,
    parameter integer FS_DEFER_MS = 50,
    // Independent integration candidate. Mode 0 preserves legacy ownership;
    // mode 1 grants this matrix buffer only through the KPF1 adapter.
    parameter integer UNIFIED_BRIDGE_MODE = 0,
    parameter integer MIN_ONLINE_BLOCKS = 16
)(
    input  wire        s_axi_aclk,
    input  wire        s_axi_aresetn,  // 低有效

    // ---- AXI4-Lite #0: Matrix Buffer 控制/数据窗口 (0x43C00000) ----
    input  wire        s_axi_ctrl_awvalid,
    input  wire [9:0]  s_axi_ctrl_awaddr,
    output wire        s_axi_ctrl_awready,
    input  wire        s_axi_ctrl_wvalid,
    input  wire [31:0] s_axi_ctrl_wdata,
    input  wire [3:0]  s_axi_ctrl_wstrb,
    output wire        s_axi_ctrl_wready,
    output wire        s_axi_ctrl_bvalid,
    output wire [1:0]  s_axi_ctrl_bresp,
    input  wire        s_axi_ctrl_bready,
    input  wire        s_axi_ctrl_arvalid,
    input  wire [9:0]  s_axi_ctrl_araddr,
    output wire        s_axi_ctrl_arready,
    output wire        s_axi_ctrl_rvalid,
    output wire [31:0] s_axi_ctrl_rdata,
    output wire [1:0]  s_axi_ctrl_rresp,
    input  wire        s_axi_ctrl_rready,

    // ---- AXI4-Lite #1: 系统状态/计数 (0x43C10000) ----
    input  wire        s_axi_sts_awvalid,
    input  wire [9:0]  s_axi_sts_awaddr,
    output wire        s_axi_sts_awready,
    input  wire        s_axi_sts_wvalid,
    input  wire [31:0] s_axi_sts_wdata,
    input  wire [3:0]  s_axi_sts_wstrb,
    output wire        s_axi_sts_wready,
    output wire        s_axi_sts_bvalid,
    output wire [1:0]  s_axi_sts_bresp,
    input  wire        s_axi_sts_bready,
    input  wire        s_axi_sts_arvalid,
    input  wire [9:0]  s_axi_sts_araddr,
    output wire        s_axi_sts_arready,
    output wire        s_axi_sts_rvalid,
    output wire [31:0] s_axi_sts_rdata,
    output wire [1:0]  s_axi_sts_rresp,
    input  wire        s_axi_sts_rready,

    // ---- 调试观测（BD 悬空/ILA）----
    output wire [7:0]  dbg_frame_cnt,
    output wire        dbg_i2c_ack_err,
    output wire        dbg_block_ready,

    // ---- v1.2 配置（BD 悬空 = 0; TB/后级可驱动）----
    input  wire        dd_bypass_i,     // 1 = 跳过 Dirty Detector 恒全刷（v1.1 回退）

    // ---- v1.3: I2C 开漏总线引出（真实 IOBUF 在顶层引脚推断; 板上需上拉,
    //      XDC PULLUP 兜底。实际约束见
    //      constraints/ax7020_i2c.xdc）----
    // Phase 2F-FIX: expose drive/sense separately so the TOP level can
    // instantiate an explicit IOBUF (open-drain). The FSM/timing is untouched.
    output wire        i2c_scl_drv_o,   // 1 = pull SCL low
    output wire        i2c_sda_drv_o,   // 1 = pull SDA low
    input  wire        i2c_scl_i_i,     // sensed SCL level
    input  wire        i2c_sda_i_i,     // sensed SDA level

    // ---- KV260 port: read-only status taps for the OE# safety guard ----
    // These are the SAME internal wires the AX7020 top already computes; the
    // only change is making them observable. No pipeline logic is altered.
    output wire        o_init_healthy,     // init_done && online==0xFFFF && no init err
    output wire        o_fault_latched,    // sticky I2C fault latch
    output wire        o_force_off,        // effective force_off (fs|sw|safety)
    output wire        o_runtime_run,      // runtime phase active
    output wire [15:0] o_online_mask,      // per-block online
    output wire [31:0] o_i2c_error_count,  // cumulative I2C errors

    // Unified-adapter sideband; ignored when UNIFIED_BRIDGE_MODE=0.
    // bridge_commit_i is AFTER actual generation confirmation, not merely
    // an AXI CTRL write acknowledgement.
    input  wire        bridge_force_off_i,
    input  wire        bridge_grant_i,
    input  wire        bridge_discard_i,
    input  wire        bridge_commit_i,
    input  wire        bridge_recovery_i,
    output wire        bridge_pending_o,
    output wire [15:0] bridge_active_gen_o,
    output wire        bridge_quiescent_o,
    output wire        bridge_dd_busy_o,
    output wire        bridge_init_busy_o
);

    // ========================================================================
    // 内部连线声明
    // ========================================================================
    wire clk = s_axi_aclk;
    wire rst_n = s_axi_aresetn;
    wire bld_busy, bld_blk_done;
    wire init_restart, init_healthy, runtime_run, safety_off;
    wire        mb_commit_ack;
    wire        mb_act_bank;
    wire [7:0]  scan_rd_data;
    wire [7:0]  map_addr, map_source_index;
    wire [3:0]  map_blk, map_ch;
    wire [6:0]  map_pca;
    wire [1:0]  map_tca;
    wire [7:0]  map_rd_addr;
    wire [2:0]  duty_level_bits;
    wire [11:0] duty_pwm;
    wire        duty_full_off;
    wire [3:0]  cur_blk, cur_ch;
    wire [7:0]  cur_src;
    wire [11:0] cur_pwm;
    wire        cur_foff;
    wire [6:0]  cur_pca;
    wire [1:0]  cur_tca;
    wire        out_valid, frame_done;
    wire        sched_busy;
    wire        blk_go = ~bld_busy;          // 块推进流控: 下游 I2C 排空才扫下一块

    // ---- v1.2: Dirty Detector / Failsafe Timer / 集成控制器 ----
    wire        commit_ack = UNIFIED_BRIDGE_MODE ? bridge_commit_i : mb_commit_ack;
    wire        dd_start, dd_busy, dd_dirty_valid;
    wire [7:0]  dd_new_addr, dd_old_addr, dd_pend_data;
    wire [15:0] dd_bitmap;
    wire [4:0]  dd_count;
    wire        dd_active;
    wire        fs_force_off;
    wire        v12_frame_ready;
    wire        v12_dirty_valid;
    wire [15:0] v12_dirty_bitmap;
    wire        v12_force_off;
    wire        recovery_frame, fs_refresh_frame, first_frame_seen;
    wire        sw_force_off;
    wire        sw_tca_en;
    // v1.2: 软件强制全断（SW_FS_CTRL）与硬件 failsafe 相或 = 有效 force_off
    wire        fs_force_eff = fs_force_off | sw_force_off | safety_off |
                               (UNIFIED_BRIDGE_MODE ? bridge_force_off_i : 1'b0);
    // v1.6: fault/init 上下文 + i2c 仲裁
    wire        legacy_fault_clear;
    // A request only: the unchanged recovery FSM admits it in HOLD after
    // every FULL_OFF transaction completed and the full pipeline is idle.
    // It never directly clears the IIC fault monitor or its error masks.
    wire        fault_clear = legacy_fault_clear |
                              (UNIFIED_BRIDGE_MODE ? bridge_recovery_i : 1'b0);
    wire        init_busy, init_done;
    wire [2:0] recovery_state;
    wire runtime_rst_n = rst_n && runtime_run;
    reg [3:0] bld_block_context;
    reg bld_off_context;
    reg [15:0] loaded_full_off;
    reg [31:0] i2c_timeout_count;
    reg need_fresh_commit;
    reg [15:0] recovery_pending_gen;
    wire m_timeout_error;
    wire [3:0]  init_blk;
    wire        init_pca_txn;
    wire        sw_fault_latched;
    wire        ar_cmd_start, ar_valid, ar_stop;
    wire [7:0]  ar_data;
    wire        ar_ready, ar_done, ar_ack_err;
    wire        ar_ready_rt;   // runtime 侧 ready(init 期间压 0)
    wire [15:0] online_mask, init_err_mask, run_err_mask, off_pend_mask;
    wire [31:0] i2c_err_cnt, off_retry_cnt;
    wire [15:0] active_gen, pending_gen;
    wire [2:0]  cs_status;
    wire [3:0]  sched_cur_blk = cur_blk;    wire        inj_sb_start, inj_sb_done, inj_sb_ack_err;
    wire [3:0]  inj_sb_blk;
    wire [2:0]  inj_sb_kind;
    wire        pinit_sb_start, pinit_sb_done, pinit_sb_ack_err;
    wire [2:0]  pinit_sb_kind;
    wire pinit_sb_last, pinit_sb_global;
    wire        buf_ch_wr;
    wire [3:0]  buf_ch_idx;
    wire [11:0] buf_ch_duty;
    wire        buf_ch_full_off;
    wire        buf_trig;
    wire [6:0]  buf_pca;
    wire        buf_busy, buf_blk_ready;
    wire [3:0]  buf_ready_blk;
    wire [7:0]  seq_err, drop_err, commit_err;
    wire        sw_commit;
    // v1.4: builder -> tca9548_inject -> i2c_master 字节流
    wire        bj_cmd_start, bj_valid, bj_stop, bj_ready, bj_busy, bj_done;
    wire bj_ack_err;
    wire [7:0]  bj_data;
    wire        m_cmd_start, m_byte_valid, m_byte_stop, m_byte_ready;
    wire [7:0]  m_byte_data;
    wire        m_busy, m_txn_done, m_ack_err;
    wire        bld_txn_done;                    // builder 每事务 done（未用, 防多驱动）
    wire        scl_drv, sda_drv;
    reg         blk_commit_r;
    reg [7:0]   sw_dbg_frame_cnt;
    assign ar_ack_err = m_ack_err;
    // Phase 2F-DEBUG: MIN_ONLINE_BLOCKS parameterises the bring-up gate.
//   16 (default) = original behaviour: all 16 PCA9685 must be online, no init error.
//    1           = debug build: any one online block is enough to enter RUN.
// Restore to 16 for the full 256-channel acceptance build.
// This isolated candidate defaults to all 16 boards. The historical source
// retains its one-board debug constant and is not modified by this candidate.
wire [4:0] online_block_cnt = online_mask[0] +online_mask[1] +online_mask[2] +online_mask[3]
                            + online_mask[4] +online_mask[5] +online_mask[6] +online_mask[7]
                            + online_mask[8] +online_mask[9] +online_mask[10]+online_mask[11]
                            + online_mask[12]+online_mask[13]+online_mask[14]+online_mask[15];
assign init_healthy = init_done &&
                      (online_block_cnt >= MIN_ONLINE_BLOCKS) &&
                      ((MIN_ONLINE_BLOCKS >= 16) ? (init_err_mask == 0) : 1'b1);
    wire pipeline_idle = !m_busy && !bld_busy && !buf_busy && !sched_busy &&
                         !dd_active && !bj_busy && !blk_commit_r && !buf_trig && !v12_frame_ready &&
                         !ar_done && !bld_blk_done && !inj_sb_done && !pinit_sb_done;
    fault_recovery_ctrl #(.CLK_FREQ(CLK_FREQ_HZ), .RETRY_MS(FAULT_RETRY_MS)) u_recovery (
        .clk(clk), .rst_n(rst_n), .init_busy(init_busy), .init_done(init_done),
        .init_healthy(init_healthy), .fault_latched(sw_fault_latched),
        .off_pending(off_pend_mask), .pipeline_idle(pipeline_idle),
        .runtime_busy(sched_busy || bld_busy || buf_busy || v12_frame_ready),
        .fault_clear(fault_clear), .runtime_run(runtime_run), .safety_off(safety_off),
        .init_restart(init_restart), .state_o(recovery_state)
    );
    always @(posedge clk or negedge runtime_rst_n) begin
        if (!runtime_rst_n) begin
            bld_block_context<=0; bld_off_context<=0; loaded_full_off<=0;
        end else begin
            if(buf_ch_wr) loaded_full_off[buf_ch_idx]<=buf_ch_full_off;
            if(buf_trig) begin
                bld_block_context<=buf_ready_blk;
                // Certify loaded data, never a live watchdog/software request.
                bld_off_context<=&loaded_full_off;
            end
        end
    end
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) i2c_timeout_count<=0;
        else if(ar_done && m_timeout_error) i2c_timeout_count<=i2c_timeout_count+1;
    end
    // Retain both matrix banks, but never promote a pre-recovery pending request.
    // A new software COMMIT after healthy RUN is the explicit resume event.
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin need_fresh_commit<=0; recovery_pending_gen<=0; end
        else if(sw_fault_latched) begin
            need_fresh_commit<=1; recovery_pending_gen<=pending_gen;
        end else if(need_fresh_commit && !runtime_run) recovery_pending_gen<=pending_gen;
        else if(need_fresh_commit && (pending_gen!=recovery_pending_gen)) need_fresh_commit<=0;
    end

    // ========================================================================
    // 1) Matrix Buffer + AXI 封装（v1.1 扩展: scan 读端口供 Scheduler 复用）
    // ========================================================================
    matrix_buffer_axi u_mb_axi (
        .s_axi_aclk    (s_axi_aclk),
        .s_axi_aresetn (s_axi_aresetn),
        .s_axi_awvalid (s_axi_ctrl_awvalid),
        .s_axi_awaddr  (s_axi_ctrl_awaddr),
        .s_axi_awready (s_axi_ctrl_awready),
        .s_axi_wvalid  (s_axi_ctrl_wvalid),
        .s_axi_wdata   (s_axi_ctrl_wdata),
        .s_axi_wstrb   (s_axi_ctrl_wstrb),
        .s_axi_wready  (s_axi_ctrl_wready),
        .s_axi_bvalid  (s_axi_ctrl_bvalid),
        .s_axi_bresp   (s_axi_ctrl_bresp),
        .s_axi_bready  (s_axi_ctrl_bready),
        .s_axi_arvalid (s_axi_ctrl_arvalid),
        .s_axi_araddr  (s_axi_ctrl_araddr),
        .s_axi_arready (s_axi_ctrl_arready),
        .s_axi_rvalid  (s_axi_ctrl_rvalid),
        .s_axi_rdata   (s_axi_ctrl_rdata),
        .s_axi_rresp   (s_axi_ctrl_rresp),
        .s_axi_rready  (s_axi_ctrl_rready),
        .act_bank_o    (mb_act_bank),
        .commit_ack_o  (mb_commit_ack),
        .enable_o      (),
        .scan_rd_addr  (dd_active ? dd_new_addr : map_rd_addr),
        .scan_rd_sel   (dd_active | sched_busy),
        .scan_rd_data  (scan_rd_data),
        .dd_pend_addr  (dd_old_addr),
        .dd_pend_sel   (dd_active),
        .dd_pend_data  (dd_pend_data),
        // v1.6: 整帧原子 commit 门控
        .commit_gate_en(1'b1),
        .sched_busy_i  (sched_busy),
        .dd_busy_i     (dd_active),
        .init_busy_i   (init_busy || !runtime_run),
        .fault_latched_i(sw_fault_latched),
        .commit_grant_i((!need_fresh_commit || (pending_gen!=recovery_pending_gen)) &&
                        (UNIFIED_BRIDGE_MODE ? bridge_grant_i : 1'b1)),
        .discard_pending_i(init_restart || (UNIFIED_BRIDGE_MODE ? bridge_discard_i : 1'b0)),
        .commit_req_o(bridge_pending_o),
        .active_gen_o  (active_gen),
        .pending_gen_o (pending_gen),
        .commit_status_o (cs_status)
    );

    // ========================================================================
    // 2) 扫描链
    // ========================================================================


    matrix_map_rom u_map (
        .clk          (s_axi_aclk),
        .matrix_addr  (map_addr),
        .source_index (map_source_index),
        .block_id     (map_blk),
        .channel_id   (map_ch),
        .pca_addr     (map_pca),
        .tca_ch       (map_tca)
    );

    duty_lut_rom u_duty (
        .clk       (s_axi_aclk),
        .level     (duty_level_bits),
        .pwm_value (duty_pwm),
        .full_off  (duty_full_off)
    );

    // 注: matrix_buffer 本体位于 u_mb_axi 内部; Scheduler 经其 v1.1 scan 读端口访问

    scheduler_fsm u_sched (
        .clk             (s_axi_aclk),
        .rst_n           (runtime_rst_n),
        .frame_ready     (v12_frame_ready),   // v1.2: 集成控制器门控
        .dirty_valid     (v12_dirty_valid),
        .dirty_bitmap    (v12_dirty_bitmap),
        .force_off       (v12_force_off),
        .level_data      (scan_rd_data),
        .source_index    (map_source_index),
        .block_id        (map_blk),
        .channel_id      (map_ch),
        .pca_addr        (map_pca),
        .tca_ch          (map_tca),
        .pwm_value       (duty_pwm),
        .full_off        (duty_full_off),
        .map_addr        (map_addr),
        .rd_addr         (map_rd_addr),
        .duty_level      (duty_level_bits),
        .current_block   (cur_blk),
        .current_channel (cur_ch),
        .current_source_index (cur_src),
        .current_pwm     (cur_pwm),
        .current_full_off(cur_foff),
        .current_pca_addr(cur_pca),
        .current_tca_ch  (cur_tca),
        .out_valid       (out_valid),
        .frame_done      (frame_done),
        .busy_o          (sched_busy),
        .blk_go_i        (blk_go)
    );

    // ========================================================================
    // 2.5) v1.2: Dirty Detector（commit 后独占双读口 ~261 拍）
    // ========================================================================
    dirty_detector u_dd (
        .clk            (clk),
        .rst_n          (runtime_rst_n),
        .start          (dd_start),
        .old_frame_addr (dd_old_addr),
        .old_frame_data (dd_pend_data),
        .new_frame_addr (dd_new_addr),
        .new_frame_data (scan_rd_data),
        .dirty_valid    (dd_dirty_valid),
        .dirty_bitmap   (dd_bitmap),
        .dirty_count    (dd_count),
        .busy           (dd_busy)
    );

    // ========================================================================
    // 3) 块级刷新缓冲
    // ========================================================================
    // 块提交自动生成: 块内最后一个通道有效后 1 拍（含软件强制并联合路）
    always @(posedge s_axi_aclk or negedge runtime_rst_n) begin
        if (!runtime_rst_n)
            blk_commit_r <= 1'b0;
        else
            blk_commit_r <= (out_valid && (cur_ch == 4'd15)) || sw_commit;
    end

    block_refresh_buffer u_buf (
        .clk           (s_axi_aclk),
        .rst_n         (runtime_rst_n),
        .ch_valid      (out_valid),
        .block_id      (cur_blk),
        .channel_id    (cur_ch),
        .pwm_value     (cur_pwm),
        .full_off      (cur_foff),
        .pca_addr_in   (cur_pca),
        .block_commit  (blk_commit_r),
        .bld_busy      (bld_busy),
        .bld_done      (bld_blk_done),
        .ch_wr         (buf_ch_wr),
        .ch_idx        (buf_ch_idx),
        .ch_duty       (buf_ch_duty),
        .ch_full_off   (buf_ch_full_off),
        .block_trig    (buf_trig),
        .bld_pca_addr  (buf_pca),
        .ready_block_id(buf_ready_blk),
        .block_ready   (buf_blk_ready),
        .busy          (buf_busy),
        .seq_err       (seq_err),
        .drop_err      (drop_err),
        .commit_err    (commit_err)
    );

    // ========================================================================
    // 4) 突发组装器 + I2C 主机
    // ========================================================================
    pca9685_burst_builder u_bld (
        .clk          (s_axi_aclk),
        .rst_n        (runtime_rst_n),
        .ch_wr        (buf_ch_wr),
        .ch_idx       (buf_ch_idx),
        .ch_duty      (buf_ch_duty),
        .ch_full_off  (buf_ch_full_off),
        .block_trig   (buf_trig),
        .pca_addr     (buf_pca),
        .mode1_en     (1'b1),
        .m_cmd_start  (bj_cmd_start),
        .m_byte_valid (bj_valid),
        .m_byte_data  (bj_data),
        .m_byte_stop  (bj_stop),
        .m_byte_ready (bj_ready),
        .m_busy       (bj_busy),
        .m_done       (bj_done),       // 等 I2C 事务完成（master.done 经注入器门控）
        .m_failed     (bj_ack_err),
        .busy         (bld_busy),
        .done         (bld_txn_done),  // builder 块完成（独立线网, 勿与 master.done 共线）
        .blk_done     (bld_blk_done)
    );

    // v1.4: TCA9548 选通注入器（en=SW_CTRL bit1 软件使能; 复位/缺省 0 = 纯直通）
    tca9548_inject u_inj (
        .clk        (s_axi_aclk),
        .rst_n      (runtime_rst_n),
        .en         (sw_tca_en | safety_off | (UNIFIED_BRIDGE_MODE != 0)), .sched_blk_i(bld_block_context),
        .fs_i       (bld_off_context),
        .sb_start_o (inj_sb_start),
        .sb_blk_o   (inj_sb_blk),
        .sb_kind_o  (inj_sb_kind),
        .sb_done_o  (inj_sb_done),
        .sb_ack_err_o(inj_sb_ack_err),
        .b_cmd_start(bj_cmd_start),
        .b_valid    (bj_valid),
        .b_data     (bj_data),
        .b_stop     (bj_stop),
        .b_ready    (bj_ready),
        .b_busy     (bj_busy),
        .b_done     (bj_done),
        .b_ack_err  (bj_ack_err),
        .m_cmd_start(m_cmd_start),
        .m_valid    (m_byte_valid),
        .m_data     (m_byte_data),
        .m_stop     (m_byte_stop),
        .m_ready    (m_byte_ready),
        .m_busy     (m_busy),
        .m_done     (m_txn_done),
        .m_ack_err  (m_ack_err),
        .m_start_accepted(ar_cmd_start && !m_busy && !init_busy)
    );

    // ========================================================================
    // 4.5) v1.2: Failsafe Timer + 集成控制器
    // ========================================================================
    failsafe_timer #(
        .CLK_FREQ   (CLK_FREQ_HZ),
        .TIMEOUT_MS (FS_TIMEOUT_MS),
        .DEFER_MS   (FS_DEFER_MS)
    ) u_fs (
        .clk            (clk),
        .rst_n          (rst_n),
        .frame_valid    (commit_ack),
        .refresh_active (!pipeline_idle), // scan complete precedes final I2C STOP
        .force_off      (fs_force_off)
    );

    tactile_v12_ctrl u_v12 (
        .clk               (clk),
        .rst_n             (runtime_rst_n),
        .commit_ack        (commit_ack),
        .fs_force_off      (fs_force_eff),
        .frame_valid_fs    (),
        .dd_start          (dd_start),
        .dd_busy           (dd_busy),
        .dd_dirty_valid    (dd_dirty_valid),
        .dd_dirty_bitmap   (dd_bitmap),
        .dd_active         (dd_active),
        .frame_ready       (v12_frame_ready),
        .sched_dirty_valid (v12_dirty_valid),
        .sched_dirty_bitmap(v12_dirty_bitmap),
        .sched_force_off   (v12_force_off),
        .frame_done        (frame_done),
        .sched_busy        (sched_busy),
        .dd_bypass         (dd_bypass_i),
        .recovery_frame    (recovery_frame),
        .fs_refresh_frame  (fs_refresh_frame),
        .first_frame_seen  (first_frame_seen)
    );

    // v1.6: INIT sequencer + master 仲裁(INIT 期间独占, runtime 直通)
    pca_init_sequencer #(.CLK_FREQ(CLK_FREQ_HZ), .PWM_HZ(50)) u_pinit (
        .clk          (s_axi_aclk),
        .rst_n        (s_axi_aresetn),
        .start        (1'b1),
        .restart      (init_restart),
        .init_busy    (init_busy),
        .init_done    (init_done),
        .cur_blk      (init_blk),
        .cur_pca_txn  (init_pca_txn),        .sb_start     (pinit_sb_start),
        .sb_done      (pinit_sb_done),
        .sb_ack_err   (pinit_sb_ack_err),
        .sb_kind      (pinit_sb_kind),
        .sb_init_last (pinit_sb_last), .sb_init_global(pinit_sb_global),
        .r_cmd_start  (m_cmd_start),
        .r_valid      (m_byte_valid),
        .r_data       (m_byte_data),
        .r_stop       (m_byte_stop),
        .r_ready      (m_byte_ready),
        .r_done_gated (m_txn_done),
        .m_busy       (m_busy),
        .m_done       (ar_done),
        .m_ack_err    (ar_ack_err),
        .m_cmd_start  (ar_cmd_start),
        .m_valid      (ar_valid),
        .m_data       (ar_data),
        .m_stop       (ar_stop),
        .m_ready      (ar_ready)
    );

    assign ar_ready_rt = ar_ready & ~init_busy;

    i2c_fault_monitor u_fmon (
        .clk          (s_axi_aclk),
        .rst_n        (s_axi_aresetn),
        .init_done    (init_done),
        .fault_clear  (fault_clear),
        .init_restart (init_restart), .init_healthy(init_healthy),
        .sb_init_last (pinit_sb_last), .sb_init_global(pinit_sb_global),
        .sb_start     (init_busy ? pinit_sb_start : inj_sb_start),
        .sb_kind      (init_busy ? pinit_sb_kind : inj_sb_kind),
        .sb_blk       (init_busy ? init_blk : inj_sb_blk),
        .sb_done      (init_busy ? pinit_sb_done : inj_sb_done),
        .sb_ack_err   (init_busy ? pinit_sb_ack_err : inj_sb_ack_err),
        .online_mask    (online_mask),
        .init_error_mask   (init_err_mask),
        .runtime_error_mask(run_err_mask),
        .off_pending_mask   (off_pend_mask),
        .fault_latched  (sw_fault_latched),
        .i2c_error_count   (i2c_err_cnt),
        .off_retry_count   (off_retry_cnt)
    );

    i2c_master #(.CLK_FREQ_HZ(CLK_FREQ_HZ), .I2C_FREQ_HZ(I2C_FREQ_HZ),
        .TIMEOUT_US(I2C_TIMEOUT_US), .ABORT_ON_NACK(1)) u_i2c (
        .clk        (s_axi_aclk),
        .rst_n      (s_axi_aresetn),
        .cmd_start  (ar_cmd_start),
        .abort_req  (!init_busy && fs_force_eff && !bld_off_context),
        .timeout_error(m_timeout_error),
        .byte_valid (ar_valid),
        .byte_data  (ar_data),
        .byte_stop  (ar_stop),
        .byte_ready (ar_ready),
        .ack_error  (m_ack_err),
        .done       (ar_done),
        .busy       (m_busy),
        .scl_drv    (scl_drv),
        .sda_drv    (sda_drv),
        // v1.3: 回读真实引脚电平（开漏 + 外部/内部上拉）, 从机 ACK/时钟
        // 拉伸经由实际总线采样, 替代 v1.2 的内部回环
        .scl_i      (i2c_scl_i_i),
        .sda_i      (i2c_sda_i_i)
    );

    // 开漏三态: drv=1 拉低, drv=0 释放（上拉为高）; 顶层引脚处由 Vivado
    // 推断 IOBUF, 综合语义与 IOBUF 原语等价
    // Phase 2F-FIX: drive intent is exported; the actual tri-state IOBUF
    // lives at the top level so it can never be optimized into an OBUF.
    assign i2c_scl_drv_o = scl_drv;
    assign i2c_sda_drv_o = sda_drv;

    // ========================================================================
    // 5) 系统状态/计数寄存器
    // ========================================================================
    axi_status_regs u_sts (
        .clk           (s_axi_aclk),
        .rst_n         (s_axi_aresetn),
        .s_axi_awvalid (s_axi_sts_awvalid),
        .s_axi_awaddr  (s_axi_sts_awaddr),
        .s_axi_awready (s_axi_sts_awready),
        .s_axi_wvalid  (s_axi_sts_wvalid),
        .s_axi_wdata   (s_axi_sts_wdata),
        .s_axi_wready  (s_axi_sts_wready),
        .s_axi_bvalid  (s_axi_sts_bvalid),
        .s_axi_bresp   (s_axi_sts_bresp),
        .s_axi_bready  (s_axi_sts_bready),
        .s_axi_arvalid (s_axi_sts_arvalid),
        .s_axi_araddr  (s_axi_sts_araddr),
        .s_axi_arready (s_axi_sts_arready),
        .s_axi_rvalid  (s_axi_sts_rvalid),
        .s_axi_rdata   (s_axi_sts_rdata),
        .s_axi_rresp   (s_axi_sts_rresp),
        .s_axi_rready  (s_axi_sts_rready),
        .frame_done_i  (frame_done),
        .burst_trig_i  (buf_trig),
        .sched_busy_i  (sched_busy),
        .buf_busy_i    (buf_busy),
        .bld_busy_i    (bld_busy),
        .i2c_busy_i    (m_busy),
        .block_ready_i (buf_blk_ready),
        .i2c_ack_err_i (m_ack_err),
        // ---- v1.2 ----
        .dirty_bitmap_i(dd_bitmap),
        .dirty_count_i (dd_count),
        .dd_busy_i     (dd_busy),
        .dd_active_i   (dd_active),
        .dd_bypass_i   (dd_bypass_i),
        .fs_active_i   (fs_force_eff),
        .recovery_seen_i   (recovery_frame),
        .fs_refresh_seen_i (fs_refresh_frame),
        .first_frame_seen_i(first_frame_seen),
        .seq_err_i     (seq_err),
        .drop_err_i    (drop_err),
        .commit_err_i  (commit_err),
        .sw_commit_o   (sw_commit),
        .sw_force_off_o(sw_force_off),
        .init_done_i   (init_healthy),
        .online_mask_i (online_mask),
        .init_error_mask_i (init_err_mask),
        .runtime_error_mask_i (run_err_mask),
        .off_pending_mask_i (off_pend_mask),
        .fault_latched_i (sw_fault_latched),
        .i2c_error_cnt_i (i2c_err_cnt),
        .off_retry_cnt_i (off_retry_cnt),
        .i2c_timeout_cnt_i(i2c_timeout_count),
        .recovery_status_i({25'd0,1'b1,init_restart,safety_off,runtime_run,recovery_state}),
        .active_gen_i  (active_gen),
        .pending_gen_i (pending_gen),
        .fault_clear_o (legacy_fault_clear),
        .sw_tca_en_o   (sw_tca_en)
    );

    assign dbg_frame_cnt   = sw_dbg_frame_cnt;
    assign dbg_i2c_ack_err = m_ack_err;
    assign dbg_block_ready = buf_blk_ready;

    always @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn)
            sw_dbg_frame_cnt <= 8'h00;
        else if (frame_done)
            sw_dbg_frame_cnt <= sw_dbg_frame_cnt + 8'd1;
    end

    // ---- KV260 port: status tap drive (read-only) ----
    assign o_init_healthy    = init_healthy;
    assign o_fault_latched   = sw_fault_latched;
    assign o_force_off       = fs_force_eff;
    assign o_runtime_run     = runtime_run;
    assign o_online_mask     = online_mask;
    assign o_i2c_error_count = i2c_err_cnt;
    assign bridge_active_gen_o = active_gen;
    // Includes every downstream staging/transaction completion boundary, not
    // just the scheduler scan. A last PCA STOP must finish before a new bank.
    assign bridge_quiescent_o = pipeline_idle;
    assign bridge_dd_busy_o = dd_active;
    assign bridge_init_busy_o = init_busy || !runtime_run;

endmodule
