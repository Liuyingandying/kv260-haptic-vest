// kv260_haptic_top.v -- KV260-specific haptic control top (NEW for KV260 port)
//
// This is NOT a port of the AX7020 top. It is a new KV260 integration layer that
// reuses the verified AX7020 pipeline (kv260_haptic_pipeline) unchanged and adds
// the KV260-specific pieces the AX7020 design lacked:
//
//   * KV260 PS clock/reset naming (pl_clk0 / pl_resetn0)
//   * ONE AXI4-Lite slave port with internal address decode, so the KV260 BD
//     SmartConnect needs only a single M_AXI interface:
//         0x0000_0xxx  -> pipeline control (matrix buffer + control regs)
//         0x0000_1xxx  -> pipeline status  (axi_status_regs)
//         0x0000_2xxx  -> OE control register (this file)
//   * haptic_oe_guard: independent PCA9685 OE# with safe OFF default
//   * open-drain I2C pads (drive low / release high-Z only, never push-pull high)
//
// NOT SAID HERE: any physical pin. J2 mapping remains UNKNOWN; the XDC shipped
// with this project carries explicit TODO markers instead of guessed LOC.
`timescale 1ns/1ps

module kv260_haptic_top #(
    parameter integer CLK_FREQ_HZ  = 96_968_727,  // KV260 pl_clk0
    // Phase 1D: Fast-mode compliance. TICKS = CLK_FREQ_HZ/(4*I2C_FREQ_HZ).
    // 400_000 -> TICKS=60 -> 404.04 kHz, t_LOW=1237.5 ns  (VIOLATES 400 kHz and 1.3 us)
    // 378_700 -> TICKS=64 -> 378.78 kHz, t_LOW=1320.0 ns  (compliant)
    parameter integer I2C_FREQ_HZ   = 378_700,
    parameter integer I2C_TIMEOUT_US = 5000,
    parameter integer FAULT_RETRY_MS = 10,
    parameter integer FS_TIMEOUT_MS  = 500,
    parameter integer FS_DEFER_MS    = 50,
    parameter integer OE_ARM_DELAY_MS = 100,
    parameter integer OE_MIN_OFF_MS   = 200,
    // Phase 1G-U: external hardware watchdog heartbeat (~1 kHz square wave)
    parameter integer WDOG_HB_HZ      = 1_000
)(
    // ---- KV260 PS PL clock / reset ----
    input  wire        pl_clk0,        // psu_pl_clk0 = 96.968727 MHz
    input  wire        pl_resetn0,     // active low

    // ---- single AXI4-Lite slave (from KV260 BD SmartConnect M_AXI) ----
    input  wire [15:0] s_axi_awaddr,
    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,
    output wire [1:0]  s_axi_bresp,
    output wire        s_axi_bvalid,
    input  wire        s_axi_bready,
    input  wire [15:0] s_axi_araddr,
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,
    output wire [31:0] s_axi_rdata,
    output wire [1:0]  s_axi_rresp,
    output wire        s_axi_rvalid,
    input  wire        s_axi_rready,

    // ---- I2C open-drain pads (external 4.7k pull-up to 3.3V REQUIRED) ----
    // Phase 2F-FIX: open-drain pads are driven by an explicit IOBUF at the
    // wrapper level; this module exports drive intent and consumes sensed level.
    output wire        i2c_scl_drv,
    output wire        i2c_sda_drv,
    input  wire        i2c_scl_i,
    input  wire        i2c_sda_i,

    // ---- PCA9685 OE# : external 3.3V pull-up, FPGA drives LOW to enable ----
    output wire        oe_n,

    // ---- Phase 1G-U: heartbeat to the EXTERNAL hardware watchdog --------
    // ~1 kHz square wave. The external retriggerable monostable re-arms on
    // every edge; if the edges stop (reset / clock stop / FPGA unconfigured)
    // the monostable times out and OE# is pulled HIGH => outputs OFF.
    // Frozen DC level is harmless because the watchdog is EDGE triggered.
    output wire        wdog_heartbeat,

    // ---- debug / observability ----
    output wire [7:0]  dbg_frame_cnt,
    output wire        dbg_i2c_ack_err,
    output wire        dbg_block_ready,
    output wire [3:0]  dbg_oe_reason,
    output wire        dbg_oe_armed,
    output wire [15:0] dbg_online_mask,
    output wire        dbg_init_healthy,
    output wire        dbg_fault_latched,
    output wire        dbg_force_off
);

    // =========================================================================
    // Address decode: bits [13:12]
    //   2'b00 -> pipeline ctrl  (0x0000)
    //   2'b01 -> pipeline sts   (0x1000)
    //   2'b10 -> OE control reg (0x2000)
    // =========================================================================
    // Phase 2B-FIX: WRITE and READ region decode must be SEPARATE.
    // The previous form OR-ed awaddr and araddr into one sel_* signal, so a stale
    // read address selected the OE register during a legitimate matrix write and
    // set SW_OE_REQ (safety defect). Write paths use *_aw only, read paths *_ar only.
    wire sel_ctrl_aw = (s_axi_awaddr[13:12] == 2'b00);
    wire sel_sts_aw  = (s_axi_awaddr[13:12] == 2'b01);
    wire sel_oe_aw   = (s_axi_awaddr[13:12] == 2'b10);
    wire sel_ctrl_ar = (s_axi_araddr[13:12] == 2'b00);
    wire sel_sts_ar  = (s_axi_araddr[13:12] == 2'b01);
    wire sel_oe_ar   = (s_axi_araddr[13:12] == 2'b10);


    // =========================================================================
    // OE control register (KV260-specific, 0x2000)
    //   bit0 : SW_OE_REQ  - software requests output enable (0 = keep OFF)
    //   bit1 : SW_OE_CLR  - write 1 to force request back to 0 (self-clearing)
    // Read back: {28'b0, armed, oe_n, sw_oe_req} on bits [3:0] area
    // =========================================================================
    wire       oe_armed;
    reg        sw_oe_req;
    reg        oe_bvalid;
    reg [1:0]  oe_bresp;
    reg        oe_rvalid;
    reg [31:0] oe_rdata;
    reg [1:0]  oe_rresp;

    // Phase 2B-FIX: AW and W are buffered INDEPENDENTLY (AXI-Lite does not require
    // them to arrive in the same cycle, nor in a fixed order).
    reg        oe_aw_pend;
    reg        oe_w_pend;
    reg [31:0] oe_wdata_q;
    wire oe_aw_acc = s_axi_awvalid && sel_oe_aw && !oe_aw_pend;
    wire oe_w_acc  = s_axi_wvalid  && sel_oe_aw && !oe_w_pend;
    wire oe_commit = oe_aw_pend && oe_w_pend;

    always @(posedge pl_clk0 or negedge pl_resetn0) begin
        if (!pl_resetn0) begin
            sw_oe_req <= 1'b0;          // R2: default OFF
            oe_bvalid <= 1'b0;
            oe_bresp  <= 2'b00;
            oe_aw_pend<= 1'b0;
            oe_w_pend <= 1'b0;
            oe_wdata_q<= 32'd0;
        end else begin
            oe_bvalid <= 1'b0;
            if (oe_aw_acc) oe_aw_pend <= 1'b1;
            if (oe_w_acc) begin
                oe_w_pend  <= 1'b1;
                oe_wdata_q <= s_axi_wdata;
            end
            if (oe_commit) begin
                if (oe_wdata_q[1])      sw_oe_req <= 1'b0;   // explicit clear wins
                else                    sw_oe_req <= oe_wdata_q[0];
                oe_aw_pend <= 1'b0;
                oe_w_pend  <= 1'b0;
                oe_bvalid  <= 1'b1;
                oe_bresp   <= 2'b00;
            end else if (oe_bvalid && s_axi_bready) begin
                oe_bvalid <= 1'b0;
            end
        end
    end

    always @(posedge pl_clk0 or negedge pl_resetn0) begin
        if (!pl_resetn0) begin
            oe_rvalid <= 1'b0; oe_rdata <= 32'd0; oe_rresp <= 2'b00;
        end else begin
            if (sel_oe_ar && s_axi_arvalid && !oe_rvalid) begin
                oe_rvalid <= 1'b1;
                oe_rdata  <= {28'd0, oe_armed, oe_n, sw_oe_req};
                oe_rresp  <= 2'b00;
            end else if (oe_rvalid && s_axi_rready) begin
                oe_rvalid <= 1'b0;
            end
        end
    end

    // =========================================================================
    // Verified AX7020 pipeline (unmodified logic; KV260 clock parameter)
    // =========================================================================
    wire        p_init_healthy, p_fault_latched, p_force_off, p_runtime_run;
    wire [15:0] p_online_mask;
    wire [31:0] p_i2c_error_count;
    wire        p_i2c_busy;

    wire        ctrl_awready, ctrl_wready, ctrl_bvalid, ctrl_arready, ctrl_rvalid;
    wire [1:0]  ctrl_bresp, ctrl_rresp;
    wire [31:0] ctrl_rdata;
    wire        sts_awready, sts_wready, sts_bvalid, sts_arready, sts_rvalid;
    wire [1:0]  sts_bresp, sts_rresp;
    wire [31:0] sts_rdata;

    kv260_haptic_pipeline #(
        .CLK_FREQ_HZ     (CLK_FREQ_HZ),
        .I2C_FREQ_HZ     (I2C_FREQ_HZ),
        .I2C_TIMEOUT_US  (I2C_TIMEOUT_US),
        .FAULT_RETRY_MS  (FAULT_RETRY_MS),
        .FS_TIMEOUT_MS   (FS_TIMEOUT_MS),
        .FS_DEFER_MS     (FS_DEFER_MS)
    ) u_pipe (
        .s_axi_aclk         (pl_clk0),
        .s_axi_aresetn      (pl_resetn0),

        // control AXI slave
        .s_axi_ctrl_awvalid (s_axi_awvalid && sel_ctrl_aw),
        .s_axi_ctrl_awaddr  (s_axi_awaddr[9:0]),
        .s_axi_ctrl_awready (ctrl_awready),
        .s_axi_ctrl_wvalid  (s_axi_wvalid && sel_ctrl_aw),
        .s_axi_ctrl_wdata   (s_axi_wdata),
        .s_axi_ctrl_wstrb   (s_axi_wstrb),
        .s_axi_ctrl_wready  (ctrl_wready),
        .s_axi_ctrl_bvalid  (ctrl_bvalid),
        .s_axi_ctrl_bresp   (ctrl_bresp),
        .s_axi_ctrl_bready  (s_axi_bready),
        .s_axi_ctrl_arvalid (s_axi_arvalid && sel_ctrl_ar),
        .s_axi_ctrl_araddr  (s_axi_araddr[9:0]),
        .s_axi_ctrl_arready (ctrl_arready),
        .s_axi_ctrl_rvalid  (ctrl_rvalid),
        .s_axi_ctrl_rdata   (ctrl_rdata),
        .s_axi_ctrl_rresp   (ctrl_rresp),
        .s_axi_ctrl_rready  (s_axi_rready),

        // status AXI slave
        .s_axi_sts_awvalid  (s_axi_awvalid && sel_sts_aw),
        .s_axi_sts_awaddr   (s_axi_awaddr[9:0]),
        .s_axi_sts_awready  (sts_awready),
        .s_axi_sts_wvalid   (s_axi_wvalid && sel_sts_aw),
        .s_axi_sts_wdata    (s_axi_wdata),
        .s_axi_sts_wstrb    (s_axi_wstrb),
        .s_axi_sts_wready   (sts_wready),
        .s_axi_sts_bvalid   (sts_bvalid),
        .s_axi_sts_bresp    (sts_bresp),
        .s_axi_sts_bready   (s_axi_bready),
        .s_axi_sts_arvalid  (s_axi_arvalid && sel_sts_ar),
        .s_axi_sts_araddr   (s_axi_araddr[9:0]),
        .s_axi_sts_arready  (sts_arready),
        .s_axi_sts_rvalid   (sts_rvalid),
        .s_axi_sts_rdata    (sts_rdata),
        .s_axi_sts_rresp    (sts_rresp),
        .s_axi_sts_rready   (s_axi_rready),

        .dbg_frame_cnt      (dbg_frame_cnt),
        .dbg_i2c_ack_err    (dbg_i2c_ack_err),
        .dbg_block_ready    (dbg_block_ready),

        .dd_bypass_i        (1'b0),

        .i2c_scl_drv_o      (i2c_scl_drv),
        .i2c_sda_drv_o      (i2c_sda_drv),
        .i2c_scl_i_i        (i2c_scl_i),
        .i2c_sda_i_i        (i2c_sda_i),
        

        .o_init_healthy     (p_init_healthy),
        .o_fault_latched    (p_fault_latched),
        .o_force_off        (p_force_off),
        .o_runtime_run      (p_runtime_run),
        .o_online_mask      (p_online_mask),
        .o_i2c_error_count  (p_i2c_error_count)
    );

    // =========================================================================
    // AXI response mux (single slave port)
    // =========================================================================
    // Phase 2B-FIX: write responses follow the AW decode, read responses follow the
    // AR decode, so neither channel can be routed by the other's stale address.
    // accept into the skid buffer as soon as it is empty (address-independent)
    assign s_axi_awready = sel_ctrl_aw ? ctrl_awready : (sel_sts_aw ? sts_awready : (sel_oe_aw ? 1'b1 : 1'b0));
    assign s_axi_wready  = sel_ctrl_aw ? ctrl_wready  : (sel_sts_aw ? sts_wready  : (sel_oe_aw ? 1'b1 : 1'b0));

    assign s_axi_bvalid  = sel_ctrl_aw ? ctrl_bvalid  : (sel_sts_aw ? sts_bvalid  : oe_bvalid);
    assign s_axi_bresp   = sel_ctrl_aw ? ctrl_bresp   : (sel_sts_aw ? sts_bresp   : oe_bresp);
    assign s_axi_arready = sel_ctrl_ar ? ctrl_arready : (sel_sts_ar ? sts_arready : (sel_oe_ar ? 1'b1 : 1'b0));
    assign s_axi_rvalid  = sel_ctrl_ar ? ctrl_rvalid  : (sel_sts_ar ? sts_rvalid  : oe_rvalid);
    assign s_axi_rresp   = sel_ctrl_ar ? ctrl_rresp   : (sel_sts_ar ? sts_rresp   : oe_rresp);
    assign s_axi_rdata   = sel_ctrl_ar ? ctrl_rdata   : (sel_sts_ar ? sts_rdata   : oe_rdata);

    // =========================================================================
    // OE# safety guard (NEW)
    // =========================================================================
    wire [3:0]  oe_reason;
    wire [31:0] oe_arm_timer, oe_off_dwell;

    haptic_oe_guard #(
        .CLK_FREQ_HZ  (CLK_FREQ_HZ),
        .ARM_DELAY_MS (OE_ARM_DELAY_MS),
        .MIN_OFF_MS   (OE_MIN_OFF_MS)
    ) u_oe (
        .clk          (pl_clk0),
        .rst_n        (pl_resetn0),
        .init_healthy (p_init_healthy),
        .fault_latched(p_fault_latched),
        .force_off    (p_force_off),
        .i2c_busy     (p_i2c_busy),
        .sw_oe_req    (sw_oe_req),
        .oe_n         (oe_n),
        .armed        (oe_armed),
        .off_reason   (oe_reason),
        .arm_timer    (oe_arm_timer),
        .off_dwell    (oe_off_dwell)
    );

    assign dbg_oe_reason    = oe_reason;
    assign dbg_oe_armed     = oe_armed;
    assign dbg_online_mask  = p_online_mask;
    assign dbg_init_healthy = p_init_healthy;
    assign dbg_fault_latched= p_fault_latched;
    assign dbg_force_off    = p_force_off;

    // i2c bus activity is informational only; never gates OE
    assign p_i2c_busy = 1'b0;

    // =========================================================================
    // Phase 1G-U: watchdog heartbeat generator (~1 kHz, 50% duty)
    //   HB_HALF = CLK_FREQ_HZ / (2 * WDOG_HB_HZ)
    //   96.968727 MHz -> 48,484 cycles per half period -> 1.0000 kHz
    //   50 MHz        -> 25,000 cycles per half period -> 1.0000 kHz
    // Reset: heartbeat held LOW (safe, no edges -> external watchdog stays OFF).
    // Clock stop: heartbeat freezes -> no more edges -> external watchdog times out.
    // =========================================================================
    localparam integer HB_HALF = (CLK_FREQ_HZ / (2 * WDOG_HB_HZ)) > 1
                                 ? (CLK_FREQ_HZ / (2 * WDOG_HB_HZ)) : 1;
    reg [31:0] hb_cnt;
    reg        hb_q;
    always @(posedge pl_clk0 or negedge pl_resetn0) begin
        if (!pl_resetn0) begin
            hb_cnt <= 32'd0;
            hb_q   <= 1'b0;          // reset -> LOW
        end else if (hb_cnt >= (HB_HALF - 1)) begin
            hb_cnt <= 32'd0;
            hb_q   <= ~hb_q;
        end else begin
            hb_cnt <= hb_cnt + 32'd1;
        end
    end
    assign wdog_heartbeat = hb_q;

endmodule
