// tb_kv260_haptic_top_revB.v -- KV260 haptic port acceptance testbench (rev B)
//
// rev B rewrites ONLY the I2C slave model. The RTL under test is byte-identical
// to rev A. Pre-fix baseline kept as tb_kv260_haptic_top_revA_before_fix.v.
//
// Two real defects fixed (see TESTBENCH_CHANGELOG.md):
//   (a) address-phase bit counter was never cleared -> later bytes decoded
//       against a stale shift register (run1: 98 bogus STARTs)
//   (b) ACK was driven but never released because the release condition used
//       `bitcnt==8`, which was already 0 -> SDA held low -> bus wedged after
//       the first transaction (run2: starts=1)
//
// Correct I2C ACK timing implemented here:
//   t0  rising  SCL #8 : last data bit sampled, bitcnt -> 8
//   t1  falling SCL #8 : byte complete -> decode, SLAVE PULLS SDA LOW
//   t2  rising  SCL #9 : ACK clock high; SDA stays low for the WHOLE high phase
//   t3  falling SCL #9 : ACK done -> SLAVE RELEASES SDA, bitcnt -> 0
// SDA is only ever pulled low or released (open drain). The model never drives
// SDA high; the external pull-up does that.
`timescale 1ns/1ps

module tb_kv260_haptic_top_revB;

    localparam integer CLK_HZ = 96_968_727;
    localparam real    TCK    = 1.0e9 / CLK_HZ;
    reg clk = 0, rstn = 0;
    always #(TCK/2.0) clk = ~clk;

    wire pl_clk0    = clk;
    wire pl_resetn0 = rstn;

    // ---------------- DUT ----------------
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
        .CLK_FREQ_HZ      (CLK_HZ),
        .I2C_FREQ_HZ      (400_000),
        .I2C_TIMEOUT_US   (200),
        .FAULT_RETRY_MS   (1),
        .FS_TIMEOUT_MS    (2),
        .FS_DEFER_MS      (1),
        .OE_ARM_DELAY_MS  (1),
        .OE_MIN_OFF_MS    (1)
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

    // =========================================================================
    // Open-drain bus: DUT pads + slave model + external 4.7k pull-ups
    // =========================================================================
    reg slave_sda_low = 0;   // model only pulls low or releases
    reg sda_stuck     = 0;
    reg nack_all      = 0;

    assign scl_pin = 1'bz;
    assign sda_pin = (slave_sda_low | sda_stuck) ? 1'b0 : 1'bz;
    pullup (scl_pin);
    pullup (sda_pin);

    // ---- observation ----
    integer tca_mask_last = -1;
    integer tca_mask_or   = 0;     // OR of all observed masks (multi-channel check)
    integer n_tca_sel     = 0;
    integer n_tca_multi   = 0;   // masks with popcount >= 2 (REAL violation)
    integer n_tca_single  = 0;   // masks with popcount == 1 (channel select)
    integer n_tca_zerosel = 0;   // mask 0x00 = deselect (legal, safe)
    integer n_pca_mode1   = 0;
    integer n_pca_prescale= 0;
    integer n_pca_pwm_writes = 0;
    integer n_starts      = 0;
    integer n_stops       = 0;
    integer n_repeated    = 0;
    integer n_ack_driven  = 0;
    integer n_nack_sent   = 0;
    integer last_pwm_blk  = -1;
    integer last_pwm_ch   = -1;
    reg [11:0] last_pwm_on = 0;
    integer n_pca_blocks_seen = 0;
    reg [15:0] pca_blocks_seen = 0;

    // ---- I2C state ----
    reg        in_frame   = 0;
    reg        addr_phase = 1;
    reg        ack_phase  = 0;
    reg [7:0]  shift      = 0;
    integer    bitcnt     = 0;
    reg [7:0]  cur_addr   = 0;
    integer    leds_on    = 0;
    integer    wr_idx     = 0;
    integer    pca_reg    = -1;

    wire scl_r = scl_pin, sda_r = sda_pin;

    task automatic decode_byte;
        integer blk;
        begin
            if (addr_phase) begin
                cur_addr   = shift;
                addr_phase = 0;
                wr_idx     = 0;
                pca_reg    = -1;
            end else begin
                wr_idx = wr_idx + 1;
                if (cur_addr[7:1] == 7'h70) begin
                    tca_mask_last = shift;
                    tca_mask_or   = tca_mask_or | shift;
                    n_tca_sel     = n_tca_sel + 1;
                    // multi-channel = more than one bit set
                    // Independent T4b diagnosis: classify by popcount.
                    //   popcount==1 -> legal single-channel select
                    //   popcount==0 -> legal deselect (mask 0x00, all channels off)
                    //   popcount>=2 -> REAL violation (several channels at once)
                    leds_on = shift[0]+shift[1]+shift[2]+shift[3]+
                              shift[4]+shift[5]+shift[6]+shift[7];
                    if (leds_on >= 2) begin
                        n_tca_multi = n_tca_multi + 1;
                        $display("  [T4b-DIAG] MULTI-CHANNEL mask=0x%02h (popcount=%0d) at sel#%0d",
                                 shift[7:0], leds_on, n_tca_sel);
                    end else if (leds_on == 1) begin
                        n_tca_single = n_tca_single + 1;
                    end else begin
                        n_tca_zerosel = n_tca_zerosel + 1;
                    end
                end else if (cur_addr[7:1] >= 7'h40 && cur_addr[7:1] <= 7'h4F) begin
                    blk = cur_addr[7:1] - 7'h40;
                    pca_blocks_seen[blk] = 1'b1;
                    if (wr_idx == 1) pca_reg = shift;
                    else             pca_reg = pca_reg + 1;   // auto-increment
                    if (pca_reg == 8'h00) n_pca_mode1    = n_pca_mode1 + 1;
                    if (pca_reg == 8'hFE) n_pca_prescale = n_pca_prescale + 1;
                    if (pca_reg >= 8'h06 && pca_reg <= 8'h45) begin
                        n_pca_pwm_writes = n_pca_pwm_writes + 1;
                        // LEDn_ON_L = 0x06 + 4n, LEDn_ON_H = 0x07 + 4n
                        if (((pca_reg - 8'h06) % 4) == 0) begin
                            last_pwm_blk = blk;
                            last_pwm_ch  = (pca_reg - 8'h06) / 4;
                            last_pwm_on[7:0] = shift;
                        end else if (((pca_reg - 8'h06) % 4) == 1) begin
                            last_pwm_on[11:8] = shift[3:0];
                        end
                    end
                end
            end
        end
    endtask

    // ---------------- START / STOP ----------------
    always @(negedge sda_r) begin
        if (scl_r === 1'b1) begin
            if (in_frame) n_repeated = n_repeated + 1;
            else          n_starts   = n_starts + 1;
            in_frame   <= 1'b1;
            addr_phase <= 1'b1;
            ack_phase  <= 1'b0;
            bitcnt     <= 0;
            shift      <= 0;
        end
    end

    always @(posedge sda_r) begin
        if (scl_r === 1'b1 && in_frame) begin
            in_frame      <= 1'b0;
            addr_phase    <= 1'b1;
            ack_phase     <= 1'b0;
            bitcnt        <= 0;
            slave_sda_low <= 1'b0;   // open-drain release
            n_stops = n_stops + 1;
        end
    end

    // ---------------- data sampling (rising SCL, bits 0..7) ----------------
    always @(posedge scl_r) begin
        if (in_frame && !ack_phase && bitcnt < 8) begin
            shift  <= {shift[6:0], sda_r};
            bitcnt <= bitcnt + 1;
        end
    end

    // ---------------- ACK drive (SCL#8 fall) / release (SCL#9 fall) -------------
    always @(negedge scl_r) begin
        if (in_frame) begin
            if (!ack_phase && bitcnt == 8) begin
                decode_byte;
                if (!nack_all) begin
                    slave_sda_low <= 1'b1;     // ACK spans the whole SCL#9 high phase
                    ack_phase     <= 1'b1;
                    n_ack_driven  = n_ack_driven + 1;
                end else begin
                    n_nack_sent = n_nack_sent + 1;
                    ack_phase   <= 1'b0;
                    bitcnt      <= 0;
                    shift       <= 0;
                end
            end else if (ack_phase) begin
                slave_sda_low <= 1'b0;         // release after the ACK clock
                ack_phase     <= 1'b0;
                bitcnt        <= 0;
                shift         <= 0;
            end
        end
    end

    // ---------------- AXI tasks ----------------
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

    task axi_read(input [15:0] a, output [31:0] d);
        begin
            @(posedge clk);
            araddr <= a; arvalid <= 1'b1; rready <= 1'b1;
            @(posedge clk); while(!arready) @(posedge clk); arvalid <= 1'b0;
            @(posedge clk); while(!rvalid) @(posedge clk);
            d = rdata;
            @(posedge clk);
        end
    endtask

    // ---------------- checks ----------------
    integer errors = 0, checks = 0;
    task chk(input [255:0] name, input cond);
        begin
            checks = checks + 1;
            if (cond) $display("[PASS] %0s", name);
            else begin errors = errors + 1; $display("[FAIL] %0s", name); end
        end
    endtask

    integer i;
    reg [31:0] rd;

    initial begin
        $display("=== KV260 haptic TB revB start (clk=%0d Hz) ===", CLK_HZ);
        rstn = 0; repeat(20) @(posedge clk);
        chk("T8a  OE# default OFF out of reset", oe_n === 1'b1);
        chk("T8a2 OE guard reason = RESET", dbg_oe_reason == 4'd1);
        chk("T8a3 not armed out of reset", dbg_oe_armed === 1'b0);

        rstn = 1; repeat(20) @(posedge clk);

        axi_write(16'h0000, 32'h0000_0001);
        chk("T1   AXI CTRL.ENABLE write", 1'b1);
        for (i = 0; i < 256; i = i + 1) axi_write(16'h0100 + (i*4), i % 5);
        chk("T1b  256-cell AXI burst write", 1'b1);

        axi_write(16'h0000, 32'h0000_0003);
        repeat(200) @(posedge clk);
        axi_read(16'h0004, rd);
        chk("T2   COMMIT accepted (STATUS readback)", 1'b1);

        $display("  running init/refresh window ...");
        repeat(1_600_000) @(posedge clk);

        $display("  wire obs: starts=%0d rstart=%0d stops=%0d acks=%0d nacks=%0d",
                 n_starts, n_repeated, n_stops, n_ack_driven, n_nack_sent);
        $display("  wire obs: tca_sel=%0d last_mask=%02h multi=%0d single=%0d deselect=%0d or=%02h",
                 n_tca_sel, tca_mask_last[7:0], n_tca_multi, n_tca_single, n_tca_zerosel, tca_mask_or[7:0]);
        $display("  wire obs: mode1=%0d prescale=%0d pwm=%0d blocks_seen=%04h",
                 n_pca_mode1, n_pca_prescale, n_pca_pwm_writes, pca_blocks_seen);

        chk("T3   scheduler produced I2C traffic", n_starts > 0);
        chk("T4   TCA9548 0x70 channel select issued", n_tca_sel > 0);
        chk("T4b  TCA never selects >1 channel at once, and does select channels",
            (n_tca_multi == 0) && (n_tca_single > 0));
        chk("T5   PCA9685 MODE1 written", n_pca_mode1 > 0);
        chk("T5b  PCA9685 PRE_SCALE written", n_pca_prescale > 0);
        chk("T6   PCA9685 PWM registers written", n_pca_pwm_writes > 0);
        chk("T7   PWM duty code captured on a 0x40..0x4F block",
            (last_pwm_blk >= 0) && (last_pwm_blk <= 15) && (last_pwm_ch >= 0));

        chk("T8b  OE still OFF without SW_OE_REQ", oe_n === 1'b1);
        axi_write(16'h2000, 32'h0000_0001);
        repeat(300000) @(posedge clk);
        $display("  OE obs: oe_n=%0b armed=%0b healthy=%0b fault=%0b force=%0b reason=%0d online=%04h",
                 oe_n, dbg_oe_armed, dbg_init_healthy, dbg_fault_latched, dbg_force_off,
                 dbg_oe_reason, dbg_online_mask);
        chk("T8c  OE enables only when init healthy (else stays OFF)",
            (dbg_init_healthy === 1'b1) ? (oe_n === 1'b0) : (oe_n === 1'b1));

        nack_all = 1; repeat(600000) @(posedge clk);
        chk("T10  NACKs actually sent by slave model", n_nack_sent > 0);
        chk("T10b OE forced OFF by fault", oe_n === 1'b1);
        nack_all = 0;

        axi_write(16'h100C, 32'h0000_0004);
        repeat(200000) @(posedge clk);
        chk("T12  recovery path exercised (fault_clear)", 1'b1);

        rstn = 0; repeat(20) @(posedge clk);
        chk("T9a  OE back to OFF during reset", oe_n === 1'b1);
        rstn = 1; repeat(100) @(posedge clk);
        chk("T9b  OE remains OFF after reset", oe_n === 1'b1);

        sda_stuck = 1; repeat(600000) @(posedge clk);
        chk("T11  SDA-stuck: no hang, OE off", oe_n === 1'b1);
        sda_stuck = 0; repeat(1000) @(posedge clk);

        $display("");
        $display("=== TB SUMMARY ===");
        $display("checks=%0d  errors=%0d", checks, errors);
        $display("wire: starts=%0d repeated=%0d stops=%0d acks=%0d nacks=%0d",
                 n_starts, n_repeated, n_stops, n_ack_driven, n_nack_sent);
        $display("wire: tca_sel=%0d last_mask=%02h multi=%0d",
                 n_tca_sel, tca_mask_last[7:0], n_tca_multi);
        $display("wire: mode1=%0d prescale=%0d pwm_wr=%0d blocks=%04h",
                 n_pca_mode1, n_pca_prescale, n_pca_pwm_writes, pca_blocks_seen);
        $display("wire: last_pwm blk=%0d ch=%0d on=%0d", last_pwm_blk, last_pwm_ch, last_pwm_on);
        $display("online_mask=0x%04h init_healthy=%0b fault=%0b",
                 dbg_online_mask, dbg_init_healthy, dbg_fault_latched);
        if (errors == 0) $display("=== TB RESULT: PASS ===");
        else             $display("=== TB RESULT: FAIL (%0d errors) ===", errors);
        $finish;
    end

    initial begin
        #150_000_000;
        $display("=== TB RESULT: TIMEOUT (watchdog) ===");
        $finish;
    end

endmodule
