// AXI4-Lite interface wrapper around kv260_perception_top.
//
// The Block Design needs a named AXI4-Lite interface (s_axi) rather than loose
// ports. This wrapper presents the standard interface and re-exposes the status
// and inhibit signals as plain ports for BD-side observation.
//
// Safety defaults are unchanged: SIM_CONTROL_ENABLE=0 keeps control inhibited,
// and the legacy sideband is driven from BD constants (permit=0).
`timescale 1ns/1ps

module kv260_perception_axi_wrap #(
    parameter integer TICKS_PER_US       = 100,
    parameter integer FRESH_US           = 250000,
    parameter integer FUTURE_US          = 40000,
    parameter integer SIM_CONTROL_ENABLE = 0
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 clk CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF s_axi, ASSOCIATED_RESET rst_n" *)
    input  wire        clk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 rst_n RST" *)
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input  wire        rst_n,

    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWADDR" *)
    input  wire [11:0] s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWVALID" *)
    input  wire        s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWREADY" *)
    output wire        s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WDATA" *)
    input  wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WSTRB" *)
    input  wire [3:0]  s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WVALID" *)
    input  wire        s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WREADY" *)
    output wire        s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BRESP" *)
    output wire [1:0]  s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BVALID" *)
    output wire        s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BREADY" *)
    input  wire        s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARADDR" *)
    input  wire [11:0] s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARVALID" *)
    input  wire        s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARREADY" *)
    output wire        s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RDATA" *)
    output wire [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RRESP" *)
    output wire [1:0]  s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RVALID" *)
    output wire        s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RREADY" *)
    input  wire        s_axi_rready,

    // Legacy controller sideband (BD constants in this build)
    input  wire        local_healthy_i,
    input  wire        simulation_permit_i,
    input  wire        sched_busy_i,
    input  wire        dd_busy_i,
    input  wire        init_busy_i,

    // Observation / inhibit outputs
    output wire        active_valid_o,
    output wire [255:0] effective_unknown_o,
    output wire [31:0] observation_gen_o,
    output wire [31:0] fault_reason_o,
    output wire        force_off_o,
    output wire        control_valid_o,
    output wire        control_commit_pulse_o,
    output wire [31:0] control_session_o,
    output wire [63:0] control_seq_o,
    output wire [15:0] legacy_active_gen_o,
    output wire [15:0] legacy_pending_gen_o,
    output wire        legacy_commit_pending_o,
    output wire [2:0]  legacy_commit_status_o,
    output wire        legacy_act_bank_o,
    output wire [7:0]  legacy_scan_data_o
);

    kv260_perception_top #(
        .TICKS_PER_US       (TICKS_PER_US),
        .FRESH_US           (FRESH_US),
        .FUTURE_US          (FUTURE_US),
        .SIM_CONTROL_ENABLE (SIM_CONTROL_ENABLE)
    ) u_top (
        .clk                     (clk),
        .rst_n                   (rst_n),
        .s_awaddr                (s_axi_awaddr),
        .s_awvalid               (s_axi_awvalid),
        .s_awready               (s_axi_awready),
        .s_wdata                 (s_axi_wdata),
        .s_wstrb                 (s_axi_wstrb),
        .s_wvalid                (s_axi_wvalid),
        .s_wready                (s_axi_wready),
        .s_bresp                 (s_axi_bresp),
        .s_bvalid                (s_axi_bvalid),
        .s_bready                (s_axi_bready),
        .s_araddr                (s_axi_araddr),
        .s_arvalid               (s_axi_arvalid),
        .s_arready               (s_axi_arready),
        .s_rdata                 (s_axi_rdata),
        .s_rresp                 (s_axi_rresp),
        .s_rvalid                (s_axi_rvalid),
        .s_rready                (s_axi_rready),
        .local_healthy_i         (local_healthy_i),
        .simulation_permit_i     (simulation_permit_i),
        .sched_busy_i            (sched_busy_i),
        .dd_busy_i               (dd_busy_i),
        .init_busy_i             (init_busy_i),
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
