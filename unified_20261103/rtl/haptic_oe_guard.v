// haptic_oe_guard.v -- KV260 haptic OE# safety controller (NEW for KV260 port)
//
// Solves the AX7020 design gap documented in KV260_HAPTIC_H0_READONLY_AUDIT.md
// and FPGA_P0_FAULT_CHAIN_REPAIR_REPORT.md:34/208:
//   "现有工程未实现或约束由 AX7020 控制的独立 PCA9685 OE / 功率关断通路"
//   "OE# 硬线拉低（常使能），FPGA 侧无需处理"
//
// Safety contract (all six requirements from the porting task):
//   R1 independent OE# output, driven by this block only
//   R2 OE# defaults OFF (oe_n = 1) and stays OFF across reset
//   R3 unconfigured FPGA / reset / abnormal -> output OFF
//      (unconfigured case is covered EXTERNALLY by a pull-up on OE#; see XDC)
//   R4 enable only after init healthy AND I2C fault-free, sustained ARM_DELAY_MS
//   R5 any of {NACK-latched fault, failsafe force_off, not healthy} -> OFF
//      immediately, in the SAME clock domain, without any I2C transaction
//   R6 shutdown is purely combinational from status taps, so a dead/hung I2C bus
//      cannot prevent the disable (it does not wait for a bus transaction)
//
// Electrical intent (pin NOT assigned here -- J2 is still UNKNOWN):
//   OE# pad -> external 3.3 V pull-up (default OFF when FPGA is unconfigured)
//   FPGA drives LOW to enable. Never drive high push-pull into an external pull.
//
// oe_n = 1  => OUTPUT DISABLED  (OE# high, PCA9685 outputs high-Z / off)
// oe_n = 0  => OUTPUT ENABLED   (OE# low)
`timescale 1ns/1ps

module haptic_oe_guard #(
    parameter integer CLK_FREQ_HZ  = 96_968_727,
    parameter integer ARM_DELAY_MS = 100,   // healthy must persist this long before enable
    parameter integer MIN_OFF_MS   = 200    // minimum OFF dwell after any fault
)(
    input  wire        clk,
    input  wire        rst_n,          // active low; async assert, sync release

    // ---- status taps from kv260_haptic_pipeline ----
    input  wire        init_healthy,   // init_done && online_mask==0xFFFF && no init err
    input  wire        fault_latched,  // sticky I2C fault (NACK / protocol)
    input  wire        force_off,      // effective force_off (failsafe | sw | safety)
    input  wire        i2c_busy,       // bus currently active (informational)

    // ---- software enable request (from AXI control register) ----
    input  wire        sw_oe_req,      // 1 = software asks for output enable

    // ---- OE# pad ----
    output reg         oe_n,           // 1 = disabled (safe), 0 = enabled

    // ---- observability ----
    output reg         armed,          // 1 = enable conditions currently satisfied
    output reg  [3:0]  off_reason,     // why oe_n is 1 (sticky until re-armed)
    output reg  [31:0] arm_timer,      // cycles healthy has persisted
    output reg  [31:0] off_dwell       // cycles spent OFF since last fault
);

    localparam integer ARM_CYCLES = (CLK_FREQ_HZ / 1000) * ARM_DELAY_MS;
    localparam integer OFF_CYCLES = (CLK_FREQ_HZ / 1000) * MIN_OFF_MS;

    localparam [3:0] R_NONE      = 4'd0,  // no blocker
                     R_RESET     = 4'd1,  // reset / power-on
                     R_NO_REQ    = 4'd2,  // software has not requested enable
                     R_UNHEALTHY = 4'd3,  // init not healthy
                     R_FAULT     = 4'd4,  // sticky I2C fault latched
                     R_FORCE_OFF = 4'd5,  // failsafe / software full-off
                     R_NOT_ARMED = 4'd6;  // healthy but arm delay not elapsed

    reg rst_sync1, rst_sync2;
    wire rst_n_sync = rst_sync2;

    // Synchronise the active-low reset into this clock domain (async assert).
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin rst_sync1 <= 1'b0; rst_sync2 <= 1'b0; end
        else        begin rst_sync1 <= 1'b1; rst_sync2 <= rst_sync1; end
    end

    // ---- blocker decode (pure combinational: works even if I2C is hung) ----
    wire blocker_unhealthy = ~init_healthy;
    wire blocker_fault     =  fault_latched;
    wire blocker_force     =  force_off;
    wire blocker_noreq     = ~sw_oe_req;

    wire any_blocker = blocker_unhealthy | blocker_fault | blocker_force | blocker_noreq;

    // ---- arm timer: count how long the conditions have been clean ----
    always @(posedge clk or negedge rst_n_sync) begin
        if (!rst_n_sync)            arm_timer <= 32'd0;
        else if (any_blocker)       arm_timer <= 32'd0;
        else if (arm_timer < ARM_CYCLES[31:0]) arm_timer <= arm_timer + 32'd1;
    end

    wire arm_ready = !any_blocker && (arm_timer >= ARM_CYCLES[31:0]);

    // ---- off dwell timer: enforced minimum OFF time after a fault ----
    always @(posedge clk or negedge rst_n_sync) begin
        if (!rst_n_sync)                      off_dwell <= 32'd0;
        else if (blocker_fault | blocker_force) off_dwell <= 32'd0;
        else if (off_dwell < OFF_CYCLES[31:0])  off_dwell <= off_dwell + 32'd1;
    end

    wire dwell_ok = (off_dwell >= OFF_CYCLES[31:0]);

    // ---- armed / oe_n ----
    always @(posedge clk or negedge rst_n_sync) begin
        if (!rst_n_sync) begin
            armed      <= 1'b0;
            oe_n       <= 1'b1;              // R2: safe default = DISABLED
            off_reason <= R_RESET;
        end else begin
            armed <= arm_ready && dwell_ok;
            // R5/R6: disable is immediate and needs no bus transaction
            oe_n  <= ~(arm_ready && dwell_ok);
            if (blocker_fault)        off_reason <= R_FAULT;
            else if (blocker_force)   off_reason <= R_FORCE_OFF;
            else if (blocker_unhealthy) off_reason <= R_UNHEALTHY;
            else if (blocker_noreq)   off_reason <= R_NO_REQ;
            else if (!arm_ready || !dwell_ok) off_reason <= R_NOT_ARMED;
            else                      off_reason <= R_NONE;
        end
    end

    // synthesis guard: i2c_busy is intentionally only informational, never a
    // blocker -- a hung bus must not be able to keep the output enabled.
    wire unused_i2c_busy = i2c_busy;

endmodule
