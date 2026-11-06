// KV260统一板级顶层：PS Block Design + 统一感知/触觉逻辑 + 真实开漏IOBUF。
// R6统一数字候选（非马达放行）：OBS2常驻；bit30申请数字pad生命周期，先bus-clear/再init，结束先drain/再关闭。
// 编译/实现通过不等于TCA/PCA ACK或马达验收通过。
`timescale 1ns/1ps
module kv260_unified_board_top #(
    parameter [63:0] SIGNAL_WINDOW_CYCLES=64'd5_000_000_000,
    // This independently identified DGM2 candidate enables matrix bench mode.
    // Adapter/wrapper defaults and the original N6D1 release remain unchanged.
    parameter integer DIGITAL_MATRIX_MODE=1,
    parameter [15:0] DIGITAL_TARGET_MASK=16'hf0f0
) (
    inout wire i2c_scl_io,
    inout wire i2c_sda_io
);
    wire pl_clk0, pl_resetn0;
    wire [31:0] diag_ctrl_o, diag_ctrl_t;
    wire [31:0] diag_status_i;
    reg [31:0] diag_measure_i;
    // 沿用已验收诊断平台的独立总线启动释放：先让GPIO/互联可读，
    // 再根据真实PS reset或显式原有bit31复位覆盖决定payload复位。
    reg [15:0] bus_startup=16'b0;
    reg [31:0] diag_ticks=32'b0;
    (* ASYNC_REG="TRUE" *) reg [1:0] ps_reset_sync=2'b0;
    always @(posedge pl_clk0) begin
        bus_startup <= {bus_startup[14:0],1'b1};
        diag_ticks <= diag_ticks + 1'b1;
        ps_reset_sync <= {ps_reset_sync[0],pl_resetn0};
    end
    wire pl_bus_resetn = &bus_startup;
    // bit31是原平台已有的payload复位覆盖，不是SCL/SDA或马达输出许可。
    wire reset_override = diag_ctrl_o[31] & ~diag_ctrl_t[31];
    wire base_core_resetn = pl_bus_resetn & (ps_reset_sync[1] | reset_override);
    wire window_active, window_expired, arm_synced, window_start;
    wire arm_request = diag_ctrl_o[30] & ~diag_ctrl_t[30];
    // AXI mailbox/status stay resident when the pad window expires/disarms.
    // Only legitimate PS/base reset controls this core reset domain.
    wire core_resetn = base_core_resetn;
    // C1 bit23 is a default-disabled digital control request, not physical OE proof.
    // Formal quality, proven capture age, sequence and all16 local health still apply.
    // GPIO direction must be output. Default TRI=all1 means no request.
    wire operator_control_request = diag_ctrl_o[23] & ~diag_ctrl_t[23];
    (* ASYNC_REG="TRUE" *) reg [1:0] operator_control_sync=2'b0;
    always @(posedge pl_clk0) begin
        if (!base_core_resetn) operator_control_sync <= 2'b0;
        else operator_control_sync <= {operator_control_sync[0],operator_control_request};
    end
    wire operator_control_arm = operator_control_sync[1];
    wire runtime_permit = operator_control_arm && window_active && !diag_owner; // C1 digital logical permit; never physical OE proof.
    // bit29仅请求原恢复FSM；必须先观察HOLD/off_pending=0/管线排空。
    // data=1且方向TRI=0才有效。bit30只控制有界pad窗口；bit28另行请求逻辑控制。
    wire recovery_request = diag_ctrl_o[29] & ~diag_ctrl_t[29];
    (* ASYNC_REG="TRUE" *) reg [1:0] recovery_sync=2'b0;
    always @(posedge pl_clk0) begin
        if (!pl_bus_resetn) recovery_sync <= 2'b0;
        else recovery_sync <= {recovery_sync[0],recovery_request};
    end
    wire [31:0] mail_awaddr;
    wire [2:0] mail_awprot;
    wire mail_awvalid;
    wire mail_awready;
    wire [31:0] mail_wdata;
    wire [3:0] mail_wstrb;
    wire mail_wvalid;
    wire mail_wready;
    wire [1:0] mail_bresp;
    wire mail_bvalid;
    wire mail_bready;
    wire [31:0] mail_araddr;
    wire [2:0] mail_arprot;
    wire mail_arvalid;
    wire mail_arready;
    wire [31:0] mail_rdata;
    wire [1:0] mail_rresp;
    wire mail_rvalid;
    wire mail_rready;
    wire [31:0] sts_awaddr;
    wire [2:0] sts_awprot;
    wire sts_awvalid;
    wire sts_awready;
    wire [31:0] sts_wdata;
    wire [3:0] sts_wstrb;
    wire sts_wvalid;
    wire sts_wready;
    wire [1:0] sts_bresp;
    wire sts_bvalid;
    wire sts_bready;
    wire [31:0] sts_araddr;
    wire [2:0] sts_arprot;
    wire sts_arvalid;
    wire sts_arready;
    wire [31:0] sts_rdata;
    wire [1:0] sts_rresp;
    wire sts_rvalid;
    wire sts_rready;

    wire scl_sense, sda_sense, scl_low, sda_low;
    wire core_scl_low, core_sda_low;
    wire guard_pad_enable,guard_core_resetn,guard_issue_enable,guard_fault,guard_close_clean;
    wire iic_busy,iic_cmd_start;
    wire [31:0] guard_status;
    // Diagnostic IIC owner uses spare GPIO bits only, never physical push-pull.
    // Owner is latched while pads are closed and cannot switch during a transfer.
    // GPIO bits:25 request owner,26 SCL pull-low,27 SDA pull-low,24 busy.
    wire diag_owner_request=diag_ctrl_o[25]&~diag_ctrl_t[25];
    wire diag_scl_low=diag_ctrl_o[26]&~diag_ctrl_t[26];
    wire diag_sda_low=diag_ctrl_o[27]&~diag_ctrl_t[27];
    wire diag_busy=diag_ctrl_o[24]&~diag_ctrl_t[24];
    reg diag_owner=1'b0;
    always @(posedge pl_clk0) begin
        if(!pl_bus_resetn)diag_owner<=1'b0;
        else if(!guard_pad_enable&&!guard_core_resetn)diag_owner<=diag_owner_request;
    end
    // Dedicated flip-flop is the sole reset driver. No LUT decode feeds async
    // resets; diagnostic ownership holds the business init/master in reset.
    reg haptic_resetn_registered=1'b0;
    always @(posedge pl_clk0) begin
        if(!pl_bus_resetn)haptic_resetn_registered<=1'b0;
        else haptic_resetn_registered<=guard_core_resetn&&!diag_owner;
    end
    wire guard_busy=diag_owner?diag_busy:iic_busy;
    wire guard_scl_low=diag_owner?diag_scl_low:core_scl_low;
    wire guard_sda_low=diag_owner?diag_sda_low:core_sda_low;
    // No window_active AND here: a lease withdrawal must not cut off STOP.
    i2c_pad_lifecycle_guard #(.CLK_FREQ_HZ(100_000_000)) u_pad_guard (
        .clk(pl_clk0),.rst_n(pl_bus_resetn),.request_i(window_active&&base_core_resetn),
        .core_busy_i(guard_busy),.raw_scl_low_i(guard_scl_low),.raw_sda_low_i(guard_sda_low),
        .scl_i(scl_sync),.sda_i(sda_sync),
        .pad_enable_o(guard_pad_enable),.core_resetn_o(guard_core_resetn),.issue_enable_o(guard_issue_enable),
        .scl_low_o(scl_low),.sda_low_o(sda_low),.fault_o(guard_fault),.close_clean_o(guard_close_clean),.status_o(guard_status)
    );
    (* MARK_DEBUG="TRUE" *) wire raw_scl_low, raw_sda_low;
    (* MARK_DEBUG="TRUE" *) wire force_off, control_valid, active_valid;
    (* MARK_DEBUG="TRUE" *) wire init_healthy, fault_latched, pipeline_quiescent;
    wire oe_disable, control_commit;
    wire [255:0] effective_unknown;
    wire [31:0] observation_gen, control_session, fault_reason;
    wire [63:0] control_seq;
    wire [15:0] haptic_active_gen, online_mask;
    wire [7:0] frame_count;
    wire [31:0] first_failure_info;
    wire [15:0] init_tca_failure_mask, init_pca_failure_mask;
    // 外部IIC异步输入：只在新板级边界加入两级同步；原核心不改。
    // 名义100MHz下约两拍延迟，不能当作实测滤波/电气验收。
    // 约束仅豁免pad到第一级D，meta→sync及sync→核心仍正常分析。
    (* ASYNC_REG="TRUE" *) reg scl_meta=1'b1;
    (* ASYNC_REG="TRUE" *) reg scl_sync=1'b1;
    (* ASYNC_REG="TRUE" *) reg sda_meta=1'b1;
    (* ASYNC_REG="TRUE" *) reg sda_sync=1'b1;
    always @(posedge pl_clk0) begin
        scl_meta <= scl_sense;
        scl_sync <= scl_meta;
        sda_meta <= sda_sense;
        sda_sync <= sda_meta;
    end
    // 唯一物理缓冲：I固定0，只能拉低或高阻；O回读真实pad，不能接常量1。
    // 本信号副本core允许IIC输出意图，但窗口默认OFF；DONT_TOUCH不授权马达。
    // 窗口到期/撤销只释放管脚，邮箱/状态不复位；不把IIC释放当实体PWM关闭。
    (* DONT_TOUCH="TRUE" *)
    IOBUF #(.IOSTANDARD("LVCMOS33"),.DRIVE(4),.SLEW("SLOW")) u_scl_iobuf (
        .I(1'b0),.T(~scl_low),.O(scl_sense),.IO(i2c_scl_io));
    (* DONT_TOUCH="TRUE" *)
    IOBUF #(.IOSTANDARD("LVCMOS33"),.DRIVE(4),.SLEW("SLOW")) u_sda_iobuf (
        .I(1'b0),.T(~sda_low),.O(sda_sense),.IO(i2c_sda_io));

    kv260_unified_ps_wrapper u_ps_bd (
        .M_AXI_PERCEPTION_awaddr(mail_awaddr),
        .M_AXI_PERCEPTION_awprot(mail_awprot),
        .M_AXI_PERCEPTION_awvalid(mail_awvalid),
        .M_AXI_PERCEPTION_awready(mail_awready),
        .M_AXI_PERCEPTION_wdata(mail_wdata),
        .M_AXI_PERCEPTION_wstrb(mail_wstrb),
        .M_AXI_PERCEPTION_wvalid(mail_wvalid),
        .M_AXI_PERCEPTION_wready(mail_wready),
        .M_AXI_PERCEPTION_bresp(mail_bresp),
        .M_AXI_PERCEPTION_bvalid(mail_bvalid),
        .M_AXI_PERCEPTION_bready(mail_bready),
        .M_AXI_PERCEPTION_araddr(mail_araddr),
        .M_AXI_PERCEPTION_arprot(mail_arprot),
        .M_AXI_PERCEPTION_arvalid(mail_arvalid),
        .M_AXI_PERCEPTION_arready(mail_arready),
        .M_AXI_PERCEPTION_rdata(mail_rdata),
        .M_AXI_PERCEPTION_rresp(mail_rresp),
        .M_AXI_PERCEPTION_rvalid(mail_rvalid),
        .M_AXI_PERCEPTION_rready(mail_rready),
        .M_AXI_STATUS_awaddr(sts_awaddr),
        .M_AXI_STATUS_awprot(sts_awprot),
        .M_AXI_STATUS_awvalid(sts_awvalid),
        .M_AXI_STATUS_awready(sts_awready),
        .M_AXI_STATUS_wdata(sts_wdata),
        .M_AXI_STATUS_wstrb(sts_wstrb),
        .M_AXI_STATUS_wvalid(sts_wvalid),
        .M_AXI_STATUS_wready(sts_wready),
        .M_AXI_STATUS_bresp(sts_bresp),
        .M_AXI_STATUS_bvalid(sts_bvalid),
        .M_AXI_STATUS_bready(sts_bready),
        .M_AXI_STATUS_araddr(sts_araddr),
        .M_AXI_STATUS_arprot(sts_arprot),
        .M_AXI_STATUS_arvalid(sts_arvalid),
        .M_AXI_STATUS_arready(sts_arready),
        .M_AXI_STATUS_rdata(sts_rdata),
        .M_AXI_STATUS_rresp(sts_rresp),
        .M_AXI_STATUS_rvalid(sts_rvalid),
        .M_AXI_STATUS_rready(sts_rready),
        .diag_ctrl_o(diag_ctrl_o),
        .diag_ctrl_t(diag_ctrl_t),
        .diag_status_i(diag_status_i),
        .diag_measure_i(diag_measure_i),
        .pl_clk0(pl_clk0),
        .pl_resetn0(pl_resetn0),
        .pl_bus_resetn(pl_bus_resetn)
    );

    // C1逻辑许可=显式bit23输出请求经两拍同步、pad窗口有效、非诊断owner；OE内部仍禁止。
    // PL0名义100MHz；实际运行必须验证真实频率，不能只依据此参数宣称PASS。
    unified_perception_haptic_runtime #(
        .CLK_FREQ_HZ(100_000_000),.TICKS_PER_US(100),
        .FRESH_US(250000),.FUTURE_US(40000),
        .I2C_FREQ_HZ(100_000),.I2C_TIMEOUT_US(20000),
        .CONTROL_PATH_ENABLE(1),.ACTUATOR_PINS_ENABLE(1),.MIN_ONLINE_BLOCKS(16),
        .HAPTIC_LIFECYCLE_GUARD_ENABLE(1),
        .DIGITAL_BRINGUP_MODE(1),.DIGITAL_MATRIX_MODE(DIGITAL_MATRIX_MODE),
        .DIGITAL_TARGET_MASK(DIGITAL_TARGET_MASK)
    ) u_unified (
        .clk(pl_clk0),.rst_n(core_resetn),.bus_resetn_i(pl_bus_resetn),
        .haptic_resetn_i(haptic_resetn_registered),.iic_issue_enable_i(guard_issue_enable&&!diag_owner),
        .iic_busy_o(iic_busy),.iic_cmd_start_o(iic_cmd_start),
        .runtime_permit_i(runtime_permit),.recovery_request_i(recovery_sync[1]),
        .s_awaddr(mail_awaddr[11:0]),
        .s_awvalid(mail_awvalid),
        .s_awready(mail_awready),
        .s_wdata(mail_wdata),
        .s_wstrb(mail_wstrb),
        .s_wvalid(mail_wvalid),
        .s_wready(mail_wready),
        .s_bresp(mail_bresp),
        .s_bvalid(mail_bvalid),
        .s_bready(mail_bready),
        .s_araddr(mail_araddr[11:0]),
        .s_arvalid(mail_arvalid),
        .s_arready(mail_arready),
        .s_rdata(mail_rdata),
        .s_rresp(mail_rresp),
        .s_rvalid(mail_rvalid),
        .s_rready(mail_rready),
        .sts_awaddr(sts_awaddr[9:0]),
        .sts_awvalid(sts_awvalid),
        .sts_awready(sts_awready),
        .sts_wdata(sts_wdata),
        .sts_wstrb(sts_wstrb),
        .sts_wvalid(sts_wvalid),
        .sts_wready(sts_wready),
        .sts_bresp(sts_bresp),
        .sts_bvalid(sts_bvalid),
        .sts_bready(sts_bready),
        .sts_araddr(sts_araddr[9:0]),
        .sts_arvalid(sts_arvalid),
        .sts_arready(sts_arready),
        .sts_rdata(sts_rdata),
        .sts_rresp(sts_rresp),
        .sts_rvalid(sts_rvalid),
        .sts_rready(sts_rready),
        .scl_i(scl_sync),.sda_i(sda_sync),
        .raw_scl_drive_low_o(raw_scl_low),.raw_sda_drive_low_o(raw_sda_low),
        .scl_drive_low_o(core_scl_low),.sda_drive_low_o(core_sda_low),.oe_disable_o(oe_disable),
        .force_off_o(force_off),.control_valid_o(control_valid),.active_valid_o(active_valid),
        .effective_unknown_o(effective_unknown),.observation_gen_o(observation_gen),
        .haptic_active_gen_o(haptic_active_gen),.control_commit_pulse_o(control_commit),
        .control_session_o(control_session),.control_seq_o(control_seq),.fault_reason_o(fault_reason),
        .init_healthy_o(init_healthy),.fault_latched_o(fault_latched),.online_mask_o(online_mask),
        .pipeline_quiescent_o(pipeline_quiescent),.frame_count_o(frame_count),
        .first_failure_info_o(first_failure_info),
        .init_tca_failure_mask_o(init_tca_failure_mask),.init_pca_failure_mask_o(init_pca_failure_mask)
    );


    wire [63:0] window_elapsed;
    wire [31:0] scl_rises, scl_falls, sda_rises, sda_falls;
    wire [31:0] raw_scl_transitions, raw_sda_transitions;
    signal_window_monitor #(.WINDOW_CYCLES(SIGNAL_WINDOW_CYCLES)) u_signal_window (
        .clk(pl_clk0),.rst_n(pl_bus_resetn),.arm_request_async(arm_request),
        .scl_sense(scl_sync),.sda_sense(sda_sync),
        .raw_scl_low(raw_scl_low),.raw_sda_low(raw_sda_low),
        .window_active(window_active),.expired(window_expired),
        .arm_synced_o(arm_synced),.start_pulse_o(window_start),
        .elapsed_cycles(window_elapsed),
        .scl_rise_count(scl_rises),.scl_fall_count(scl_falls),
        .sda_rise_count(sda_rises),.sda_fall_count(sda_falls),
        .raw_scl_transition_count(raw_scl_transitions),
        .raw_sda_transition_count(raw_sda_transitions)
    );
    // Channel1 (+0) is paged input; channel2 DATA/TRI remain at +8/+C.
    // Selectors must themselves be configured output; input/TRI1 means zero.
    wire [3:0] measure_page=diag_ctrl_o[3:0] & ~diag_ctrl_t[3:0];
    always @* begin
        case (measure_page)
            4'd0: diag_measure_i=DIGITAL_MATRIX_MODE?32'h52364d32:32'h52364431; // R6M2 / original R6D1
            4'd1: diag_measure_i=scl_rises;
            4'd2: diag_measure_i=scl_falls;
            4'd3: diag_measure_i=sda_rises;
            4'd4: diag_measure_i=sda_falls;
            // [0]active [1]expired [2]arm_sync [3]core_resetn
            // [4]base_resetn [5]raw_arm [6]guard_pad_enable [7]control_valid [8]force_off
            4'd5: diag_measure_i={23'b0,force_off,control_valid,guard_pad_enable,
                                 arm_request,base_core_resetn,core_resetn,
                                 arm_synced,window_expired,window_active};
            4'd6: diag_measure_i=window_elapsed[31:0];
            4'd7: diag_measure_i=window_elapsed[63:32];
            4'd8: diag_measure_i=raw_scl_transitions;
            4'd9: diag_measure_i=raw_sda_transitions;
            4'd10: diag_measure_i=first_failure_info;
            4'd11: diag_measure_i={16'b0,init_tca_failure_mask};
            4'd12: diag_measure_i={16'b0,init_pca_failure_mask};
            // [0]request after TRI, [1]2FF synchronized request, [2]runtime permit.
            4'd13: diag_measure_i={29'b0,runtime_permit,operator_control_arm,operator_control_request};
            4'd14: diag_measure_i=guard_status;
            // Page15: [0]owner,[1]request,[2]diag_busy,[3]SCLpull,[4]SDApull,
            // [5]business resetn,[6]guard issue,[7]pad enable. Upper bits zero.
            4'd15: diag_measure_i={24'b0,guard_pad_enable,guard_issue_enable,
                haptic_resetn_registered,diag_sda_low,diag_scl_low,diag_busy,
                diag_owner_request,diag_owner};
            default: diag_measure_i=32'hbad00000 | {28'b0,measure_page};
        endcase
    end

    // 状态低6位沿用原复位观测口径；高16位是PL时钟活动计数。
    // [0]PS原始resetn [1]同步resetn [2]startup [3]常1 [4]原bit31覆盖 [5]core resetn
    // [6]SCL [7]SDA [8]recovery请求 [9]init健康 [10]fault [11]force_off
    // [12]control_valid [13]active_valid [14]全管线空闲 [15]OE禁止（仅内部信号）
    assign diag_status_i = {diag_ticks[31:16],oe_disable,pipeline_quiescent,
        active_valid,control_valid,force_off,fault_latched,init_healthy,recovery_sync[1],
        sda_sync,scl_sync,core_resetn,reset_override,1'b1,pl_bus_resetn,
        ps_reset_sync[1],pl_resetn0};
    // 不猜测OE或外部watchdog针位：本顶层只有两根已确认IIC管脚。
    // oe_disable=1是内部约束，不代表实体PCA的OE已连接或马达已物理关闭。
endmodule
