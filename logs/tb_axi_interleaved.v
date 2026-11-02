`timescale 1ns/1ps
// tb_axi_interleaved.v -- Phase 2B-FIX targeted tests T1..T7
// Verifies the AXI-Lite region decode fix: a write to one region can NEVER be
// routed by a stale read address (and vice versa).
//
// OE safety is checked via DIRECT hierarchical access to dut.oe_n / dut.sw_oe_req,
// NOT through the AXI readback -- so a broken read path cannot mask a real OE fault.
module tb_axi_interleaved();
    localparam integer CLK_HZ = 90_908_180;
    localparam real TCK = 1.0e9/CLK_HZ;
    reg clk=0, rstn=0;
    always #(TCK/2.0) clk = ~clk;
    wire pl_clk0=clk, pl_resetn0=rstn;

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
    assign scl_pin = 1'bz; assign sda_pin = 1'bz;
    pullup(scl_pin); pullup(sda_pin);

    // ---- DIRECT hierarchical OE observation (independent of AXI readback) ----
    wire h_oe_n      = dut.oe_n;
    wire h_sw_oe_req = dut.sw_oe_req;

    // ---- one-hot monitor on the real decode ----
    integer oh_viol = 0;
    integer aw_hits = 0, ar_hits = 0;
    integer oh_n, oh_m;
    always @(posedge clk) begin
        if (dut.s_axi_awvalid) begin
            oh_n = dut.sel_ctrl_aw + dut.sel_sts_aw + dut.sel_oe_aw;
            if (oh_n > 1) begin oh_viol = oh_viol + 1;
                $display("  [ONEHOT] AW hit %0d regions! addr=0x%04X", oh_n, dut.s_axi_awaddr); end
            if (oh_n == 1) aw_hits = aw_hits + 1;
        end
        if (dut.s_axi_arvalid) begin
            oh_m = dut.sel_ctrl_ar + dut.sel_sts_ar + dut.sel_oe_ar;
            if (oh_m > 1) begin oh_viol = oh_viol + 1;
                $display("  [ONEHOT] AR hit %0d regions! addr=0x%04X", oh_m, dut.s_axi_araddr); end
            if (oh_m == 1) ar_hits = ar_hits + 1;
        end
    end

    // ---- AXI tasks (independent AW / W) ----
    task aw_send(input [15:0] a);
        begin
            @(posedge clk); awaddr<=a; awvalid<=1;
            @(posedge clk); while(!awready) @(posedge clk);
            awvalid<=0; @(posedge clk);
        end
    endtask
    task w_send(input [31:0] d);
        begin
            @(posedge clk); wdata<=d; wstrb<=4'hF; wvalid<=1;
            @(posedge clk); while(!wready) @(posedge clk);
            wvalid<=0; @(posedge clk);
        end
    endtask
    task b_wait(output [1:0] br);
        begin
            bready<=1;
            @(posedge clk); while(!bvalid) @(posedge clk);
            br = bresp; @(posedge clk); bready<=0;
        end
    endtask
    task wr(input [15:0] a, input [31:0] d);
        begin : W
            reg [1:0] br;
            fork aw_send(a); w_send(d); join
            b_wait(br);
            $display("  WRITE awaddr=0x%04X wdata=0x%08X aw_hit=%0d sts_hit=%0d oe_hit=%0d bresp=%0d",
                     a, d, dut.sel_ctrl_aw, dut.sel_sts_aw, dut.sel_oe_aw, br);
        end
    endtask
    task rd(input [15:0] a, output [31:0] d);
        begin
            @(posedge clk); araddr<=a; arvalid<=1; rready<=1;
            @(posedge clk); while(!arready) @(posedge clk); arvalid<=0;
            @(posedge clk); while(!rvalid) @(posedge clk);
            d = rdata; $display("  READ  araddr=0x%04X -> 0x%08X (ar_hit ctrl=%0d sts=%0d oe=%0d)",
                                a, d, dut.sel_ctrl_ar, dut.sel_sts_ar, dut.sel_oe_ar);
            @(posedge clk); rready<=0;
        end
    endtask

    localparam [15:0] MB_CTRL=16'h0000, MB_STATUS=16'h0004,
                      MB_PEND0=16'h0100, MB_ACT0=16'h0200,
                      ST_SWFS=16'h1024, ST_INITDONE=16'h1028, ST_ONLINE=16'h102C,
                      OE_CTRL=16'h2000;

    integer errors=0, checks=0;
    task chk(input [255:0] nm, input cond);
        begin checks=checks+1;
            if (cond) $display("[PASS] %0s", nm);
            else begin errors=errors+1; $display("[FAIL] %0s", nm); end end
    endtask

    reg [31:0] v, v2; reg [1:0] br;
    reg oe_before, req_before;

    initial begin
        $display("=== PHASE 2B-FIX interleaved AXI test (CLK=%0d Hz) ===", CLK_HZ);
        rstn=0; repeat(50) @(posedge clk); rstn=1; repeat(50) @(posedge clk);

        $display("-- baseline OE (direct hierarchical) --");
        $display("  dut.oe_n=%0b  dut.sw_oe_req=%0b  (expect 1 / 0)", h_oe_n, h_sw_oe_req);
        chk("B0 reset: dut.oe_n == 1 (outputs OFF)", h_oe_n === 1'b1);
        chk("B1 reset: dut.sw_oe_req == 0",          h_sw_oe_req === 1'b0);

        // ---------------- T1: read 0x2000 -> write 0x0000 ----------------
        $display("\n-- T1: read 0x2000 then write 0x0000 (must NOT touch OE) --");
        req_before = h_sw_oe_req; oe_before = h_oe_n;
        rd(OE_CTRL, v);
        wr(MB_CTRL, 32'h1);
        $display("  after: dut.sw_oe_req=%0b dut.oe_n=%0b", h_sw_oe_req, h_oe_n);
        chk("T1 write to 0x0000 did not change sw_oe_req", h_sw_oe_req === req_before);
        chk("T1 write to 0x0000 did not change oe_n",      h_oe_n === oe_before);

        // ---------------- T2: read 0x0000 -> write 0x2000 ----------------
        $display("\n-- T2: read 0x0000 then write 0x2000 (only OE may respond) --");
        rd(MB_CTRL, v);
        wr(OE_CTRL, 32'h2);                 // SW_OE_CLR, keeps req 0
        $display("  after: dut.sw_oe_req=%0b dut.oe_n=%0b", h_sw_oe_req, h_oe_n);
        chk("T2 OE region write did not enable outputs (oe_n stays 1)", h_oe_n === 1'b1);
        chk("T2 OE region write left sw_oe_req 0", h_sw_oe_req === 1'b0);

        // ---------------- T3: read 0x1000 -> write 0x0000 ----------------
        $display("\n-- T3: read 0x1000 then write 0x0000 (must NOT touch status regs) --");
        rd(ST_ONLINE, v);
        wr(MB_CTRL, 32'h1);
        rd(ST_SWFS, v2);
        $display("  ST_SW_FS_CTRL after = 0x%08X (expect 0)", v2);
        chk("T3 status SW_FS_CTRL not modified by matrix write", (v2 & 32'h1) === 1'b0);

        // ---------------- T4: write matrix -> read OE ----------------
        $display("\n-- T4: write matrix then read OE --");
        wr(MB_PEND0, 32'd3);
        rd(OE_CTRL, v);
        $display("  OE_CTRL readback = 0x%08X (bit1=oe_n, bit0=req)", v);
        chk("T4 OE readback bit1 == 1 (oe_n)", ((v>>1)&1) === 1'b1);
        chk("T4 OE readback bit0 == 0 (req)",  ((v>>0)&1) === 1'b0);
        chk("T4 matrix write left dut.oe_n == 1", h_oe_n === 1'b1);
        chk("T4 matrix write left dut.sw_oe_req == 0", h_sw_oe_req === 1'b0);

        // ---------------- T5: interleaved reads/writes across 3 regions ----------------
        $display("\n-- T5: interleaved read/write across all 3 regions --");
        rd(OE_CTRL, v);      wr(MB_CTRL, 32'h1);
        rd(MB_STATUS, v);    wr(ST_SWFS, 32'h0);
        rd(ST_INITDONE, v);  wr(OE_CTRL, 32'h2);
        rd(MB_ACT0, v);      wr(MB_PEND0, 32'd2);
        rd(ST_ONLINE, v);    wr(MB_CTRL, 32'h1);
        rd(OE_CTRL, v);      wr(OE_CTRL, 32'h2);
        $display("  after T5: dut.oe_n=%0b dut.sw_oe_req=%0b", h_oe_n, h_sw_oe_req);
        chk("T5 interleaved traffic left oe_n == 1", h_oe_n === 1'b1);
        chk("T5 interleaved traffic left sw_oe_req == 0", h_sw_oe_req === 1'b0);

        // ---------------- T6: AW before W ----------------
        $display("\n-- T6: AW arrives several cycles BEFORE W --");
        aw_send(MB_PEND0);
        repeat(5) @(posedge clk);
        w_send(32'd1);
        b_wait(br);
        $display("  bresp=%0d", br);
        chk("T6 AW-before-W completes", br === 2'b00);
        chk("T6 AW-before-W did not touch OE", (h_oe_n===1'b1) && (h_sw_oe_req===1'b0));

        // ---------------- T7: W before AW ----------------
        $display("\n-- T7: W arrives several cycles BEFORE AW --");
        w_send(32'd1);
        repeat(5) @(posedge clk);
        aw_send(MB_PEND0);
        b_wait(br);
        $display("  bresp=%0d", br);
        chk("T7 W-before-AW completes", br === 2'b00);
        chk("T7 W-before-AW did not touch OE", (h_oe_n===1'b1) && (h_sw_oe_req===1'b0));

        // ---------------- OE region with W before AW (the tricky one) ----------------
        $display("\n-- T7b: OE region, W before AW --");
        w_send(32'h2);
        repeat(5) @(posedge clk);
        aw_send(OE_CTRL);
        b_wait(br);
        $display("  bresp=%0d  dut.sw_oe_req=%0b dut.oe_n=%0b", br, h_sw_oe_req, h_oe_n);
        chk("T7b OE write with W-first handled, outputs stay OFF", h_oe_n === 1'b1);

        $display("\n=== one-hot violations = %0d (must be 0) ===", oh_viol);
        chk("OH1 no multi-region hit on any AW/AR transaction", oh_viol == 0);

        $display("\n=== RESULT: checks=%0d errors=%0d ===", checks, errors);
        if (errors==0) $display("=== PHASE2B-FIX INTERLEAVED: PASS ===");
        else           $display("=== PHASE2B-FIX INTERLEAVED: FAIL ===");
        $finish;
    end
    initial begin #50_000_000; $display("=== TIMEOUT ==="); $finish; end
endmodule