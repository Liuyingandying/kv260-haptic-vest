// tb_phase1e_regmap.v -- Phase 1E: register-level + timing verification
//
// Decoder #1 (this file) rebuilds the full PCA9685 register image from raw bus
// edges and writes BOTH a byte capture (for an independent Python decoder) and
// the reconstructed 256-channel image.
//
// Pointer handling (the bug that produced the earlier false positive):
//   byte after ADDR  = register POINTER (never a data write)
//   byte after that  = data for regidx = ptr + (wr_idx-2)   (auto-increment)
`timescale 1ns/1ps

module tb_phase1e_regmap;

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
        .CLK_FREQ_HZ(CLK_HZ),
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
    wire p_init_healthy = dut.u_pipe.init_healthy;

    // ---------- open-drain bus + ACKing slave ----------
    reg slave_sda_low = 0;
    assign scl_pin = 1'bz;
    assign sda_pin = slave_sda_low ? 1'b0 : 1'bz;
    pullup (scl_pin); pullup (sda_pin);
    wire scl_r = scl_pin, sda_r = sda_pin;

    // ================= timing measurement =================
    realtime t_scl_rise = 0.0, t_scl_fall = 0.0, t_sda_edge = 0.0;
    realtime per_sum = 0.0, hi_sum = 0.0, lo_sum = 0.0, su_sum = 0.0, hd_sum = 0.0;
    realtime per_min = 1e12, hi_min = 1e12, lo_min = 1e12, su_min = 1e12, hd_min = 1e12;
    realtime sta_min = 1e12, sto_min = 1e12, sta_cur = 0.0, sto_cur = 0.0;
    realtime sta_sum = 0.0, sto_sum = 0.0;
    integer  n_per = 0, n_hi = 0, n_lo = 0, n_su = 0, n_hd = 0, n_sta = 0, n_sto = 0;
    realtime d_x;
    reg      sda_prev = 1'b1, scl_prev = 1'b1;

    always @(scl_r or sda_r) begin
        // SDA transitions
        if (sda_r !== sda_prev) begin
            t_sda_edge = $realtime;
            if (scl_r === 1'b1) begin
                // START (fall) or STOP (rise) while SCL high
                if (sda_r === 1'b0) sta_cur = $realtime;   // START: setup measured to next SCL fall
                else                sto_cur = $realtime;   // STOP: setup measured from last SCL rise
            end
            sda_prev = sda_r;
        end
        // SCL transitions
        if (scl_r !== scl_prev) begin
            if (scl_r === 1'b1) begin
                if (t_scl_rise > 0.0) begin
                    d_x = $realtime - t_scl_rise;  per_sum = per_sum + d_x; n_per = n_per + 1;
                    if (d_x < per_min) per_min = d_x;
                end
                if (t_scl_fall > 0.0) begin
                    d_x = $realtime - t_scl_fall;  lo_sum = lo_sum + d_x; n_lo = n_lo + 1;
                    if (d_x < lo_min) lo_min = d_x;
                end
                // SDA data setup: measured from last SDA edge (must be during SCL low)
                if (t_sda_edge > 0.0 && t_sda_edge >= t_scl_fall) begin
                    d_x = $realtime - t_sda_edge;  su_sum = su_sum + d_x; n_su = n_su + 1;
                    if (d_x < su_min) su_min = d_x;
                end
                // STOP setup: SCL rise -> SDA rise
                if (sto_cur > 0.0) begin
                    d_x = $realtime - sto_cur;  sto_sum = sto_sum + d_x; n_sto = n_sto + 1;
                    if (d_x < sto_min) sto_min = d_x;
                end
                t_scl_rise = $realtime;
            end else begin
                if (t_scl_rise > 0.0) begin
                    d_x = $realtime - t_scl_rise;  hi_sum = hi_sum + d_x; n_hi = n_hi + 1;
                    if (d_x < hi_min) hi_min = d_x;
                end
                // START setup: SDA fall -> SCL fall
                if (sta_cur > 0.0) begin
                    d_x = $realtime - sta_cur;  sta_sum = sta_sum + d_x; n_sta = n_sta + 1;
                    if (d_x < sta_min) sta_min = d_x;
                end
                t_scl_fall = $realtime;
            end
            scl_prev = scl_r;
        end
    end
    // ================= decoder #1: full register image =================
    reg [7:0]  img [0:15][0:255];    // [block][register] live
    reg [7:0]  img_i [0:15][0:255];  // snapshot at init_done
    reg        img_i_v [0:15][0:255];
    reg        init_done_d = 0;
    reg        img_valid [0:15][0:255];
    reg [7:0]  mode1_hist [0:15][0:7];
    integer    n_mode1 [0:15];
    integer    blk_seen [0:15];
    integer    blk_bytes [0:15];
    reg        blk_tca_ch_ok [0:15];

    reg [7:0] shift = 0;
    integer   bitcnt = 0;
    reg       in_frame = 0, addr_phase = 1, ack_phase = 0;
    reg [7:0] cur_addr = 0;
    integer   wr_idx = 0, ptr = -1, regidx = -1;
    integer   ch_cur = -1;
    integer   n_addr_bytes = 0, n_data_bytes = 0, n_tca = 0, n_acks = 0;
    integer   i, b, c, r;
    always @(posedge clk) begin
        init_done_d <= p_init_done;
        if (!init_done_d && p_init_done) begin
            for (b = 0; b < 16; b = b + 1)
                for (r = 0; r < 256; r = r + 1) begin
                    img_i[b][r]   <= img[b][r];
                    img_i_v[b][r] <= img_valid[b][r];
                end
            $display("  [snapshot] register image captured at init_done (t=%0.3f ms)", $realtime/1.0e6);
        end
    end

    integer   capf;
    realtime  t_start_sim;

    task automatic decode_data_byte;
        begin
            if (cur_addr[7:1] == 7'h70) begin
                // TCA select: single data byte = channel mask
                ch_cur = -1;
                for (i = 0; i < 8; i = i + 1) if (shift[i]) ch_cur = i;
                n_tca = n_tca + 1;
                $fwrite(capf, "%0.1f,TCA,0x70,,%02h,%0d,%0d\n",
                        ($realtime - t_start_sim)/1000.0, shift, ch_cur, 1);
            end else if (cur_addr[7:1] >= 7'h40 && cur_addr[7:1] <= 7'h4F) begin
                b = cur_addr[7:1] - 7'h40;
                blk_seen[b] = 1;
                blk_bytes[b] = blk_bytes[b] + 1;
                if (wr_idx == 1) regidx = -1;            // pointer byte, not data
                else             regidx = ptr + (wr_idx - 2);
                $fwrite(capf, "%0.1f,DATA,0x%02h,%0d,%02h,%0d,%0d\n",
                        ($realtime - t_start_sim)/1000.0, cur_addr, ptr, shift, regidx, 1);
                if (regidx >= 0 && regidx <= 255) begin
                    img[b][regidx] = shift;
                    img_valid[b][regidx] = 1'b1;
                    if (regidx == 8'h00) begin
                        if (n_mode1[b] < 8) mode1_hist[b][n_mode1[b]] = shift;
                        n_mode1[b] = n_mode1[b] + 1;
                    end
                end
            end
        end
    endtask

    always @(negedge sda_r) if (scl_r === 1'b1) begin
        if (!in_frame) $fwrite(capf, "%0.1f,START,,,,\n", ($realtime - t_start_sim)/1000.0);
        in_frame <= 1; addr_phase <= 1; ack_phase <= 0; bitcnt <= 0; shift <= 0;
    end
    always @(posedge sda_r) if (scl_r === 1'b1 && in_frame) begin
        in_frame <= 0; addr_phase <= 1; ack_phase <= 0; bitcnt <= 0;
        slave_sda_low <= 1'b0;
        $fwrite(capf, "%0.1f,STOP,,,,\n", ($realtime - t_start_sim)/1000.0);
    end
    always @(posedge scl_r) if (in_frame && !ack_phase && bitcnt < 8) begin
        shift <= {shift[6:0], sda_r}; bitcnt <= bitcnt + 1;
    end
    always @(negedge scl_r) if (in_frame) begin
        if (!ack_phase && bitcnt == 8) begin
            if (addr_phase) begin
                cur_addr <= shift; addr_phase <= 0; wr_idx <= 0; ptr <= -1;
                n_addr_bytes = n_addr_bytes + 1;
                $fwrite(capf, "%0.1f,ADDR,0x%02h,,%02h,,\n",
                        ($realtime - t_start_sim)/1000.0, shift, shift);
            end else begin
                wr_idx = wr_idx + 1;
                if (cur_addr[7:1] == 7'h70) begin
                    // TCA9548A: the single data byte IS the channel mask (no pointer)
                    decode_data_byte;
                    n_data_bytes = n_data_bytes + 1;
                end else if (wr_idx == 1) begin ptr = shift; regidx = -1;
                    $fwrite(capf, "%0.1f,PTR,0x%02h,%0d,%02h,,\n",
                            ($realtime - t_start_sim)/1000.0, cur_addr, ptr, shift);
                end else begin
                    decode_data_byte;
                    n_data_bytes = n_data_bytes + 1;
                end
            end
            slave_sda_low <= 1'b1; ack_phase <= 1'b1; n_acks = n_acks + 1;
        end else if (ack_phase) begin
            slave_sda_low <= 1'b0; ack_phase <= 0; bitcnt <= 0; shift <= 0;
        end
    end

    // ================= AXI =================
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

    integer j, regmapf, e_cnt;
    realtime t_init;

    initial begin
        for (b = 0; b < 16; b = b + 1) begin
            blk_seen[b] = 0; n_mode1[b] = 0; blk_bytes[b] = 0;
            for (r = 0; r < 256; r = r + 1) begin img[b][r] = 8'h00; img_valid[b][r] = 1'b0; end
        end
        t_start_sim = $realtime;
        capf = $fopen("i2c_byte_capture.csv", "w");
        $fwrite(capf, "t_us,event,addr,ptr,data,regidx,ack\n");

        $display("=== PHASE1E regmap verification start (I2C_FREQ_HZ default = 378700 -> TICKS=64) ===");
        rstn = 0; repeat(20) @(posedge clk); rstn = 1; repeat(20) @(posedge clk);

        axi_write(16'h0000, 32'h0000_0001);
        for (j = 0; j < 256; j = j + 1) axi_write(16'h0100 + (j*4), (j % 5));
        axi_write(16'h0000, 32'h0000_0003);

        j = 0;
        while (!p_init_done && j < 6_000_000) begin @(posedge clk); j = j + 1; end
        t_init = $realtime;
        repeat(400_000) @(posedge clk);
        $fclose(capf);

        $display("  init_done=%0b healthy=%0b online=%04h fault=%0b  t_init=%0.3f ms",
                 p_init_done, p_init_healthy, dbg_online_mask, dbg_fault_latched, t_init/1.0e6);

        // ---------- B. timing ----------
        $display("--- B. I2C timing (measured in-frame) ---");
        $display("  SCL period avg=%0.1f ns  => %0.2f kHz   (min %0.1f)",
                 per_sum/n_per, 1.0e9/(per_sum/n_per)/1000.0, per_min);
        $display("  t_HIGH avg=%0.1f ns (min %0.1f)   t_LOW avg=%0.1f ns (min %0.1f)",
                 hi_sum/n_hi, hi_min, lo_sum/n_lo, lo_min);
        $display("  SDA setup min=%0.1f ns   START setup min=%0.1f ns   STOP setup min=%0.1f ns",
                 su_min, sta_min, sto_min);

        chk("B1 SCL frequency <= 400 kHz", (1.0e9/(per_sum/n_per)) <= 400_000.0);
        chk("B2 t_LOW >= 1300 ns", lo_min >= 1300.0);
        chk("B3 t_HIGH >= 600 ns", hi_min >= 600.0);
        chk("B4 SDA data setup >= 100 ns", su_min >= 100.0);
        chk("B5 START setup >= 600 ns", sta_min >= 600.0);
        chk("B6 STOP setup >= 600 ns", sto_min >= 600.0);

        // ---------- C. 16-block addressing ----------
        $display("--- C. block addressing ---");
        e_cnt = 0;
        for (b = 0; b < 16; b = b + 1) if (!blk_seen[b]) begin e_cnt = e_cnt + 1;
            $display("[FAIL] C block %0d never addressed", b); end
        chk("C1 all 16 PCA9685 blocks addressed", e_cnt == 0);

        // ---------- D. PRE_SCALE ----------
        e_cnt = 0;
        for (b = 0; b < 16; b = b + 1) if (!img_i_v[b][8'hFE] || img_i[b][8'hFE] !== 8'h79) begin
            e_cnt = e_cnt + 1;
            $display("[FAIL] D BLK%0d PRE_SCALE=0x%02h valid=%0b", b, img_i[b][8'hFE], img_i_v[b][8'hFE]);
        end
        chk("D1 all 16 PRE_SCALE == 0x79 (121 -> 50.03 Hz)", e_cnt == 0);

        // ---------- E. MODE2 ----------
        e_cnt = 0;
        for (b = 0; b < 16; b = b + 1) if (!img_i_v[b][8'h01] || img_i[b][8'h01] !== 8'h04) begin
            e_cnt = e_cnt + 1;
            $display("[FAIL] E BLK%0d MODE2=0x%02h", b, img_i[b][8'h01]);
        end
        chk("E1 all 16 MODE2 == 0x04 (OUTDRV)", e_cnt == 0);

        // ---------- F. MODE1 sequence & final ----------
        $display("--- F. MODE1 history (per block) ---");
        e_cnt = 0;
        for (b = 0; b < 16; b = b + 1) begin
            $display("  BLK%02d MODE1 writes=%0d  [0]=%02h [1]=%02h [2]=%02h [3]=%02h final@init=%02h final@now=%02h",
                     b, n_mode1[b], mode1_hist[b][0], mode1_hist[b][1], mode1_hist[b][2],
                     mode1_hist[b][3], img_i[b][8'h00], img[b][8'h00]);
            if (mode1_hist[b][0] !== 8'h30) begin e_cnt = e_cnt + 1;
                $display("[FAIL] F BLK%0d first MODE1=0x%02h (expect SLEEP|AI=0x30)", b, mode1_hist[b][0]); end
        end
        chk("F1 all 16 first MODE1 == 0x30 (SLEEP|AI)", e_cnt == 0);

        // ---------- G. 256-channel FULL_OFF ----------
        // Correct FULL_OFF per PCA9685 DS + pca_init_sequencer.v L125:
        //   LEDn_ON_L=0x00, LEDn_ON_H=0x00, LEDn_OFF_L=0x00, LEDn_OFF_H=0x10 (bit4 FULL_OFF)
        $display("--- G. 256-channel FULL_OFF ---");
        e_cnt = 0;
        for (b = 0; b < 16; b = b + 1)
            for (c = 0; c < 16; c = c + 1) begin
                if (!img_i_v[b][8'h06+4*c] || img_i[b][8'h06+4*c] !== 8'h00 ||
                    !img_i_v[b][8'h07+4*c] || img_i[b][8'h07+4*c] !== 8'h00 ||
                    !img_i_v[b][8'h08+4*c] || img_i[b][8'h08+4*c] !== 8'h00 ||
                    !img_i_v[b][8'h09+4*c] || img_i[b][8'h09+4*c] !== 8'h10) begin
                    e_cnt = e_cnt + 1;
                    if (e_cnt <= 12) $display("[FAIL] G BLK%0d ch%0d ON=%02h%02h OFF=%02h%02h (expect 0000/0010)",
                        b, c, img_i[b][8'h07+4*c], img_i[b][8'h06+4*c],
                        img_i[b][8'h09+4*c], img_i[b][8'h08+4*c]);
                end
            end
        chk("G1 all 256 channels FULL_OFF (ON=0x000, OFF_H=0x10)", e_cnt == 0);
        $display("  FULL_OFF violations: %0d / 256", e_cnt);

        // ---------- H. dump image for independent cross-check ----------
        regmapf = $fopen("regmap_256.csv", "w");
        $fwrite(regmapf, "blk,ch,led_on,led_off,mode1,mode2,prescale\n");
        for (b = 0; b < 16; b = b + 1)
            for (c = 0; c < 16; c = c + 1)
                $fwrite(regmapf, "%0d,%0d,%04h,%04h,%02h,%02h,%02h\n", b, c,
                        {img_i[b][8'h07+4*c], img_i[b][8'h06+4*c]},
                        {img_i[b][8'h09+4*c], img_i[b][8'h08+4*c]},
                        img_i[b][8'h00], img_i[b][8'h01], img_i[b][8'hFE]);
        $fclose(regmapf);

        // ---------- I. accounting ----------
        $display("--- I. transaction accounting ---");
        $display("  addr_bytes=%0d data_bytes=%0d acks=%0d tca_selects=%0d",
                 n_addr_bytes, n_data_bytes, n_acks, n_tca);
        $display("  per-block data bytes: BLK0=%0d BLK1=%0d BLK15=%0d",
                 blk_bytes[0], blk_bytes[1], blk_bytes[15]);

        $display("");
        $display("=== PHASE1E RESULT: checks=%0d errors=%0d ===", checks, errors);
        if (errors == 0) $display("=== PHASE1E: PASS ==="); else $display("=== PHASE1E: FAIL ===");
        $finish;
    end
    initial begin #800_000_000; $display("=== PHASE1E TIMEOUT ==="); $finish; end

endmodule
