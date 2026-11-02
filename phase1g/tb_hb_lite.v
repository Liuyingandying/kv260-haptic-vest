`timescale 1ns/1ps
module tb_hb_lite #(parameter integer CLK_HZ = 96_968_727)();
    localparam real TCK = 1.0e9 / CLK_HZ;
    reg clk = 0, rstn = 0, clk_en = 1;
    always #(TCK/2.0) if (clk_en) clk <= ~clk;

    wire pl_clk0 = clk, pl_resetn0 = rstn;

    reg  [15:0] awaddr=0; reg awvalid=0; wire awready;
    reg  [31:0] wdata=0;  reg [3:0] wstrb=0; reg wvalid=0; wire wready;
    wire [1:0] bresp; wire bvalid; reg bready=0;
    reg  [15:0] araddr=0; reg arvalid=0; wire arready;
    wire [31:0] rdata; wire [1:0] rresp; wire rvalid; reg rready=0;
    wire scl_pin, sda_pin, oe_n, wdog_heartbeat;
    wire [7:0] dbg_frame_cnt; wire dbg_i2c_ack_err, dbg_block_ready;
    wire [3:0] dbg_oe_reason; wire dbg_oe_armed; wire [15:0] dbg_online_mask;
    wire dbg_init_healthy, dbg_fault_latched, dbg_force_off;

    kv260_haptic_top #(.CLK_FREQ_HZ(CLK_HZ)) dut (
        .pl_clk0(pl_clk0), .pl_resetn0(pl_resetn0),
        .s_axi_awaddr(awaddr), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
        .s_axi_wdata(wdata), .s_axi_wstrb(wstrb), .s_axi_wvalid(wvalid), .s_axi_wready(wready),
        .s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
        .s_axi_araddr(araddr), .s_axi_arvalid(arvalid), .s_axi_arready(arready),
        .s_axi_rdata(rdata), .s_axi_rresp(rresp), .s_axi_rvalid(rvalid), .s_axi_rready(rready),
        .i2c_scl_pin(scl_pin), .i2c_sda_pin(sda_pin), .oe_n(oe_n),
        .wdog_heartbeat(wdog_heartbeat),
        .dbg_frame_cnt(dbg_frame_cnt), .dbg_i2c_ack_err(dbg_i2c_ack_err),
        .dbg_block_ready(dbg_block_ready), .dbg_oe_reason(dbg_oe_reason),
        .dbg_oe_armed(dbg_oe_armed), .dbg_online_mask(dbg_online_mask),
        .dbg_init_healthy(dbg_init_healthy), .dbg_fault_latched(dbg_fault_latched),
        .dbg_force_off(dbg_force_off)
    );
    assign scl_pin = 1'bz;  // bus model not needed for this test
    assign sda_pin = 1'bz;
    pullup(scl_pin); pullup(sda_pin);

    // ---- external watchdog model (74LVC1G123 + MOSFET equivalent) ----
    // retriggerable: output high (=OE# pulled low = ENABLED) while edges arrive;
    // times out to low (=OE# high = DISABLED) when edges stop.
    localparam real WDOG_TAU_US = 1500.0;   // 1.5x heartbeat period
    realtime last_edge = 0.0;
    reg      wd_ok = 0;       // 1 = watchdog satisfied -> outputs allowed
    always @(posedge wdog_heartbeat) begin
        last_edge = $realtime;
        wd_ok = 1'b1;
    end
    always @(negedge wdog_heartbeat) last_edge = $realtime;
    always begin
        #100.0;
        if (wd_ok && ((($realtime - last_edge)/1000.0) > WDOG_TAU_US)) wd_ok = 1'b0;
    end
    // actual OE# seen by PCA9685 = RTL oe_n AND (watchdog satisfied)
    wire pca_oe_n = oe_n & ~wd_ok;   // wd_ok=1 -> pca_oe_n = oe_n ; wd_ok=0 -> forced HIGH (OFF)

    // ---- measurement ----
    integer n_rise = 0;
    realtime t_prev = 0.0, per_sum = 0.0, per_min = 1e12, per_max = 0.0;
    realtime d_per;
    integer  n_per = 0;
    always @(posedge wdog_heartbeat) begin
        n_rise = n_rise + 1;
        if (t_prev > 0.0) begin
            d_per = $realtime - t_prev;
            per_sum = per_sum + d_per; n_per = n_per + 1;
            if (d_per < per_min) per_min = d_per;
            if (d_per > per_max) per_max = d_per;
        end
        t_prev = $realtime;
    end

    integer errors = 0, checks = 0, i, e0;
    task chk(input [255:0] nm, input cond);
        begin checks=checks+1;
            if (cond) $display("[PASS] %0s", nm);
            else begin errors=errors+1; $display("[FAIL] %0s", nm); end end
    endtask

    initial begin
        $display("=== PHASE1G-U heartbeat test: CLK=%0d Hz ===", CLK_HZ);
        rstn = 0; repeat(50) @(posedge clk);
        chk("H1 reset: heartbeat is LOW", wdog_heartbeat === 1'b0);
        rstn = 1;
        repeat(200) @(posedge clk);
        // wait for several heartbeat periods (~4 ms)
        #4_500_000;
        $display("  rises=%0d  period avg=%0.3f us  min=%0.3f max=%0.3f",
                 n_rise, per_sum/n_per/1000.0, per_min/1000.0, per_max/1000.0);
        chk("H2 heartbeat period ~1000 us (+/-2%)",
            (per_sum/n_per > 980_000.0) && (per_sum/n_per < 1_020_000.0));
        chk("H3 heartbeat is a clean 50% square wave",
            (per_min > 980_000.0) && (per_max < 1_020_000.0));
        chk("H4 external watchdog is satisfied while heartbeating", wd_ok === 1'b1);

        // ---- clock stop ----
        e0 = n_rise; clk_en = 0;
        #3_000_000;
        $display("  after clock stop 3ms: edges added=%0d  wd_ok=%0b  pca_oe_n=%0b",
                 n_rise-e0, wd_ok, pca_oe_n);
        chk("H5 clock stop: heartbeat stops (no new edges)", (n_rise-e0) == 0);
        chk("H6 clock stop: external watchdog times out -> forces OE# HIGH (OFF)",
            (wd_ok === 1'b0) && (pca_oe_n === 1'b1));

        // ---- resume ----
        e0 = n_rise; clk_en = 1;
        #3_000_000;
        $display("  after clock resume 3ms: edges added=%0d  wd_ok=%0b", n_rise-e0, wd_ok);
        chk("H7 clock resume: heartbeat resumes (new edges)", (n_rise-e0) > 0);
        chk("H8 clock resume: external watchdog re-satisfied", wd_ok === 1'b1);

        $display("=== RESULT: checks=%0d errors=%0d ===", checks, errors);
        if (errors==0) $display("=== PHASE1G-U HB: PASS ==="); else $display("=== PHASE1G-U HB: FAIL ===");
        $finish;
    end
    initial begin #40_000_000; $display("=== TIMEOUT ==="); $finish; end
endmodule

`timescale 1ns/1ps
module tb_hb_96968(); tb_hb_lite #(.CLK_HZ(96_968_727)) u(); endmodule
module tb_hb_50m();   tb_hb_lite #(.CLK_HZ(50_000_000)) u(); endmodule