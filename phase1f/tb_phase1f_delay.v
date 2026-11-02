`timescale 1ns/1ps
module tb_phase1f_delay #(
    parameter integer CLK_HZ      = 96_968_727,
    parameter integer I2C_FREQ_HZ = 378_700
)();
    localparam real TCK = 1.0e9 / CLK_HZ;
    reg clk = 0, rstn = 0;
    always #(TCK/2.0) clk = ~clk;
    wire pl_clk0 = clk, pl_resetn0 = rstn;

    reg  [15:0] awaddr=0; reg awvalid=0; wire awready;
    reg  [31:0] wdata=0;  reg [3:0] wstrb=0; reg wvalid=0; wire wready;
    wire [1:0] bresp; wire bvalid; reg bready=0;
    reg  [15:0] araddr=0; reg arvalid=0; wire arready;
    wire [31:0] rdata; wire [1:0] rresp; wire rvalid; reg rready=0;
    wire scl_pin, sda_pin, oe_n;
    wire [7:0] dbg_frame_cnt; wire dbg_i2c_ack_err, dbg_block_ready;
    wire [3:0] dbg_oe_reason; wire dbg_oe_armed;
    wire [15:0] dbg_online_mask;
    wire dbg_init_healthy, dbg_fault_latched, dbg_force_off;

    kv260_haptic_top #(
        .CLK_FREQ_HZ(CLK_HZ), .I2C_FREQ_HZ(I2C_FREQ_HZ),
        .I2C_TIMEOUT_US(5000), .FAULT_RETRY_MS(10),
        .FS_TIMEOUT_MS(500), .FS_DEFER_MS(50),
        .OE_ARM_DELAY_MS(100), .OE_MIN_OFF_MS(200)
    ) dut (
        .pl_clk0(pl_clk0), .pl_resetn0(pl_resetn0),
        .s_axi_awaddr(awaddr), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
        .s_axi_wdata(wdata), .s_axi_wstrb(wstrb), .s_axi_wvalid(wvalid), .s_axi_wready(wready),
        .s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
        .s_axi_araddr(araddr), .s_axi_arvalid(arvalid), .s_axi_arready(arready),
        .s_axi_rdata(rdata), .s_axi_rresp(rresp), .s_axi_rvalid(rvalid), .s_axi_rready(rready),
        .i2c_scl_pin(scl_pin), .i2c_sda_pin(sda_pin), .oe_n(oe_n),
        .dbg_frame_cnt(dbg_frame_cnt), .dbg_i2c_ack_err(dbg_i2c_ack_err),
        .dbg_block_ready(dbg_block_ready), .dbg_oe_reason(dbg_oe_reason),
        .dbg_oe_armed(dbg_oe_armed), .dbg_online_mask(dbg_online_mask),
        .dbg_init_healthy(dbg_init_healthy), .dbg_fault_latched(dbg_fault_latched),
        .dbg_force_off(dbg_force_off)
    );
    wire p_init_done = dut.u_pipe.init_done;

    reg slave_sda_low = 0;
    assign scl_pin = 1'bz;
    assign sda_pin = slave_sda_low ? 1'b0 : 1'bz;
    pullup (scl_pin); pullup (sda_pin);
    wire scl_r = scl_pin, sda_r = sda_pin;

    reg [7:0] shift = 0; integer bitcnt = 0;
    reg in_frame = 0, addr_phase = 1, ack_phase = 0;
    reg [7:0] cur_addr = 0; integer wr_idx = 0, ptr = -1, regidx = -1;
    integer blk = -1;

    realtime t_wake [0:15];
    realtime t_rst  [0:15];
    realtime delay_v[0:15];
    integer  n_delay [0:15];
    reg [7:0] last_mode1;

    integer i, b;

    initial begin
        for (i = 0; i < 16; i = i + 1) begin
            t_wake[i] = -1.0; t_rst[i] = -1.0; delay_v[i] = -1.0; n_delay[i] = 0;
        end
    end

    // ---- ACK-only slave + per-block wake/restart timing capture ----
    always @(negedge sda_r) if (scl_r === 1'b1) begin
        in_frame <= 1; addr_phase <= 1; ack_phase <= 0; bitcnt <= 0; shift <= 0;
    end
    always @(posedge sda_r) if (scl_r === 1'b1 && in_frame) begin
        in_frame <= 0; addr_phase <= 1; ack_phase <= 0; bitcnt <= 0;
        slave_sda_low <= 1'b0;
        // STOP of a transaction that wrote MODE1=0x20 (wake) -> start of the delay
        if (blk >= 0 && t_wake[blk] < 0.0) t_wake[blk] = $realtime;
        blk <= -1;
    end
    always @(posedge scl_r) if (in_frame && !ack_phase && bitcnt < 8) begin
        shift <= {shift[6:0], sda_r}; bitcnt <= bitcnt + 1;
    end
    always @(negedge scl_r) if (in_frame) begin
        if (!ack_phase && bitcnt == 8) begin
            if (addr_phase) begin
                cur_addr <= shift; addr_phase <= 0; wr_idx <= 0; ptr <= -1;
                // START of a transaction addressed to block b, after its wake
                if (shift[7:1] >= 7'h40 && shift[7:1] <= 7'h4F) begin
                    b = shift[7:1] - 7'h40;
                    for (i = 0; i < 16; i = i + 1) ;
                    if (t_wake[b] > 0.0 && t_rst[b] < 0.0) begin
                        t_rst[b] = $realtime;
                        delay_v[b] = t_rst[b] - t_wake[b];
                        n_delay[b] = n_delay[b] + 1;
                    end
                end
            end else begin
                wr_idx = wr_idx + 1;
                if (wr_idx == 1) begin ptr = shift; regidx = -1; end
                else begin
                    regidx = ptr + (wr_idx - 2);
                    if (regidx == 8'h00) last_mode1 = shift;
                    // MODE1=0xA0 (RESTART) marks the end of the wait window
                    // wake = MODE1 0x20 (SLEEP cleared) -> start of the 1 ms window
                    if (regidx == 8'h00 && shift == 8'h20 &&
                        cur_addr[7:1] >= 7'h40 && cur_addr[7:1] <= 7'h4F)
                        blk = cur_addr[7:1] - 7'h40;
                end
            end
            slave_sda_low <= 1'b1; ack_phase <= 1'b1;
        end else if (ack_phase) begin
            slave_sda_low <= 1'b0; ack_phase <= 0; bitcnt <= 0; shift <= 0;
        end
    end

    task axi_write(input [15:0] a, input [31:0] d);
        begin
            @(posedge clk);
            awaddr <= a; awvalid <= 1'b1; wdata <= d; wstrb <= 4'hF; wvalid <= 1'b1; bready <= 1'b1;
            fork
                begin @(posedge clk); while(!awready) @(posedge clk); awvalid <= 1'b0; end
                begin @(posedge clk); while(!wready)  @(posedge clk); wvalid  <= 1'b0; end
            join
            @(posedge clk); while(!bvalid) @(posedge clk);
            @(posedge clk);
        end
    endtask

    integer j, errors = 0;
    realtime expected_us, mn, mx, sum;
    integer  n_ok;

    initial begin
        $display("=== PHASE1F delay test: CLK=%0d Hz, I2C_FREQ=%0d Hz ===", CLK_HZ, I2C_FREQ_HZ);
        $display("    DLY_1MS = CLK/1000 = %0d cycles -> %.3f us expected",
                 CLK_HZ/1000, (CLK_HZ/1000)*1.0e6/CLK_HZ);
        rstn = 0; repeat(20) @(posedge clk); rstn = 1; repeat(20) @(posedge clk);
        axi_write(16'h0000, 32'h0000_0001);
        for (j = 0; j < 256; j = j + 1) axi_write(16'h0100 + (j*4), (j % 5));
        axi_write(16'h0000, 32'h0000_0003);
        j = 0;
        while (!p_init_done && j < 12_000_000) begin @(posedge clk); j = j + 1; end
        repeat(50_000) @(posedge clk);

        expected_us = (CLK_HZ/1000) * 1.0e6 / CLK_HZ;
        $display("  init_done=%0b", p_init_done);
        $display("  blk  wake->restart (us)   #measured");
        mn = 1.0e12; mx = 0.0; sum = 0.0; n_ok = 0;
        for (i = 0; i < 16; i = i + 1) begin
            $display("  %2d   %10.3f            %0d", i,
                     (n_delay[i] > 0) ? delay_v[i]/1000.0 : -1.0, n_delay[i]);
            if (n_delay[i] > 0) begin
                if (delay_v[i]/1000.0 < mn) mn = delay_v[i]/1000.0;
                if (delay_v[i]/1000.0 > mx) mx = delay_v[i]/1000.0;
                sum = sum + delay_v[i]/1000.0;
                if (delay_v[i]/1000.0 >= 500.0) n_ok = n_ok + 1;
            end
        end
        $display("  ---- min=%.3f us  max=%.3f us  mean=%.3f us  expected=%.3f us",
                 mn, mx, sum/n_ok, expected_us);
        $display("  blocks with delay >= 500 us: %0d / 16", n_ok);

        if (n_ok != 16) begin errors = errors + 1; $display("[FAIL] not all 16 blocks had a >=500us wake delay"); end
        else $display("[PASS] all 16 blocks wake delay >= 500 us");
        if (n_ok == 0) begin
            errors = errors + 1; $display("[FAIL] no delay measured at all");
        end else if (mn < expected_us * 0.95 || mx > expected_us * 1.15) begin
            errors = errors + 1;
            $display("[FAIL] delay out of tolerance: min=%.3f max=%.3f expected=%.3f", mn, mx, expected_us);
        end else $display("[PASS] delay within 95%%..115%% of DLY_1MS (%.3f..%.3f us)",
                          expected_us*0.95, expected_us*1.15);
        $display("=== PHASE1F DELAY RESULT: errors=%0d ===", errors);
        $finish;
    end
    initial begin #2_000_000_000; $display("=== PHASE1F DELAY TIMEOUT ==="); $finish; end
endmodule
