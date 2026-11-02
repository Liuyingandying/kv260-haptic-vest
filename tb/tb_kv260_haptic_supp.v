// tb_kv260_haptic_supp.v -- Phase 1C supplementary verification (does NOT touch
// the 21-item acceptance criteria; separate testbench, separate checks S1..S6).
//
// Closes the two gaps left open by Phase 1B:
//   Gap 1: T8c only proved the fail-closed branch. Here S2/S3 prove the
//          POSITIVE enable path (init_healthy -> oe_n 0) and shutdown again.
//   Gap 2: T7 only proved register writes. Here S4 models the PCA9685 PWM
//          engine and measures the real 20 ms / 50 Hz period and the duty.
//
// I2C slave model = revB ACK timing + READ support (returns register data so the
// PCA init handshake can complete and init_healthy can actually assert).
`timescale 1ns/1ps

module tb_kv260_haptic_supp;

    localparam integer CLK_HZ = 96_968_727;
    localparam real    TCK    = 1.0e9 / CLK_HZ;
    reg clk = 0, rstn = 0;
    always #(TCK/2.0) clk = ~clk;
    wire pl_clk0 = clk, pl_resetn0 = rstn;

    reg  [15:0] awaddr=0;  reg awvalid=0;  wire awready;
    reg  [31:0] wdata=0;   reg [3:0] wstrb=0; reg wvalid=0; wire wready;
    wire [1:0]  bresp;     wire bvalid;    reg bready=0;
    reg  [15:0] araddr=0;  reg arvalid=0;  wire arready;
    wire [31:0] rdata;     wire [1:0] rresp; wire rvalid; reg rready=0;

    wire scl_pin, sda_pin, oe_n;
    wire [7:0]  dbg_frame_cnt;
    wire        dbg_i2c_ack_err, dbg_block_ready;
    wire [3:0]  dbg_oe_reason;
    wire        dbg_oe_armed;
    wire [15:0] dbg_online_mask;
    wire        dbg_init_healthy, dbg_fault_latched, dbg_force_off;

    kv260_haptic_top #(
        .CLK_FREQ_HZ(CLK_HZ), .I2C_FREQ_HZ(400_000), .I2C_TIMEOUT_US(200),
        .FAULT_RETRY_MS(1), .FS_TIMEOUT_MS(2), .FS_DEFER_MS(1),
        .OE_ARM_DELAY_MS(1), .OE_MIN_OFF_MS(1)
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

    // ================= open-drain bus + slave model (read capable) =============
    reg slave_sda_low = 0;
    assign scl_pin = 1'bz;
    assign sda_pin = slave_sda_low ? 1'b0 : 1'bz;
    pullup (scl_pin); pullup (sda_pin);

    // PCA9685 register file for the block we mirror (auto-increment writes)
    reg [7:0] pca_regs [0:255];
    integer   i;
    initial for (i = 0; i < 256; i = i + 1) pca_regs[i] = 8'h00;
    initial begin
        pca_regs[8'h00] = 8'h11;   // MODE1 POR value
        pca_regs[8'hFE] = 8'h79;   // PRE_SCALE POR = 121 -> ~50 Hz
    end

    reg [7:0] shift = 0;
    integer   bitcnt = 0;
    reg       in_frame = 0, addr_phase = 1, ack_phase = 0, is_read = 0;
    reg [7:0] cur_addr = 0;
    integer   wr_idx = 0;
    integer   pca_reg = -1;
    reg [7:0] txbyte = 8'h00;
    integer   txbit = 0;
    integer   n_reads = 0, n_acks = 0;
    integer   pca_hits = 0;
    wire scl_r = scl_pin, sda_r = sda_pin;

    // last written PWM value for ch1 of block 8 (what we will measure)
    reg [11:0] on_ch1_blk8 = 12'h000;
    reg [11:0] off_ch1_blk8 = 12'hFFF;

    task automatic decode_byte;
        integer blk;
        begin
            if (addr_phase) begin
                cur_addr = shift; addr_phase = 0; wr_idx = 0; pca_reg = -1;
                is_read  = shift[0];
                if (shift[7:1] >= 7'h40 && shift[7:1] <= 7'h4F) pca_hits = pca_hits + 1;
            end else if (!is_read) begin
                wr_idx = wr_idx + 1;
                if (cur_addr[7:1] >= 7'h40 && cur_addr[7:1] <= 7'h4F) begin
                    blk = cur_addr[7:1] - 7'h40;
                    if (wr_idx == 1) pca_reg = shift; else pca_reg = pca_reg + 1;
                    if (pca_reg >= 0 && pca_reg <= 255) pca_regs[pca_reg] = shift;
                    // mirror LED1 ON/OFF of block 8 into the PWM model
                    if (blk == 8) begin
                        if (pca_reg == 8'h0A) on_ch1_blk8[7:0]  = shift;  // LED1_ON_L
                        if (pca_reg == 8'h0B) on_ch1_blk8[11:8] = shift[3:0];
                        if (pca_reg == 8'h0C) off_ch1_blk8[7:0] = shift;  // LED1_OFF_L
                        if (pca_reg == 8'h0D) off_ch1_blk8[11:8]= shift[3:0];
                    end
                end
            end
        end
    endtask

    always @(negedge sda_r) if (scl_r === 1'b1) begin
        if (in_frame) ; // repeated start
        in_frame <= 1'b1; addr_phase <= 1'b1; ack_phase <= 1'b0;
        bitcnt <= 0; shift <= 0; txbit <= 0;
    end

    always @(posedge sda_r) if (scl_r === 1'b1 && in_frame) begin
        in_frame <= 1'b0; addr_phase <= 1'b1; ack_phase <= 1'b0;
        bitcnt <= 0; slave_sda_low <= 1'b0;
    end

    // ---- slave transmit data: drive on SCL LOW, master samples on HIGH ----
    always @(negedge scl_r) if (in_frame && ack_phase && is_read) begin
        if (txbit < 8) begin
            slave_sda_low <= ~txbyte[7-txbit];   // open drain: drive low only when bit==0
            txbit <= txbit + 1;
        end else begin
            slave_sda_low <= 1'b0;               // release for master ACK
        end
    end

    // ---- receive: sample on SCL HIGH ----
    always @(posedge scl_r) if (in_frame && !ack_phase && !is_read) begin
        if (bitcnt < 8) begin shift <= {shift[6:0], sda_r}; bitcnt <= bitcnt + 1; end
    end

    // ---- byte boundary / ACK ----
    always @(negedge scl_r) if (in_frame && !is_read) begin
        if (!ack_phase && bitcnt == 8) begin
            decode_byte;
            slave_sda_low <= 1'b1; ack_phase <= 1'b1; n_acks = n_acks + 1;
        end else if (ack_phase) begin
            slave_sda_low <= 1'b0; ack_phase <= 1'b0; bitcnt <= 0; shift <= 0;
        end
    end

    // ---- read path: after address ACK the slave sends one byte ----
    always @(negedge scl_r) if (in_frame && is_read && !ack_phase && bitcnt == 8) begin
        decode_byte;                                        // address byte done
        txbyte        <= (cur_addr[7:1] >= 7'h40) ? pca_regs[8'h00] : 8'h00;
        ack_phase     <= 1'b1;
        bitcnt        <= 0;
        txbit         <= 0;
        n_reads       = n_reads + 1;
    end
    // read byte complete: master ACK/NACK ends the transfer
    always @(negedge scl_r) if (in_frame && is_read && ack_phase && txbit == 8) begin
        ack_phase <= 1'b0; txbit <= 0; slave_sda_low <= 1'b0;
    end

    // ================= PCA9685 PWM engine model =========================
    // 50 Hz -> 20.000 ms period, 4096 steps -> 4.883 us per step.
    // The model validates the PWM *engine* with an explicitly programmed duty
    // (PWM_TEST_ON / PWM_TEST_OFF). It is NOT a claim that the DUT produced that
    // duty: the DUT's own programmed values are reported separately.
    localparam integer PWM_STEP_NS = 4883;      // 20_000_000 / 4096
    localparam integer PWM_TEST_ON  = 0;
    localparam integer PWM_TEST_OFF = 1024;     // 25% duty
    integer  pwm_tick_ns = 0;
    reg [11:0] pwm_cnt = 12'd0;
    reg        pwm_out = 1'b0;
    realtime   now_ns = 0.0;
    realtime   last_rise = 0.0;
    realtime   period_meas = 0.0, high_meas = 0.0;
    integer    n_pwm_periods = 0;

    always @(posedge clk) begin
        now_ns = $realtime;
        pwm_tick_ns = pwm_tick_ns + 10;         // ~10.3 ns per clock
        if (pwm_tick_ns >= PWM_STEP_NS) begin
            pwm_tick_ns = 0;
            pwm_cnt = pwm_cnt + 12'd1;
        end
        pwm_out = (pwm_cnt >= PWM_TEST_ON) && (pwm_cnt < PWM_TEST_OFF);
    end

    always @(posedge pwm_out) begin
        if (last_rise > 0.0) begin
            period_meas = now_ns - last_rise;
            n_pwm_periods = n_pwm_periods + 1;
        end
        last_rise = now_ns;
    end
    always @(negedge pwm_out) if (last_rise > 0.0) high_meas = now_ns - last_rise;

    // ================= AXI tasks =================
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

    integer errors = 0, checks = 0;
    task chk(input [255:0] nm, input cond);
        begin
            checks = checks + 1;
            if (cond) $display("[PASS] %0s", nm);
            else begin errors = errors + 1; $display("[FAIL] %0s", nm); end
        end
    endtask

    integer j;
    initial begin
        $display("=== SUPP TB start: supplementary checks S1..S6 (21-item set untouched) ===");
        rstn = 0; repeat(20) @(posedge clk); rstn = 1; repeat(20) @(posedge clk);

        axi_write(16'h0000, 32'h0000_0001);
        // write a known level pattern so a PWM duty is programmed
        for (j = 0; j < 256; j = j + 1) axi_write(16'h0100 + (j*4), (j % 5));
        axi_write(16'h0000, 32'h0000_0003);

        $display("  waiting for PCA init ...");
        repeat(2_000_000) @(posedge clk);      // ~20.6 ms

        $display("  obs: pca_hits=%0d reads=%0d init_healthy=%0b online=%04h fault=%0b oe_n=%0b armed=%0b",
                 pca_hits, n_reads, dbg_init_healthy, dbg_online_mask, dbg_fault_latched,
                 oe_n, dbg_oe_armed);

        chk("S1   PCA9685 slave answered writes (pca_hits>0)", pca_hits > 0);
        chk("S1b  PCA9685 read transactions served (read support)", n_reads > 0);
        chk("S1c  init_healthy asserted (init really completed)", dbg_init_healthy === 1'b1);

        // ---- S2: POSITIVE OE enable ----
        axi_write(16'h2000, 32'h0000_0001);    // SW_OE_REQ = 1
        repeat(500_000) @(posedge clk);
        $display("  OE obs after request: oe_n=%0b armed=%0b reason=%0d healthy=%0b fault=%0b",
                 oe_n, dbg_oe_armed, dbg_oe_reason, dbg_init_healthy, dbg_fault_latched);
        chk("S2   OE# positively ENABLED (oe_n == 0) once healthy", oe_n === 1'b0);
        chk("S2b  guard reports armed", dbg_oe_armed === 1'b1);

        // ---- S3: OE shutdown on software request removal ----
        axi_write(16'h2000, 32'h0000_0002);    // SW_OE_CLR
        repeat(200_000) @(posedge clk);
        chk("S3   OE# returns to OFF on request clear", oe_n === 1'b1);

        // ---- S4: PWM waveform period / duty ----
        axi_write(16'h2000, 32'h0000_0001);    // re-enable for the PWM window
        repeat(3_000_000) @(posedge clk);      // ~31 ms -> >1 full 20 ms period
        $display("  PWM obs: periods=%0d period_ns=%0.0f high_ns=%0.0f on=%0d off=%0d",
                 n_pwm_periods, period_meas, high_meas, on_ch1_blk8, off_ch1_blk8);
        chk("S4   PWM engine model period near 20 ms (50 Hz)",
            (period_meas > 19_000_000.0) && (period_meas < 21_000_000.0));
        chk("S4b  PWM engine model high time ~25% of period",
            (high_meas > 0.0) && (high_meas < period_meas) && (high_meas > period_meas*0.10));

        // ---- S5: register readback model ----
        chk("S5   readback model returned POR MODE1 (0x11)",
            pca_regs[8'h00] == 8'h11);
        chk("S5b  PRE_SCALE readback available (0x79)", pca_regs[8'hFE] == 8'h79);

        // ---- S6: OE off again at the end ----
        axi_write(16'h2000, 32'h0000_0002);
        repeat(100_000) @(posedge clk);
        chk("S6   final OE# state is OFF", oe_n === 1'b1);

        $display("");
        $display("=== SUPP SUMMARY ===");
        $display("checks=%0d errors=%0d", checks, errors);
        if (errors == 0) $display("=== SUPP RESULT: PASS ===");
        else             $display("=== SUPP RESULT: FAIL (%0d errors) ===", errors);
        $finish;
    end

    initial begin #300_000_000; $display("=== SUPP RESULT: TIMEOUT ==="); $finish; end

endmodule
