// Independent OFFLINE integration candidate; NOT an accepted board image.
// One writer owns the haptic matrix: the unchanged KPF1 adapter below.
// Physical pins are disabled by default and OE remains disabled unconditionally.
// SIM_CONTROL_ENABLE is a DIGITAL SIMULATION permission, never physical safety.
`timescale 1ns/1ps
module unified_perception_haptic #(
    parameter integer CLK_FREQ_HZ = 100_000_000,
    parameter integer TICKS_PER_US = 100,
    parameter integer FRESH_US = 250000,
    parameter integer FUTURE_US = 40000,
    parameter integer I2C_FREQ_HZ = 100_000,
    // A 66-byte 100 kHz transfer alone requires at least 5.94 ms.
    // This transaction deadline does not change the 250 ms KPF1 frame lifetime.
    parameter integer I2C_TIMEOUT_US = 20000,
    parameter integer FAULT_RETRY_MS = 10,
    parameter integer SIM_CONTROL_ENABLE = 0,
    parameter integer ACTUATOR_PINS_ENABLE = 0,
    parameter integer MIN_ONLINE_BLOCKS = 16
)(
    input wire clk, input wire rst_n,
    input wire simulation_permit_i,
    // Synchronous one-request interface. Assert only after digital FULL_OFF
    // draining and HOLD have been observed. A rising edge requests the legacy
    // FSM's complete reinitialization; it grants no perception/output permit.
    input wire recovery_request_i,

    // KPF1 mailbox AXI4-Lite interface, unchanged from the frozen adapter.
    input wire [11:0] s_awaddr, input wire s_awvalid, output wire s_awready,
    input wire [31:0] s_wdata, input wire [3:0] s_wstrb,
    input wire s_wvalid, output wire s_wready,
    output wire [1:0] s_bresp, output wire s_bvalid, input wire s_bready,
    input wire [11:0] s_araddr, input wire s_arvalid, output wire s_arready,
    output wire [31:0] s_rdata, output wire [1:0] s_rresp,
    output wire s_rvalid, input wire s_rready,

    // Existing status bank, READ ONLY in this candidate. AXI writes receive
    // SLVERR after independently accepting AW and W, never an unbounded stall.
    // There is NO raw 256-byte matrix write path bypassing KPF1 quality/UNKNOWN.
    input wire [9:0] sts_awaddr, input wire sts_awvalid, output wire sts_awready,
    input wire [31:0] sts_wdata, input wire [3:0] sts_wstrb,
    input wire sts_wvalid, output wire sts_wready,
    output wire [1:0] sts_bresp, output wire sts_bvalid, input wire sts_bready,
    input wire [9:0] sts_araddr, input wire sts_arvalid, output wire sts_arready,
    output wire [31:0] sts_rdata, output wire [1:0] sts_rresp,
    output wire sts_rvalid, input wire sts_rready,

    input wire scl_i, input wire sda_i,
    // Raw intent is for an offline open-drain slave model / internal debug.
    output wire raw_scl_drive_low_o, output wire raw_sda_drive_low_o,
    // Board-level outputs stay released with the default configuration.
    output wire scl_drive_low_o, output wire sda_drive_low_o,
    output wire oe_disable_o,

    output wire force_off_o, output wire control_valid_o,
    output wire active_valid_o, output wire [255:0] effective_unknown_o,
    output wire [31:0] observation_gen_o,
    output wire [15:0] haptic_active_gen_o,
    output wire control_commit_pulse_o,
    output wire [31:0] control_session_o, output wire [63:0] control_seq_o,
    output wire [31:0] fault_reason_o,
    output wire init_healthy_o, output wire fault_latched_o,
    output wire [15:0] online_mask_o,
    output wire pipeline_quiescent_o,
    output wire [7:0] frame_count_o
);
    wire [9:0] m_awaddr;
    wire m_awvalid, m_awready;
    wire [31:0] m_wdata;
    wire [3:0] m_wstrb;
    wire m_wvalid, m_wready;
    wire [1:0] m_bresp;
    wire m_bvalid, m_bready;
    wire legacy_pending, legacy_grant, legacy_discard;
    wire dd_busy, init_busy, runtime_run, haptic_force_off;
    wire adapter_force_off, adapter_control_valid;
    wire [31:0] i2c_errors;
    reg recovery_request_q;
    always @(posedge clk) begin
        if (!rst_n) recovery_request_q <= 1'b0;
        else recovery_request_q <= (recovery_request_i === 1'b1);
    end
    wire recovery_pulse = (recovery_request_i === 1'b1) && !recovery_request_q;
    reg sts_aw_have, sts_w_have, sts_reject_valid;
    assign sts_awready = rst_n && !sts_aw_have && !sts_reject_valid;
    assign sts_wready = rst_n && !sts_w_have && !sts_reject_valid;
    assign sts_bvalid = sts_reject_valid;
    assign sts_bresp = 2'b10;
    always @(posedge clk) begin
        if (!rst_n) begin
            sts_aw_have <= 0;
            sts_w_have <= 0;
            sts_reject_valid <= 0;
        end else begin
            if (sts_awvalid && sts_awready) sts_aw_have <= 1;
            if (sts_wvalid && sts_wready) sts_w_have <= 1;
            if (sts_aw_have && sts_w_have && !sts_reject_valid)
                sts_reject_valid <= 1;
            if (sts_reject_valid && sts_bready) begin
                sts_reject_valid <= 0;
                sts_aw_have <= 0;
                sts_w_have <= 0;
            end
        end
    end

    // Do NOT include force_off/OE in this health equation: force_off starts
    // asserted until a complete frame has actually been promoted, so that
    // dependency would deadlock the first authorized digital commit.
    wire local_healthy = init_healthy_o && runtime_run && !fault_latched_o;
    // Stable output is the stricter of adapter and legacy inhibit conditions.
    assign force_off_o = adapter_force_off || haptic_force_off;
    assign control_valid_o = adapter_control_valid && !haptic_force_off;
    assign scl_drive_low_o = (ACTUATOR_PINS_ENABLE != 0) && raw_scl_drive_low_o;
    assign sda_drive_low_o = (ACTUATOR_PINS_ENABLE != 0) && raw_sda_drive_low_o;
    // No physical output authorization path exists in this offline candidate.
    assign oe_disable_o = 1'b1;

    perception_axi_adapter #(
        .TICKS_PER_US(TICKS_PER_US), .FRESH_US(FRESH_US),
        .FUTURE_US(FUTURE_US), .SIM_CONTROL_ENABLE(SIM_CONTROL_ENABLE)
    ) u_adapter (
        .clk(clk), .rst_n(rst_n),
        .s_awaddr(s_awaddr), .s_awvalid(s_awvalid), .s_awready(s_awready),
        .s_wdata(s_wdata), .s_wstrb(s_wstrb), .s_wvalid(s_wvalid), .s_wready(s_wready),
        .s_bresp(s_bresp), .s_bvalid(s_bvalid), .s_bready(s_bready),
        .s_araddr(s_araddr), .s_arvalid(s_arvalid), .s_arready(s_arready),
        .s_rdata(s_rdata), .s_rresp(s_rresp), .s_rvalid(s_rvalid), .s_rready(s_rready),
        .m_awaddr(m_awaddr), .m_awvalid(m_awvalid), .m_awready(m_awready),
        .m_wdata(m_wdata), .m_wstrb(m_wstrb), .m_wvalid(m_wvalid), .m_wready(m_wready),
        .m_bresp(m_bresp), .m_bvalid(m_bvalid), .m_bready(m_bready),
        .legacy_commit_pending_i(legacy_pending),
        .legacy_active_gen_i(haptic_active_gen_o),
        .local_healthy_i(local_healthy), .simulation_permit_i(simulation_permit_i),
        // The whole downstream path must finish, including the last IIC STOP.
        .sched_busy_i(!pipeline_quiescent_o), .dd_busy_i(dd_busy), .init_busy_i(init_busy),
        .legacy_grant_o(legacy_grant), .legacy_discard_o(legacy_discard),
        .force_off_o(adapter_force_off), .control_valid_o(adapter_control_valid),
        .active_valid_o(active_valid_o), .effective_unknown_o(effective_unknown_o),
        .observation_gen_o(observation_gen_o),
        .control_session_o(control_session_o), .control_seq_o(control_seq_o),
        .fault_reason_o(fault_reason_o), .control_commit_pulse_o(control_commit_pulse_o)
    );

    kv260_haptic_pipeline #(
        .CLK_FREQ_HZ(CLK_FREQ_HZ), .I2C_FREQ_HZ(I2C_FREQ_HZ),
        .I2C_TIMEOUT_US(I2C_TIMEOUT_US), .FAULT_RETRY_MS(FAULT_RETRY_MS),
        .UNIFIED_BRIDGE_MODE(1), .MIN_ONLINE_BLOCKS(MIN_ONLINE_BLOCKS)
    ) u_haptic (
        .s_axi_aclk(clk), .s_axi_aresetn(rst_n),
        .s_axi_ctrl_awaddr(m_awaddr), .s_axi_ctrl_awvalid(m_awvalid), .s_axi_ctrl_awready(m_awready),
        .s_axi_ctrl_wdata(m_wdata), .s_axi_ctrl_wstrb(m_wstrb),
        .s_axi_ctrl_wvalid(m_wvalid), .s_axi_ctrl_wready(m_wready),
        .s_axi_ctrl_bresp(m_bresp), .s_axi_ctrl_bvalid(m_bvalid), .s_axi_ctrl_bready(m_bready),
        .s_axi_ctrl_araddr(10'b0), .s_axi_ctrl_arvalid(1'b0), .s_axi_ctrl_arready(),
        .s_axi_ctrl_rdata(), .s_axi_ctrl_rresp(), .s_axi_ctrl_rvalid(), .s_axi_ctrl_rready(1'b0),

        .s_axi_sts_awaddr(10'b0), .s_axi_sts_awvalid(1'b0), .s_axi_sts_awready(),
        .s_axi_sts_wdata(32'b0), .s_axi_sts_wvalid(1'b0),
        .s_axi_sts_wready(), .s_axi_sts_bresp(),
        .s_axi_sts_bvalid(), .s_axi_sts_bready(1'b1),
        .s_axi_sts_araddr(sts_araddr), .s_axi_sts_arvalid(sts_arvalid), .s_axi_sts_arready(sts_arready),
        .s_axi_sts_rdata(sts_rdata), .s_axi_sts_rresp(sts_rresp),
        .s_axi_sts_rvalid(sts_rvalid), .s_axi_sts_rready(sts_rready),
        .dbg_frame_cnt(frame_count_o), .dbg_i2c_ack_err(), .dbg_block_ready(),
        .dd_bypass_i(1'b0),
        .i2c_scl_drv_o(raw_scl_drive_low_o), .i2c_sda_drv_o(raw_sda_drive_low_o),
        .i2c_scl_i_i(scl_i), .i2c_sda_i_i(sda_i),
        .o_init_healthy(init_healthy_o), .o_fault_latched(fault_latched_o),
        .o_force_off(haptic_force_off), .o_runtime_run(runtime_run),
        .o_online_mask(online_mask_o), .o_i2c_error_count(i2c_errors),
        .bridge_force_off_i(adapter_force_off), .bridge_grant_i(legacy_grant),
        .bridge_discard_i(legacy_discard), .bridge_commit_i(control_commit_pulse_o),
        .bridge_recovery_i(recovery_pulse),
        .bridge_pending_o(legacy_pending), .bridge_active_gen_o(haptic_active_gen_o),
        .bridge_quiescent_o(pipeline_quiescent_o), .bridge_dd_busy_o(dd_busy),
        .bridge_init_busy_o(init_busy)
    );
endmodule
