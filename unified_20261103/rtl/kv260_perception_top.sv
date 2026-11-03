// KV260 K26 PS/PL integration top: perception mailbox -> legacy tactile matrix buffer.
//
// Scope: no-actuator loopback verification. The perception adapter owns the
// legacy matrix_buffer_axi write port; the legacy scheduler/I2C/PCA chain is
// NOT instantiated here. force_off_o / control_valid_o are exposed as top-level
// outputs so a future integration can wire them into the real inhibit chain.
//
// Safety defaults (must not be relaxed):
//   SIM_CONTROL_ENABLE = 0  -> control_valid_o can never assert.
//   rst_n low               -> active_valid_o=0, effective_unknown_o all ones,
//                              legacy_grant_o=0, force_off_o=1.
//
// The legacy buffer's bank RAM is not cleared by reset (matrix_buffer.v:84-111),
// so active_valid_o must stay 0 until a full atomic SEAL has been accepted.
`timescale 1ns/1ps

module kv260_perception_top #(
    parameter integer TICKS_PER_US     = 100,   // 100 MHz AXI clock
    parameter integer FRESH_US         = 250000,
    parameter integer FUTURE_US        = 40000,
    parameter integer SIM_CONTROL_ENABLE = 0    // 0 = control permanently inhibited
) (
    input  wire        clk,          // AXI / control clock, 100 MHz
    input  wire        rst_n,        // active-low, shared by adapter and legacy buffer

    // ---- PS-side AXI4-Lite slave (perception mailbox) ----
    input  wire [11:0] s_awaddr,
    input  wire        s_awvalid,
    output wire        s_awready,
    input  wire [31:0] s_wdata,
    input  wire [3:0]  s_wstrb,
    input  wire        s_wvalid,
    output wire        s_wready,
    output wire [1:0]  s_bresp,
    output wire        s_bvalid,
    input  wire        s_bready,
    input  wire [11:0] s_araddr,
    input  wire        s_arvalid,
    output wire        s_arready,
    output wire [31:0] s_rdata,
    output wire [1:0]  s_rresp,
    output wire        s_rvalid,
    input  wire        s_rready,

    // ---- legacy controller sideband (real signals in a full integration) ----
    input  wire        local_healthy_i,
    input  wire        simulation_permit_i,
    input  wire        sched_busy_i,
    input  wire        dd_busy_i,
    input  wire        init_busy_i,

    // ---- status / inhibit outputs ----
    output wire        active_valid_o,
    output wire [255:0] effective_unknown_o,
    output wire [31:0] observation_gen_o,
    output wire [31:0] fault_reason_o,
    output wire        force_off_o,
    output wire        control_valid_o,
    output wire        control_commit_pulse_o,
    output wire [31:0] control_session_o,
    output wire [63:0] control_seq_o,

    // ---- legacy matrix buffer observation (diagnostic readback) ----
    output wire [15:0] legacy_active_gen_o,
    output wire [15:0] legacy_pending_gen_o,
    output wire        legacy_commit_pending_o,
    output wire [2:0]  legacy_commit_status_o,
    output wire        legacy_act_bank_o,
    output wire [7:0]  legacy_scan_data_o
);

    // Adapter <-> legacy buffer AXI4-Lite master link
    wire [9:0]  m_awaddr;
    wire        m_awvalid, m_awready;
    wire [31:0] m_wdata;
    wire [3:0]  m_wstrb;
    wire        m_wvalid, m_wready;
    wire [1:0]  m_bresp;
    wire        m_bvalid, m_bready;

    wire legacy_grant, legacy_discard;
    wire [15:0] legacy_gen, legacy_pgen;
    wire [2:0]  legacy_cstatus;
    wire        legacy_ack, legacy_en;

    // Diagnostic scan port: tied off here so the adapter owns the write port and
    // the AXI read port stays unused. A real integration must arbitrate these
    // with the scheduler/dirty-detector (review_notes.md item 7).
    wire [7:0]  scan_addr = 8'h00;
    wire [7:0]  scan_data;

    perception_axi_adapter #(
        .TICKS_PER_US       (TICKS_PER_US),
        .FRESH_US           (FRESH_US),
        .FUTURE_US          (FUTURE_US),
        .SIM_CONTROL_ENABLE (SIM_CONTROL_ENABLE)
    ) u_adapter (
        .clk                     (clk),
        .rst_n                   (rst_n),
        .s_awaddr                (s_awaddr),
        .s_awvalid               (s_awvalid),
        .s_awready               (s_awready),
        .s_wdata                 (s_wdata),
        .s_wstrb                 (s_wstrb),
        .s_wvalid                (s_wvalid),
        .s_wready                (s_wready),
        .s_bresp                 (s_bresp),
        .s_bvalid                (s_bvalid),
        .s_bready                (s_bready),
        .s_araddr                (s_araddr),
        .s_arvalid               (s_arvalid),
        .s_arready               (s_arready),
        .s_rdata                 (s_rdata),
        .s_rresp                 (s_rresp),
        .s_rvalid                (s_rvalid),
        .s_rready                (s_rready),
        .m_awaddr                (m_awaddr),
        .m_awvalid               (m_awvalid),
        .m_awready               (m_awready),
        .m_wdata                 (m_wdata),
        .m_wstrb                 (m_wstrb),
        .m_wvalid                (m_wvalid),
        .m_wready                (m_wready),
        .m_bresp                 (m_bresp),
        .m_bvalid                (m_bvalid),
        .m_bready                (m_bready),
        .legacy_commit_pending_i (legacy_commit_pending_o),
        .legacy_active_gen_i     (legacy_gen),
        .local_healthy_i         (local_healthy_i),
        .simulation_permit_i     (simulation_permit_i),
        .sched_busy_i            (sched_busy_i),
        .dd_busy_i               (dd_busy_i),
        .init_busy_i             (init_busy_i),
        .legacy_grant_o          (legacy_grant),
        .legacy_discard_o        (legacy_discard),
        .force_off_o             (force_off_o),
        .control_valid_o         (control_valid_o),
        .active_valid_o          (active_valid_o),
        .effective_unknown_o     (effective_unknown_o),
        .observation_gen_o       (observation_gen_o),
        .control_session_o       (control_session_o),
        .control_seq_o           (control_seq_o),
        .fault_reason_o          (fault_reason_o),
        .control_commit_pulse_o  (control_commit_pulse_o)
    );

    // Legacy matrix buffer: the adapter is the sole writer. The read port is
    // unused here (no scheduler in this no-actuator build).
    matrix_buffer_axi u_legacy (
        .s_axi_aclk        (clk),
        .s_axi_aresetn     (rst_n),
        .s_axi_awaddr      (m_awaddr),
        .s_axi_awvalid     (m_awvalid),
        .s_axi_awready     (m_awready),
        .s_axi_wdata       (m_wdata),
        .s_axi_wstrb       (m_wstrb),
        .s_axi_wvalid      (m_wvalid),
        .s_axi_wready      (m_wready),
        .s_axi_bvalid      (m_bvalid),
        .s_axi_bresp       (m_bresp),
        .s_axi_bready      (m_bready),
        .s_axi_araddr      (10'b0),
        .s_axi_arvalid     (1'b0),
        .s_axi_arready     (),
        .s_axi_rvalid      (),
        .s_axi_rdata       (),
        .s_axi_rresp       (),
        .s_axi_rready      (1'b0),
        .act_bank_o        (legacy_act_bank_o),
        .commit_ack_o      (legacy_ack),
        .enable_o          (legacy_en),
        .scan_rd_addr      (scan_addr),
        .scan_rd_sel       (1'b1),
        .scan_rd_data      (scan_data),
        .commit_gate_en    (1'b1),
        .sched_busy_i      (sched_busy_i),
        .dd_busy_i         (dd_busy_i),
        .init_busy_i       (init_busy_i),
        .fault_latched_i   (!local_healthy_i || legacy_discard),
        .commit_req_o      (legacy_commit_pending_o),
        .active_gen_o      (legacy_gen),
        .pending_gen_o     (legacy_pgen),
        .commit_status_o   (legacy_cstatus),
        .commit_grant_i    (legacy_grant),
        .discard_pending_i (legacy_discard),
        .dd_pend_addr      (8'b0),
        .dd_pend_sel       (1'b0),
        .dd_pend_data      ()
    );

    assign legacy_pending_gen_o   = legacy_pgen;
    assign legacy_commit_status_o = legacy_cstatus;
    assign legacy_scan_data_o     = scan_data;

endmodule
