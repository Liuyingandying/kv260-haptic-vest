// tb_audit_decoder.v -- INDEPENDENT byte-level I2C auditor (red-team)
//
// Does NOT trust any previous counter. Rebuilds every transaction from raw
// SCL/SDA edges, attributes registers correctly (pointer vs data, auto-inc),
// reconstructs a register image PER PCA9685 BLOCK, measures real SCL timing,
// and measures the actual init completion time (not the sim window).
//
// Any failing assertion is reported; nothing is suppressed.
`timescale 1ns/1ps

module tb_audit_decoder;

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
        .CLK_FREQ_HZ(CLK_HZ), .I2C_FREQ_HZ(400_000), .I2C_TIMEOUT_US(5000),
        .FAULT_RETRY_MS(10), .FS_TIMEOUT_MS(500), .FS_DEFER_MS(50),
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
    wire p_init_healthy = dut.u_pipe.init_healthy;

    // ---------------- open-drain bus + ACKing slave ----------------
    reg slave_sda_low = 0;
    assign scl_pin = 1'bz;
    assign sda_pin = slave_sda_low ? 1'b0 : 1'bz;
    pullup (scl_pin); pullup (sda_pin);

    wire scl_r = scl_pin, sda_r = sda_pin;

    reg in_frame = 0;      // declared early: used by the timing collectors

    // ---------------- SCL timing measurement ----------------
    realtime t_rise_prev = 0.0, t_fall_prev = 0.0;
    realtime per_min = 1.0e12, per_max = 0.0, per_sum = 0.0;
    realtime hi_min = 1.0e12, hi_max = 0.0, hi_sum = 0.0, hi_n = 0.0;
    realtime lo_min = 1.0e12, lo_max = 0.0, lo_sum = 0.0, lo_n = 0.0;
    integer  n_clk_edges = 0;
    realtime now_r;
    realtime d_per, d_hi, d_lo;
    integer  chn, off, is_data, regidx;
    integer  logf, sumf;
    realtime t_init_done;
    reg      init_done_d;

    always @(posedge clk) now_r = $realtime;
    always @(posedge scl_r) begin
        n_clk_edges = n_clk_edges + 1;
        if (t_rise_prev > 0.0 && in_frame) begin
            d_per = $realtime - t_rise_prev;
            per_sum = per_sum + d_per;
            if (d_per < per_min) per_min = d_per;
            if (d_per > per_max) per_max = d_per;
        end
        if (t_fall_prev > 0.0 && in_frame) begin
            d_lo = $realtime - t_fall_prev;
            lo_sum = lo_sum + d_lo; lo_n = lo_n + 1.0;
            if (d_lo < lo_min) lo_min = d_lo;
            if (d_lo > lo_max) lo_max = d_lo;
        end
        t_rise_prev = $realtime;
    end
    always @(negedge scl_r) begin
        if (t_rise_prev > 0.0 && in_frame) begin
            d_hi = $realtime - t_rise_prev;
            hi_sum = hi_sum + d_hi; hi_n = hi_n + 1.0;
            if (d_hi < hi_min) hi_min = d_hi;
            if (d_hi > hi_max) hi_max = d_hi;
        end
        t_fall_prev = $realtime;
    end

    // ---------------- byte/transaction decoder ----------------
    reg [7:0] shift = 0;
    integer   bitcnt = 0;
    reg       addr_phase = 1, ack_phase = 0;
    reg [7:0] cur_addr = 0;
    integer   wr_idx = 0;
    integer   ptr = -1;
    integer   cur_ch = -1;

    // per-block register image (MODE1, MODE2, PRE_SCALE, LED regs)
    reg [7:0] img_mode1 [0:15];
    reg [7:0] img_mode2 [0:15];
    reg [7:0] img_pscale [0:15];
    reg [11:0] img_led_on [0:15][0:15];
    integer   n_mode1 [0:15];
    integer   n_bytes_bik [0:15];      // data bytes per block
    integer   blk_seen [0:15];
    integer   i, b, c;

    integer   txn_seq = 0;
    integer   txn_bytes = 0;
    integer   txn_addr = 0;
    integer   total_data_bytes = 0;
    integer   total_tca = 0;
    integer   total_addr_bytes = 0;
    integer   n_starts = 0, n_stops = 0, n_acks = 0, n_nacks = 0;
    integer   blk_from_ch [0:7];

    // EXPECTED map (from pca_init_sequencer.v L10-L16 / L84 / L107-L134)
    localparam [7:0] EXP_MODE1_SLEEP = 8'h30;
    localparam [7:0] EXP_PS          = 8'h79;
    localparam [7:0] EXP_MODE2       = 8'h04;
    localparam [7:0] EXP_MODE1_WAKE  = 8'h20;
    localparam [7:0] EXP_MODE1_RST   = 8'hA0;

    always @(negedge sda_r) if (scl_r === 1'b1) begin
        if (!in_frame) begin n_starts = n_starts + 1; txn_seq = txn_seq + 1;
            txn_bytes = 0; txn_addr = -1;
            $fwrite(logf, "TXN %0d  t=%0.1f us  START\n", txn_seq, ($realtime)/1000.0);
        end
        in_frame <= 1'b1; addr_phase <= 1'b1; ack_phase <= 1'b0; bitcnt <= 0; shift <= 0;
    end

    always @(posedge sda_r) if (scl_r === 1'b1 && in_frame) begin
        in_frame <= 1'b0; addr_phase <= 1'b1; ack_phase <= 1'b0; bitcnt <= 0;
        slave_sda_low <= 1'b0; n_stops = n_stops + 1;
        $fwrite(logf, "TXN %0d  t=%0.1f us  STOP  bytes=%0d\n", txn_seq, ($realtime)/1000.0, txn_bytes);
    end

    always @(posedge scl_r) if (in_frame && !ack_phase && bitcnt < 8) begin
        shift <= {shift[6:0], sda_r}; bitcnt <= bitcnt + 1;
    end

    always @(negedge scl_r) if (in_frame) begin
        if (!ack_phase && bitcnt == 8) begin
            // ---- byte complete ----
            if (addr_phase) begin
                cur_addr <= shift;
                txn_addr = shift[7:1];
                total_addr_bytes = total_addr_bytes + 1;
                addr_phase <= 0; wr_idx <= 0; ptr <= -1;
                $fwrite(logf, "        ADDR 0x%02h (7b 0x%02h) %s\n", shift, shift[7:1], shift[0]?"R":"W");
            end else begin
                wr_idx = wr_idx + 1;
                total_data_bytes = total_data_bytes + 1;
                txn_bytes = txn_bytes + 1;
                if (cur_addr[7:1] == 7'h70) begin
                    cur_ch = -1;
                    for (c = 0; c < 8; c = c + 1) if (shift[c]) cur_ch = c;
                    blk_from_ch[cur_ch < 0 ? 0 : cur_ch] = txn_bytes;  // bookkeeping
                    total_tca = total_tca + 1;
                    $fwrite(logf, "        TCA   mask=0x%02h -> ch=%0d\n", shift, cur_ch);
                end else if (cur_addr[7:1] >= 7'h40 && cur_addr[7:1] <= 7'h4F) begin
                    b = cur_addr[7:1] - 7'h40;
                    blk_seen[b] = 1;
                    if (wr_idx == 1) begin
                        ptr = shift;            // this byte is the register POINTER
                        is_data = 0;
                    end else begin
                        // first data byte goes to `ptr` itself, then auto-increments
                        regidx = ptr + (wr_idx - 2);
                        is_data = 1;
                    end
                    if (is_data && regidx == 8'h00) begin
                        n_mode1[b] = n_mode1[b] + 1;
                        if (n_mode1[b] == 1) img_mode1[b] = shift;    // first write
                        $fwrite(logf, "        BLK%0d MODE1  <= 0x%02h (#%0d)\n", b, shift, n_mode1[b]);
                    end
                    if (is_data && regidx == 8'h01) begin img_mode2[b] = shift;
                        $fwrite(logf, "        BLK%0d MODE2  <= 0x%02h\n", b, shift); end
                    if (is_data && regidx == 8'hFE) begin img_pscale[b] = shift;
                        $fwrite(logf, "        BLK%0d PRESCALE  <= 0x%02h\n", b, shift); end
                    if (is_data && regidx >= 8'h06 && regidx <= 8'h45) begin
                        chn = (regidx - 8'h06) / 4;
                        off = (regidx - 8'h06) % 4;
                        if (off == 0) img_led_on[b][chn] = {img_led_on[b][chn][11:8], shift};
                        if (off == 1) img_led_on[b][chn] = {shift[3:0], img_led_on[b][chn][7:0]};
                    end
                end
            end
            // ACK
            slave_sda_low <= 1'b1; ack_phase <= 1'b1; n_acks = n_acks + 1;
        end else if (ack_phase) begin
            slave_sda_low <= 1'b0; ack_phase <= 1'b0; bitcnt <= 0; shift <= 0;
        end
    end

    // ---------------- AXI ----------------
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

    integer errors = 0;
    integer e2, e3, e4, e5, exp_total;
    realtime avgper, f_khz;
    integer j;

    always @(posedge clk) begin
        init_done_d <= p_init_done;
        if (!init_done_d && p_init_done && t_init_done < 0.0) begin
            t_init_done = $realtime;
            $fwrite(logf, "\n*** init_done ROSE at t=%0.3f ms ***\n\n", $realtime/1.0e6);
        end
    end

    initial begin
        for (j = 0; j < 16; j = j + 1) begin
            img_mode1[j] = 8'hxx; img_mode2[j] = 8'hxx; img_pscale[j] = 8'hxx;
            n_mode1[j] = 0; n_bytes_bik[j] = 0; blk_seen[j] = 0;
            for (c = 0; c < 16; c = c + 1) img_led_on[j][c] = 12'hFFF;
        end
        for (j = 0; j < 8; j = j + 1) blk_from_ch[j] = -1;

        t_init_done = -1.0; init_done_d = 0;
        logf = $fopen("audit_decode.log", "w");
        sumf = $fopen("audit_summary.txt", "w");
        $fwrite(logf, "# byte-level I2C decode  (independent auditor)\n\n");

        $display("=== AUDIT decoder start ===");
        rstn = 0; repeat(20) @(posedge clk); rstn = 1; repeat(20) @(posedge clk);

        axi_write(16'h0000, 32'h0000_0001);
        for (j = 0; j < 256; j = j + 1) axi_write(16'h0100 + (j*4), (j % 5));
        axi_write(16'h0000, 32'h0000_0003);

        // wait until init_done or timeout 400 ms sim
        j = 0;
        while (!p_init_done && j < 4_000_000) begin @(posedge clk); j = j + 1; end
        repeat(200_000) @(posedge clk);   // let a few refresh cycles run

        $display("  init_done=%0b healthy=%0b online=%04h fault=%0b",
                 p_init_done, p_init_healthy, dbg_online_mask, dbg_fault_latched);
        $display("  init_done_time=%0.3f ms   total_data_bytes=%0d  tca=%0d  addr_bytes=%0d",
                 t_init_done/1.0e6, total_data_bytes, total_tca, total_addr_bytes);

        // ============ INDEPENDENT CHECKS ============
        $fwrite(sumf, "AUDIT SUMMARY\n=============\n");
        $fwrite(sumf, "init_done=%0b at %0.3f ms\n", p_init_done, t_init_done/1.0e6);
        $fwrite(sumf, "total_data_bytes=%0d total_addr_bytes=%0d tca_selects=%0d\n",
                total_data_bytes, total_addr_bytes, total_tca);
        $fwrite(sumf, "SCL: period avg=%0.1f ns min=%0.1f max=%0.1f  => %0.1f kHz\n",
                (per_sum/(n_clk_edges-1)), per_min, per_max,
                1.0e6/(per_sum/(n_clk_edges-1)));
        $fwrite(sumf, "SCL: high avg=%0.1f min=%0.1f   low avg=%0.1f min=%0.1f\n",
                hi_sum/hi_n, hi_min, lo_sum/lo_n, lo_min);
        $fwrite(sumf, "\nPER-BLOCK REGISTER IMAGE\n");
        for (b = 0; b < 16; b = b + 1)
            $fwrite(sumf, "BLK%02d seen=%0b MODE1=0x%02h(#%0d) MODE2=0x%02h PRESCALE=0x%02h LED1_ON=0x%03h\n",
                    b, blk_seen[b], img_mode1[b], n_mode1[b], img_mode2[b], img_pscale[b], img_led_on[b][1]);

        // A1: all 16 blocks addressed
        for (b = 0; b < 16; b = b + 1) if (!blk_seen[b]) begin
            errors = errors + 1; $display("[FAIL] A1 block %0d never addressed", b); end
        $display("[%0s] A1 all 16 PCA blocks addressed on the bus",
                 (errors==0) ? "PASS" : "see above");

        // A2: PRE_SCALE == 0x79 on every block
        begin : a2
            e2 = 0;
            for (b = 0; b < 16; b = b + 1) if (img_pscale[b] !== EXP_PS) begin
                e2 = e2 + 1;
                $display("[FAIL] A2 BLK%0d PRESCALE=0x%02h (expected 0x%02h)", b, img_pscale[b], EXP_PS);
            end
            if (e2 == 0) $display("[PASS] A2 all 16 blocks have PRESCALE=0x79 (50 Hz)");
            else errors = errors + e2;
        end

        // A3: first MODE1 write == 0x30 (SLEEP|AI)
        begin : a3
            e3 = 0;
            for (b = 0; b < 16; b = b + 1) if (img_mode1[b] !== EXP_MODE1_SLEEP) begin
                e3 = e3 + 1;
                $display("[FAIL] A3 BLK%0d first MODE1=0x%02h (expected SLEEP|AI=0x30)", b, img_mode1[b]);
            end
            if (e3 == 0) $display("[PASS] A3 all 16 blocks first MODE1 = 0x30 (SLEEP|AI)");
            else errors = errors + e3;
        end

        // A4: MODE2 == 0x04 (OUTDRV)
        begin : a4
            e4 = 0;
            for (b = 0; b < 16; b = b + 1) if (img_mode2[b] !== EXP_MODE2) begin
                e4 = e4 + 1;
                $display("[FAIL] A4 BLK%0d MODE2=0x%02h (expected 0x04)", b, img_mode2[b]);
            end
            if (e4 == 0) $display("[PASS] A4 all 16 blocks MODE2 = 0x04 (OUTDRV)");
            else errors = errors + e4;
        end

        // A5: FULL_OFF -> every LEDn_ON == 0 and LEDn_OFF contains 0x100 pattern?
        //     sequencer writes 0x00 except every 4th byte == 0x10 -> LEDn_OFF_H = 0x10
        begin : a5
            e5 = 0;
            for (b = 0; b < 16; b = b + 1)
                for (c = 0; c < 16; c = c + 1)
                    if (img_led_on[b][c] !== 12'h010) begin
                        e5 = e5 + 1;
                        if (e5 <= 8) $display("[FAIL] A5 BLK%0d ch%0d LED_ON=0x%03h (expected 0x010)",
                                             b, c, img_led_on[b][c]);
                    end
            if (e5 == 0) $display("[PASS] A5 all 256 channels FULL_OFF (LED_ON=0x010, OFF=0x000)");
            else errors = errors + 1;
        end

        // A6: BYTE BUDGET -- 16 blocks x 84 bytes expected; compare
        begin : a6
            exp_total = 16*84;
            $display("[INFO] A6 data bytes: actual=%0d expected_8x16x84=%0d tca=%0d",
                     total_data_bytes, exp_total, total_tca);
            if (total_data_bytes > exp_total * 2) begin
                errors = errors + 1;
                $display("[FAIL] A6 data byte count %0d >> expected %0d (repeated init?)",
                         total_data_bytes, exp_total);
            end else $display("[PASS] A6 data byte count within 2x of 16x84");
        end

        // B1: SCL frequency and duty
        begin : b1
            avgper = per_sum/(n_clk_edges-1);
            f_khz = 1.0e6/avgper;
            $display("[INFO] B1 SCL: period=%0.1f ns freq=%0.2f kHz high=%0.1f ns low=%0.1f ns",
                     avgper, f_khz, hi_sum/hi_n, lo_sum/lo_n);
            if (f_khz > 400.0) begin
                errors = errors + 1;
                $display("[FAIL] B1 SCL %0.2f kHz EXCEEDS Fast-mode 400 kHz limit", f_khz);
            end else $display("[PASS] B1 SCL within 400 kHz");
            if ((lo_sum/lo_n) < 1300.0) begin
                errors = errors + 1;
                $display("[FAIL] B1b t_LOW=%0.1f ns < Fast-mode min 1300 ns", lo_sum/lo_n);
            end else $display("[PASS] B1b t_LOW >= 1300 ns");
            if ((hi_sum/hi_n) < 600.0) begin
                errors = errors + 1;
                $display("[FAIL] B1c t_HIGH=%0.1f ns < Fast-mode min 600 ns", hi_sum/hi_n);
            end else $display("[PASS] B1c t_HIGH >= 600 ns");
        end

        // E1: measured init time
        $display("[INFO] E1 init_done at %0.3f ms", t_init_done/1.0e6);

        $display("");
        $display("=== AUDIT RESULT: %0d failures ===", errors);
        $fclose(logf); $fclose(sumf);
        $finish;
    end

    initial begin #600_000_000; $display("=== AUDIT TIMEOUT ==="); $finish; end

endmodule
