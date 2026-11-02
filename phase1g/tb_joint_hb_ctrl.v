`timescale 1ns/1ps
// Phase 1G-V #6: heartbeat + PCA9685 control chain JOINT simulation
module tb_joint_hb_ctrl();
    localparam integer CLK_HZ = 96_968_727;
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

    kv260_haptic_top dut (
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
    wire p_init_done = dut.u_pipe.init_done;

    // ---- minimal ACK-all I2C slave so init actually runs ----
    reg slave_sda_low = 0;
    assign scl_pin = 1'bz;
    assign sda_pin = slave_sda_low ? 1'b0 : 1'bz;
    pullup(scl_pin); pullup(sda_pin);
    wire scl_r = scl_pin, sda_r = sda_pin;
    reg [7:0] shift=0; integer bitcnt=0; reg in_frame=0, addr_phase=1, ack_phase=0;
    integer n_bus_bytes = 0;
    always @(negedge sda_r) if (scl_r === 1'b1) begin
        in_frame<=1; addr_phase<=1; ack_phase<=0; bitcnt<=0; shift<=0; end
    always @(posedge sda_r) if (scl_r === 1'b1 && in_frame) begin
        in_frame<=0; ack_phase<=0; bitcnt<=0; slave_sda_low<=1'b0; end
    always @(posedge scl_r) if (in_frame && !ack_phase && bitcnt<8) begin
        shift <= {shift[6:0], sda_r}; bitcnt <= bitcnt+1; end
    always @(negedge scl_r) if (in_frame) begin
        if (!ack_phase && bitcnt==8) begin
            slave_sda_low<=1'b1; ack_phase<=1'b1; n_bus_bytes = n_bus_bytes+1;
            if (addr_phase) addr_phase<=0;
        end else if (ack_phase) begin
            slave_sda_low<=1'b0; ack_phase<=0; bitcnt<=0; shift<=0; end
    end

    // ---- heartbeat measurement ----
    integer n_hb=0, n_hb_before=0;
    realtime t_prev=0.0, per_min=1e12, per_max=0.0, per_sum=0.0;
    integer n_per=0;
    realtime d_hb;
    always @(posedge wdog_heartbeat) begin
        n_hb = n_hb + 1;
        if (t_prev > 0.0) begin
            d_hb = $realtime - t_prev;
            per_sum = per_sum + d_hb; n_per = n_per + 1;
            if (d_hb < per_min) per_min = d_hb;
            if (d_hb > per_max) per_max = d_hb;
        end
        t_prev = $realtime;
    end

    task axi_write(input [15:0] a, input [31:0] d);
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

    integer errors=0, checks=0, i, e0;
    task chk(input [255:0] nm, input cond);
        begin checks=checks+1;
            if (cond) $display("[PASS] %0s", nm);
            else begin errors=errors+1; $display("[FAIL] %0s", nm); end end
    endtask

    initial begin
        $display("=== JOINT heartbeat + control chain ===");
        rstn=0; repeat(50) @(posedge clk);
        chk("J1 reset: heartbeat LOW while control held in reset", wdog_heartbeat===1'b0);
        rstn=1; repeat(100) @(posedge clk);

        // start PCA9685 init (long I2C activity)
        axi_write(16'h0000, 32'h0000_0001);
        for (i=0;i<256;i=i+1) axi_write(16'h0100+(i*4), i%5);
        axi_write(16'h0000, 32'h0000_0003);

        // run ~12 ms of real I2C traffic
        #12_000_000;
        $display("  bus_bytes=%0d  hb_rises=%0d  hb_period avg=%0.3f us min=%0.3f max=%0.3f",
                 n_bus_bytes, n_hb, per_sum/n_per/1000.0, per_min/1000.0, per_max/1000.0);
        chk("J2 I2C bus actually active during the window", n_bus_bytes > 100);
        chk("J3 heartbeat keeps running while I2C is busy", n_hb >= 8);
        chk("J4 heartbeat period unaffected by I2C activity (+/-2%)",
            (per_sum/n_per > 980_000.0) && (per_sum/n_per < 1_020_000.0));
        chk("J5 heartbeat jitter small (min/max within +/-2%)",
            (per_min > 980_000.0) && (per_max < 1_020_000.0));
        chk("J6 OE# still OFF by default during init (no SW request)", oe_n === 1'b1);

        // clock stop while control is mid-operation
        e0 = n_hb; clk_en = 0;
        #3_000_000;
        chk("J7 clock stop: heartbeat stops even though control was active", (n_hb-e0)==0);
        clk_en = 1;
        #3_000_000;
        chk("J8 clock resume: heartbeat resumes", (n_hb-e0) > 0);

        $display("=== JOINT RESULT: checks=%0d errors=%0d ===", checks, errors);
        if (errors==0) $display("=== PHASE1G-V JOINT: PASS ==="); else $display("=== PHASE1G-V JOINT: FAIL ===");
        $finish;
    end
    initial begin #60_000_000; $display("=== JOINT TIMEOUT ==="); $finish; end
endmodule