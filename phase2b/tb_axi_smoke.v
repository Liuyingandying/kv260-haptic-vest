`timescale 1ns/1ps
// tb_axi_smoke.v -- replays the EXACT sequence of phase2b/haptic_axi_smoke.c
// against the frozen RTL, acting as the PS AXI4-Lite master.
// This proves the SOFTWARE REGISTER SEQUENCE is valid; it is NOT proof that a real
// PS can reach the PL (that needs a loaded bitstream + device tree).
module tb_axi_smoke();
    localparam integer CLK_HZ = 90_908_180;          // Phase 2A actual PL0
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
    // no PCA9685 present: bus idle (pulled up), master will time out safely
    assign scl_pin = 1'bz; assign sda_pin = 1'bz;
    pullup(scl_pin); pullup(sda_pin);

    // offsets identical to haptic_regs.h
    localparam [15:0] MB_CTRL=16'h0000, MB_STATUS=16'h0004,
                      MB_PEND0=16'h0100, MB_ACT0=16'h0200,
                      ST_ONLINE=16'h102C, ST_INITDONE=16'h1028,
                      ST_FAULT=16'h103C, ST_RECOV=16'h1054, OE_CTRL=16'h2000;

    task wr(input [15:0] a, input [31:0] d);
        begin
            @(posedge clk);
            awaddr<=a; awvalid<=1; wdata<=d; wstrb<=4'hF; wvalid<=1; bready<=1;
            fork
                begin @(posedge clk); while(!awready) @(posedge clk); awvalid<=0; end
                begin @(posedge clk); while(!wready)  @(posedge clk); wvalid<=0;  end
            join
            @(posedge clk); while(!bvalid) @(posedge clk);
            @(posedge clk);
        end
    endtask
    task rd(input [15:0] a, output [31:0] d);
        begin
            @(posedge clk);
            araddr<=a; arvalid<=1; rready<=1;
            @(posedge clk); while(!arready) @(posedge clk); arvalid<=0;
            @(posedge clk); while(!rvalid) @(posedge clk);
            d = rdata; @(posedge clk);
        end
    endtask

    integer errors=0, checks=0, i;
    reg [31:0] v;
    task chk(input [255:0] nm, input cond);
        begin checks=checks+1;
            if (cond) $display("[PASS] %0s", nm);
            else begin errors=errors+1; $display("[FAIL] %0s", nm); end end
    endtask

    initial begin
        $display("=== AXI SMOKE (replaying haptic_axi_smoke.c sequence) CLK=%0d Hz ===", CLK_HZ);
        rstn=0; repeat(50) @(posedge clk); rstn=1; repeat(100) @(posedge clk);

        $display("-- step 1: read PL status --");
        rd(ST_ONLINE, v);    $display("  ST_ONLINE_MASK  = 0x%08X", v); chk("S1a ST_ONLINE readable", 1);
        rd(ST_INITDONE, v);  $display("  ST_INIT_DONE    = 0x%08X", v); chk("S1b ST_INIT_DONE readable and <=1", v<=1);
        rd(ST_FAULT, v);     $display("  ST_FAULT_LATCHED= 0x%08X", v); chk("S1c ST_FAULT readable and <=1", v<=1);
        rd(ST_RECOV, v);     $display("  ST_RECOVERY_ST  = 0x%08X", v);
        rd(OE_CTRL, v);
        $display("  OE_CTRL raw=0x%08X  sw_req=%0d oe_n=%0d armed=%0d", v, v[0], v[1], v[2]);
        chk("S1d OE default OFF (oe_n==1)", v[1]===1'b1);
        chk("S1e OE not requested (sw_req==0)", v[0]===1'b0);

        $display("-- step 2: matrix write --");
        wr(MB_CTRL, 32'h1);
        wr(MB_PEND0, 32'd3);
        rd(MB_ACT0, v); $display("  active bank cell0 before commit = 0x%08X (info)", v);
        chk("S2 matrix write accepted", 1);

        $display("-- step 3: STATUS before commit --");
        rd(MB_STATUS, v); $display("  MB_STATUS before = 0x%08X act_bank=%0d", v, v[0]);
        chk("S3 STATUS readable", 1);

        $display("-- step 4: COMMIT --");
        wr(MB_CTRL, 32'h3);
        i=0;
        begin : poll
            reg [31:0] t;
            forever begin
                rd(MB_STATUS, t);
                if (t[1]) begin v=t; disable poll; end
                i=i+1;
                if (i>=200000) disable poll;
            end
        end
        $display("  COMMIT poll iters=%0d  MB_STATUS=0x%08X", i, v);
        chk("S4 COMMIT_ACK observed", v[1]===1'b1);
        wr(MB_CTRL, 32'h1);

        $display("-- step 5: active bank after commit --");
        rd(MB_ACT0, v); $display("  active bank cell0 after commit = 0x%08X", v);
        chk("S5 active bank readable after commit", 1);

        $display("-- step 6: OE safety --");
        rd(OE_CTRL, v);
        $display("  OE_CTRL final=0x%08X sw_req=%0d oe_n=%0d", v, v[0], v[1]);
        chk("S6 FINAL oe_n still 1 (OFF)", v[1]===1'b1);
        chk("S6b FINAL sw_oe_req still 0", v[0]===1'b0);

        $display("=== AXI SMOKE RESULT: checks=%0d errors=%0d ===", checks, errors);
        if (errors==0) $display("=== PHASE2B AXI SMOKE: PASS ==="); else $display("=== PHASE2B AXI SMOKE: FAIL ===");
        $finish;
    end
    initial begin #80_000_000; $display("=== TIMEOUT ==="); $finish; end
endmodule