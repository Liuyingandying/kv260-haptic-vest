//=============================================================================
// kv260_haptic_axi_wrapper.v  --  Phase 2A integration wrapper (NOT frozen RTL)
//
// Purpose: give the frozen `kv260_haptic_top` a real AXI4-Lite INTERFACE so that
// Vivado Block Design can connect it to a SmartConnect. The frozen module has
// plain port lists (s_axi_awaddr/... , no interface attributes), which BD cannot
// treat as an AXI interface.
//
// NOTHING functional is changed: this file only re-labels the same wires with
// X_INTERFACE_INFO attributes and instantiates the frozen top unchanged.
//
// Frozen RTL entry point : rtl/kv260_haptic_top.v  (sha cdfb57cb... , do not edit)
//=============================================================================
`timescale 1ns/1ps

module kv260_haptic_axi_wrapper #(
    parameter integer CLK_FREQ_HZ = 90_908_180
)(
    // ---- clock / reset (as BD interfaces) ----
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME aclk, ASSOCIATED_BUSIF s_axi, ASSOCIATED_RESET aresetn, FREQ_HZ 90908180, FREQ_TOLERANCE_HZ 0, PHASE 0.0, INSERT_VIP 0" *)
    input  wire        aclk,

    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME aresetn, POLARITY ACTIVE_LOW, INSERT_VIP 0" *)
    input  wire        aresetn,

    // ---- AXI4-Lite slave (single port, 64 KiB aperture) ----
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWADDR"  *) input  wire [15:0] s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWVALID" *) input  wire        s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWREADY" *) output wire        s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WDATA"   *) input  wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WSTRB"   *) input  wire [3:0]  s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WVALID"  *) input  wire        s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WREADY"  *) output wire        s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BRESP"   *) output wire [1:0]  s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BVALID"  *) output wire        s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BREADY"  *) input  wire        s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARADDR"  *) input  wire [15:0] s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARVALID" *) input  wire        s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARREADY" *) output wire        s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RDATA"   *) output wire [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RRESP"   *) output wire [1:0]  s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RVALID"  *) output wire        s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RREADY"  *) input  wire        s_axi_rready,
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME s_axi, PROTOCOL AXI4LITE, DATA_WIDTH 32, ADDR_WIDTH 16, NUM_READ_OUTSTANDING 1, NUM_WRITE_OUTSTANDING 1, READ_WRITE_MODE READ_WRITE, HAS_BURST 0, SUPPORTS_NARROW_BURST 0, MAX_BURST_LENGTH 1, FREQ_HZ 90908180, ID_WIDTH 0, AWUSER_WIDTH 0, ARUSER_WIDTH 0, WUSER_WIDTH 0, RUSER_WIDTH 0, BUSER_WIDTH 0, HAS_LOCK 0, HAS_PROT 0, HAS_CACHE 0, HAS_QOS 0, HAS_REGION 0" *)

    // ---- external physical signals (bank 45, candidate pins) ----
    inout  wire        i2c_scl_pin,
    inout  wire        i2c_sda_pin,
    output wire        wdog_heartbeat,

    // ---- debug (left internal / unused in BD) ----
    output wire [7:0]  dbg_frame_cnt,
    output wire        dbg_i2c_ack_err,
    output wire        dbg_block_ready,
    output wire [3:0]  dbg_oe_reason,
    output wire        dbg_oe_armed,
    output wire [15:0] dbg_online_mask,
    output wire        dbg_init_healthy,
    output wire        dbg_fault_latched,
    output wire        dbg_force_off,
    output wire        oe_n
);

    kv260_haptic_top #(
        .CLK_FREQ_HZ (CLK_FREQ_HZ)
    ) u_haptic (
        .pl_clk0        (aclk),
        .pl_resetn0     (aresetn),
        .s_axi_awaddr   (s_axi_awaddr),
        .s_axi_awvalid  (s_axi_awvalid),
        .s_axi_awready  (s_axi_awready),
        .s_axi_wdata    (s_axi_wdata),
        .s_axi_wstrb    (s_axi_wstrb),
        .s_axi_wvalid   (s_axi_wvalid),
        .s_axi_wready   (s_axi_wready),
        .s_axi_bresp    (s_axi_bresp),
        .s_axi_bvalid   (s_axi_bvalid),
        .s_axi_bready   (s_axi_bready),
        .s_axi_araddr   (s_axi_araddr),
        .s_axi_arvalid  (s_axi_arvalid),
        .s_axi_arready  (s_axi_arready),
        .s_axi_rdata    (s_axi_rdata),
        .s_axi_rresp    (s_axi_rresp),
        .s_axi_rvalid   (s_axi_rvalid),
        .s_axi_rready   (s_axi_rready),
        .i2c_scl_pin    (i2c_scl_pin),
        .i2c_sda_pin    (i2c_sda_pin),
        .oe_n           (oe_n),
        .wdog_heartbeat (wdog_heartbeat),
        .dbg_frame_cnt  (dbg_frame_cnt),
        .dbg_i2c_ack_err(dbg_i2c_ack_err),
        .dbg_block_ready(dbg_block_ready),
        .dbg_oe_reason  (dbg_oe_reason),
        .dbg_oe_armed   (dbg_oe_armed),
        .dbg_online_mask(dbg_online_mask),
        .dbg_init_healthy(dbg_init_healthy),
        .dbg_fault_latched(dbg_fault_latched),
        .dbg_force_off  (dbg_force_off)
    );

endmodule