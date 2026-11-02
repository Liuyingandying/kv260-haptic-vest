// Independent recovery diagnostic: original payload RTL unchanged; internal reset override only.
// KV260 K26 board-level top: PS Block Design + perception adapter.
//
// This is the integration layer. The Block Design (kv260_ps_bd) provides the
// Zynq UltraScale+ MPSoC PS, a SmartConnect, and exports:
//   - M_AXI_PERCEPTION : AXI4-Lite master (PS -> PL), 32-bit address
//   - pl_clk0          : PS PL0 clock (96.968727 MHz)
//   - pl_resetn0       : PS PL reset, active low
//
// This module instantiates the BD wrapper and wires those exports to the
// perception adapter, which owns the legacy matrix buffer write port.
//
// This independent bring-up copy adds exactly two guarded open-drain IIC
// package signals. The matrix controller's outputs remain internal.
//
// Safety defaults (must not be relaxed):
//   SIM_CONTROL_ENABLE = 0  -> control_valid_o can never assert.
//   simulation_permit_i = 0 -> the adapter's control path stays inhibited.
//   IIC pad drive requires an explicit, time-limited diagnostic bus lease.
//   There is no automatic PCA initialization or actuator output path.
`timescale 1ns/1ps

module kv260_i2c_bringup_top #(
    parameter integer SIM_CONTROL_ENABLE = 0
)(inout wire i2c_scl_io, inout wire i2c_sda_io);

    // ---- Block Design: PS + SmartConnect ----
    wire        pl_clk0;
    wire        pl_resetn0;
    wire cfg_osc_raw, cfg_mon_clk, cfg_eos;
    (* ASYNC_REG="TRUE" *) reg [1:0] cfg_hb_sync=0, cfg_eos_sync=0;


    // The BD exports a 32-bit AXI address (SmartConnect widens the 12-bit
    // slave address). The adapter decodes only the low 12 bits, which is
    // correct because the PS maps the 64K window at 0x9F000000.
    wire [31:0] m_axil_awaddr_full;
    wire [31:0] m_axil_araddr_full;

    wire [11:0] m_axil_awaddr = m_axil_awaddr_full[11:0];
    wire [11:0] m_axil_araddr = m_axil_araddr_full[11:0];
    wire        m_axil_awvalid;
    wire        m_axil_awready;
    wire [31:0] m_axil_wdata;
    wire [3:0]  m_axil_wstrb;
    wire        m_axil_wvalid;
    wire        m_axil_wready;
    wire [1:0]  m_axil_bresp;
    wire        m_axil_bvalid;
    wire        m_axil_bready;
    wire        m_axil_arvalid;
    wire        m_axil_arready;
    wire [31:0] m_axil_rdata;
    wire [1:0]  m_axil_rresp;
    wire        m_axil_rvalid;
    wire        m_axil_rready;

    // Diagnostic-only control plane; no external package signals.
    reg [15:0] diag_startup = 16'b0;
    reg [31:0] diag_ticks = 32'b0;
    (* ASYNC_REG = "TRUE" *) reg [1:0] ps_reset_sync = 2'b0;
    always @(posedge pl_clk0) begin
        diag_startup <= {diag_startup[14:0],1'b1};
        diag_ticks <= diag_ticks + 1'b1;
        ps_reset_sync <= {ps_reset_sync[0],pl_resetn0};
    end
    wire diag_bus_resetn = &diag_startup;
    wire [31:0] diag_ctrl_o, diag_ctrl_t;
    wire iic_scl_i, iic_scl_o, iic_scl_t, iic_sda_i, iic_sda_o, iic_sda_t;
    // Phase 2E: haptic I2C master owns the pads
    wire haptic_scl, haptic_sda;
    wire iic_gate_request = diag_ctrl_o[30] & ~diag_ctrl_t[30];
    wire iic_guard_active, iic_guard_expired, scl_low, sda_low;
    (* ASYNC_REG="TRUE" *) reg [1:0] scl_pad_sync=0, sda_pad_sync=0;
    (* ASYNC_REG="TRUE" *) reg [1:0] guard_active_sync=0, guard_expired_sync=0;
    always @(posedge pl_clk0) begin
        scl_pad_sync <= {scl_pad_sync[0],iic_scl_i};
        sda_pad_sync <= {sda_pad_sync[0],iic_sda_i};
        guard_active_sync <= {guard_active_sync[0],iic_guard_active};
        guard_expired_sync <= {guard_expired_sync[0],iic_guard_expired};
    end
    i2c_open_drain_guard u_iic_guard (
        .cfg_clk(cfg_mon_clk),.rst_n(cfg_eos_sync[1]),.request_async(iic_gate_request),
        .scl_o(iic_scl_o),.scl_t(iic_scl_t),.sda_o(iic_sda_o),.sda_t(iic_sda_t),
        .scl_drive_low(scl_low),.sda_drive_low(sda_low),
        .active(iic_guard_active),.expired(iic_guard_expired)
    );
    // Phase 2E: baseline AXI-IIC IOBUFs REMOVED. The frozen haptic master's
    // open-drain IOBUF is now the S O L E driver of H12/B10 (single driver).
    // Pad readback is retained only for the diagnostic status register.
    assign iic_scl_i = i2c_scl_io;
    assign iic_sda_i = i2c_sda_io;

    // Phase 2E: bidirectional hand-off. The frozen haptic top already contains
    // the open-drain IOBUF; tran just connects it to the package pads.
    tran (i2c_scl_io, haptic_scl);
    tran (i2c_sda_io, haptic_sda);
    wire diag_reset_override = diag_ctrl_o[31] & ~diag_ctrl_t[31];
    wire payload_resetn = diag_bus_resetn & (ps_reset_sync[1] | diag_reset_override);
    // status[0]=raw PS resetn, [1]=synchronized PS resetn, [2]=bus resetn,
    // [3]=constant1, [4]=override active, [5]=actual payload resetn.
    wire [31:0] diag_status = {diag_ticks[21:0],guard_expired_sync[1],guard_active_sync[1],sda_pad_sync[1],scl_pad_sync[1],payload_resetn,diag_reset_override,
                              1'b1,diag_bus_resetn,ps_reset_sync[1],pl_resetn0};
    wire [39:0] diag_ps_araddr;
    wire diag_ps_arvalid;
    wire diag_ps_arready;
    wire diag_ps_rvalid;
    wire diag_ps_rready;
    wire diag_ps_awvalid;
    wire diag_ps_awready;
    wire diag_ps_wvalid;
    wire diag_ps_wready;
    wire diag_ps_bvalid;
    wire diag_ps_bready;
    wire diag_gpio_arvalid;
    wire diag_gpio_arready;
    wire diag_gpio_rvalid;
    wire diag_gpio_rready;
    wire diag_gpio_awvalid;
    wire diag_gpio_awready;
    wire diag_gpio_wvalid;
    wire diag_gpio_wready;
    wire diag_gpio_bvalid;
    wire diag_gpio_bready;
    wire [9:0] diag_ps_hs = {diag_ps_arvalid,diag_ps_arready,diag_ps_rvalid,diag_ps_rready,diag_ps_awvalid,diag_ps_awready,diag_ps_wvalid,diag_ps_wready,diag_ps_bvalid,diag_ps_bready};
    wire [9:0] diag_gpio_hs = {diag_gpio_arvalid,diag_gpio_arready,diag_gpio_rvalid,diag_gpio_rready,diag_gpio_awvalid,diag_gpio_awready,diag_gpio_wvalid,diag_gpio_wready,diag_gpio_bvalid,diag_gpio_bready};
    wire [9:0] diag_custom_hs = {m_axil_arvalid,m_axil_arready,m_axil_rvalid,m_axil_rready,m_axil_awvalid,m_axil_awready,m_axil_wvalid,m_axil_wready,m_axil_bvalid,m_axil_bready};
    diag_ila u_diag_ila (
        .clk(pl_clk0), .probe0(diag_status), .probe1(diag_ps_hs),
        .probe2(diag_gpio_hs), .probe3(diag_custom_hs),
        .probe4(m_axil_araddr_full), .probe5(m_axil_rdata),
        .probe6(diag_ps_araddr), .probe7(m_axil_rresp)
    );

    kv260_ps_bd_wrapper u_ps_bd (
        .iic_scl_i(iic_scl_i),.iic_scl_o(iic_scl_o),.iic_scl_t(iic_scl_t),
        .iic_sda_i(iic_sda_i),.iic_sda_o(iic_sda_o),.iic_sda_t(iic_sda_t),
        .haptic_scl_0(haptic_scl), .haptic_sda_0(haptic_sda),
        .diag_bus_resetn(diag_bus_resetn),
        .diag_status_i(diag_status), .diag_ctrl_o(diag_ctrl_o), .diag_ctrl_t(diag_ctrl_t),
        .diag_ps_araddr(diag_ps_araddr),
        .diag_ps_arvalid(diag_ps_arvalid),
        .diag_ps_arready(diag_ps_arready),
        .diag_ps_rvalid(diag_ps_rvalid),
        .diag_ps_rready(diag_ps_rready),
        .diag_ps_awvalid(diag_ps_awvalid),
        .diag_ps_awready(diag_ps_awready),
        .diag_ps_wvalid(diag_ps_wvalid),
        .diag_ps_wready(diag_ps_wready),
        .diag_ps_bvalid(diag_ps_bvalid),
        .diag_ps_bready(diag_ps_bready),
        .diag_gpio_arvalid(diag_gpio_arvalid),
        .diag_gpio_arready(diag_gpio_arready),
        .diag_gpio_rvalid(diag_gpio_rvalid),
        .diag_gpio_rready(diag_gpio_rready),
        .diag_gpio_awvalid(diag_gpio_awvalid),
        .diag_gpio_awready(diag_gpio_awready),
        .diag_gpio_wvalid(diag_gpio_wvalid),
        .diag_gpio_wready(diag_gpio_wready),
        .diag_gpio_bvalid(diag_gpio_bvalid),
        .diag_gpio_bready(diag_gpio_bready),

        .M_AXI_PERCEPTION_awaddr  (m_axil_awaddr_full),
        .M_AXI_PERCEPTION_awprot  (),
        .M_AXI_PERCEPTION_awvalid (m_axil_awvalid),
        .M_AXI_PERCEPTION_awready (m_axil_awready),
        .M_AXI_PERCEPTION_wdata   (m_axil_wdata),
        .M_AXI_PERCEPTION_wstrb   (m_axil_wstrb),
        .M_AXI_PERCEPTION_wvalid  (m_axil_wvalid),
        .M_AXI_PERCEPTION_wready  (m_axil_wready),
        .M_AXI_PERCEPTION_bresp   (m_axil_bresp),
        .M_AXI_PERCEPTION_bvalid  (m_axil_bvalid),
        .M_AXI_PERCEPTION_bready  (m_axil_bready),
        .M_AXI_PERCEPTION_araddr  (m_axil_araddr_full),
        .M_AXI_PERCEPTION_arprot  (),
        .M_AXI_PERCEPTION_arvalid (m_axil_arvalid),
        .M_AXI_PERCEPTION_arready (m_axil_arready),
        .M_AXI_PERCEPTION_rdata   (m_axil_rdata),
        .M_AXI_PERCEPTION_rresp   (m_axil_rresp),
        .M_AXI_PERCEPTION_rvalid  (m_axil_rvalid),
        .M_AXI_PERCEPTION_rready  (m_axil_rready),
        .pl_clk0                  (pl_clk0),
        .pl_resetn0               (pl_resetn0)
    );

    // Independent, observation-only clock. Never drives the payload or external pins.
    STARTUPE3 #(.PROG_USR("FALSE"),.SIM_CCLK_FREQ(0.0)) u_diag_startup (
        .CFGMCLK(cfg_osc_raw), .CFGCLK(), .EOS(cfg_eos), .DI(), .PREQ(),
        .DO(4'b0), .DTS(4'b1111), .FCSBO(1'b1), .FCSBTS(1'b1),
        .GSR(1'b0), .GTS(1'b0), .KEYCLEARB(1'b1), .PACK(1'b0),
        .USRCCLKO(1'b0), .USRCCLKTS(1'b1), .USRDONEO(1'b1), .USRDONETS(1'b1)
    );
    (* DONT_TOUCH="TRUE" *) BUFG u_cfg_mon_bufg (.I(cfg_osc_raw),.O(cfg_mon_clk));
    (* ASYNC_REG="TRUE" *) reg [1:0] cfg_ps_reset_sync=0, cfg_bus_reset_sync=0;
    (* ASYNC_REG="TRUE" *) reg [1:0] cfg_payload_reset_sync=0, cfg_override_sync=0;
    reg [15:0] cfg_ticks=0, cfg_pl_edges=0;
    reg cfg_hb_last=0;
    always @(posedge cfg_mon_clk) begin
        cfg_hb_sync <= {cfg_hb_sync[0],diag_ticks[4]};
        cfg_eos_sync <= {cfg_eos_sync[0],cfg_eos};
        cfg_ps_reset_sync <= {cfg_ps_reset_sync[0],pl_resetn0};
        cfg_bus_reset_sync <= {cfg_bus_reset_sync[0],diag_bus_resetn};
        cfg_payload_reset_sync <= {cfg_payload_reset_sync[0],payload_resetn};
        cfg_override_sync <= {cfg_override_sync[0],diag_reset_override};
        cfg_ticks <= cfg_ticks+1'b1;
        cfg_hb_last <= cfg_hb_sync[1];
        if (cfg_hb_sync[1]!=cfg_hb_last) cfg_pl_edges <= cfg_pl_edges+1'b1;
    end
    // [31:16]=identity D1A6; [7:2]=override,payload resetn,bus resetn,PS resetn,EOS,PL0 heartbeat.
    wire [31:0] cfg_status={16'hD1A6,8'b0,cfg_override_sync[1],cfg_payload_reset_sync[1],
                            cfg_bus_reset_sync[1],cfg_ps_reset_sync[1],cfg_eos_sync[1],cfg_hb_sync[1],2'b0};
    diag_independent_ila u_independent_ila (.clk(cfg_mon_clk),.probe0(cfg_status),
                                          .probe1({cfg_ticks,cfg_pl_edges}));

    // ---- Perception adapter: the actual PL payload ----
    // Diagnostic outputs stay internal. MARK_DEBUG keeps them visible to an
    // ILA without consuming package pins.
    (* MARK_DEBUG = "true" *) wire        active_valid_o;
    (* MARK_DEBUG = "true" *) wire [255:0] effective_unknown_o;
    (* MARK_DEBUG = "true" *) wire [31:0] observation_gen_o;
    (* MARK_DEBUG = "true" *) wire [31:0] fault_reason_o;
    (* MARK_DEBUG = "true" *) wire        force_off_o;
    (* MARK_DEBUG = "true" *) wire        control_valid_o;
    (* MARK_DEBUG = "true" *) wire        control_commit_pulse_o;
    (* MARK_DEBUG = "true" *) wire [31:0] control_session_o;
    (* MARK_DEBUG = "true" *) wire [63:0] control_seq_o;
    (* MARK_DEBUG = "true" *) wire [15:0] legacy_active_gen_o;
    (* MARK_DEBUG = "true" *) wire [15:0] legacy_pending_gen_o;
    (* MARK_DEBUG = "true" *) wire        legacy_commit_pending_o;
    (* MARK_DEBUG = "true" *) wire [2:0]  legacy_commit_status_o;
    (* MARK_DEBUG = "true" *) wire        legacy_act_bank_o;
    (* MARK_DEBUG = "true" *) wire [7:0]  legacy_scan_data_o;

    kv260_perception_axi_wrap #(
        .TICKS_PER_US       (100),
        .FRESH_US           (250000),
        .FUTURE_US          (40000),
        .SIM_CONTROL_ENABLE (SIM_CONTROL_ENABLE)
    ) u_perception (
        .clk                     (pl_clk0),
        .rst_n                   (payload_resetn),
        .s_axi_awaddr            (m_axil_awaddr),
        .s_axi_awvalid           (m_axil_awvalid),
        .s_axi_awready           (m_axil_awready),
        .s_axi_wdata             (m_axil_wdata),
        .s_axi_wstrb             (m_axil_wstrb),
        .s_axi_wvalid            (m_axil_wvalid),
        .s_axi_wready            (m_axil_wready),
        .s_axi_bresp             (m_axil_bresp),
        .s_axi_bvalid            (m_axil_bvalid),
        .s_axi_bready            (m_axil_bready),
        .s_axi_araddr            (m_axil_araddr),
        .s_axi_arvalid           (m_axil_arvalid),
        .s_axi_arready           (m_axil_arready),
        .s_axi_rdata             (m_axil_rdata),
        .s_axi_rresp             (m_axil_rresp),
        .s_axi_rvalid            (m_axil_rvalid),
        .s_axi_rready            (m_axil_rready),
        // No legacy controller exists in this build: report healthy, but
        // permanently withhold the simulation permit so control cannot assert.
        .local_healthy_i         (1'b1),
        .simulation_permit_i     (1'b0),
        .sched_busy_i            (1'b0),
        .dd_busy_i               (1'b0),
        .init_busy_i             (1'b0),
        .active_valid_o          (active_valid_o),
        .effective_unknown_o     (effective_unknown_o),
        .observation_gen_o       (observation_gen_o),
        .fault_reason_o          (fault_reason_o),
        .force_off_o             (force_off_o),
        .control_valid_o         (control_valid_o),
        .control_commit_pulse_o  (control_commit_pulse_o),
        .control_session_o       (control_session_o),
        .control_seq_o           (control_seq_o),
        .legacy_active_gen_o     (legacy_active_gen_o),
        .legacy_pending_gen_o    (legacy_pending_gen_o),
        .legacy_commit_pending_o (legacy_commit_pending_o),
        .legacy_commit_status_o  (legacy_commit_status_o),
        .legacy_act_bank_o       (legacy_act_bank_o),
        .legacy_scan_data_o      (legacy_scan_data_o)
    );

endmodule
