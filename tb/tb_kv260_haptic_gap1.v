// tb_kv260_haptic_gap1.v -- Gap 1 diagnostic: find the FIRST fault_latched rise.
//
// Testbench only. Zero functional RTL is modified or overridden. All probing is
// read-only hierarchical reference into kv260_haptic_pipeline internals.
//
// Outputs:
//   init_signal_trace.vcd  - waveform (DUT + pipeline + init FSM)
//   init_event_timeline.csv- event log, one row per key event
//   fault_first_trigger.log- full context at the first 0->1 of fault_latched
`timescale 1ns/1ps

module tb_kv260_haptic_gap1;

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

    // ================= read-only hierarchical probes =================
    wire        p_init_done   = dut.u_pipe.init_done;
    wire        p_init_busy   = dut.u_pipe.init_busy;
    wire        p_init_healthy= dut.u_pipe.init_healthy;
    wire        p_init_restart= dut.u_pipe.init_restart;
    wire [15:0] p_online_mask = dut.u_pipe.online_mask;
    wire [15:0] p_init_err    = dut.u_pipe.init_err_mask;
    wire [15:0] p_run_err     = dut.u_pipe.run_err_mask;
    wire [15:0] p_off_pend    = dut.u_pipe.off_pend_mask;
    wire        p_fault_lat   = dut.u_pipe.sw_fault_latched;
    wire        p_fault_clear = dut.u_pipe.fault_clear;
    wire [31:0] p_i2c_err_cnt = dut.u_pipe.i2c_err_cnt;
    wire [31:0] p_off_retry   = dut.u_pipe.off_retry_cnt;
    wire        p_runtime_run = dut.u_pipe.runtime_run;
    wire        p_safety_off  = dut.u_pipe.safety_off;
    wire [2:0]  p_rec_state   = dut.u_pipe.recovery_state;
    wire        p_m_busy      = dut.u_pipe.m_busy;
    wire        p_m_done      = dut.u_pipe.m_txn_done;
    wire        p_m_ack_err   = dut.u_pipe.m_ack_err;
    wire        p_m_timeout   = dut.u_pipe.m_timeout_error;
    wire        p_ar_done     = dut.u_pipe.ar_done;
    wire        p_ar_ack_err  = dut.u_pipe.ar_ack_err;
    wire [31:0] p_i2c_tmo_cnt = dut.u_pipe.i2c_timeout_count;
    // init sequencer
    wire [2:0]  p_init_st     = dut.u_pipe.u_pinit.st;
    wire [3:0]  p_init_blk    = dut.u_pipe.init_blk;
    wire        p_init_pca_tx = dut.u_pipe.init_pca_txn;
    wire        p_pinit_sb_start = dut.u_pipe.pinit_sb_start;
    wire        p_pinit_sb_done  = dut.u_pipe.pinit_sb_done;
    wire        p_pinit_sb_ack   = dut.u_pipe.pinit_sb_ack_err;
    wire [2:0]  p_pinit_sb_kind  = dut.u_pipe.pinit_sb_kind;
    // inject (runtime TCA select)
    wire        p_inj_sb_start = dut.u_pipe.inj_sb_start;
    wire        p_inj_sb_done  = dut.u_pipe.inj_sb_done;
    wire        p_inj_sb_ack   = dut.u_pipe.inj_sb_ack_err;
    wire [3:0]  p_inj_sb_blk   = dut.u_pipe.inj_sb_blk;
    // OE
    wire        p_sw_oe_req    = dut.sw_oe_req;
    wire [3:0]  p_oe_reason    = dut.dbg_oe_reason;

    initial begin
        $dumpfile("init_signal_trace.vcd");
        $dumpvars(1, tb_kv260_haptic_gap1.dut);
    end

    // ================= I2C slave model (revB ACK timing + byte log) =========
    reg slave_sda_low = 0;
    assign scl_pin = 1'bz;
    assign sda_pin = slave_sda_low ? 1'b0 : 1'bz;
    pullup (scl_pin); pullup (sda_pin);

    reg [7:0] shift = 0;
    integer   bitcnt = 0;
    reg       in_frame = 0, addr_phase = 1, ack_phase = 0;
    reg [7:0] cur_addr = 0;
    integer   wr_idx = 0, pca_reg = -1;
    integer   n_start = 0, n_stop = 0, n_ack = 0, n_nack = 0;
    integer   n_tca = 0, n_pca_wr = 0;
    reg [7:0] tca_mask = 8'h00;
    reg [6:0] last_pca = 7'h00;
    reg       nack_mode = 0;   // diagnostic knob (not used in the good run)
    wire scl_r = scl_pin, sda_r = sda_pin;
    integer   csv;
    realtime  t0;

    task automatic csv_row(input [63:0] kind, input [31:0] a, input [31:0] b);
        begin
            $fwrite(csv, "%0.1f,%0s,%0d,%0d,%0d,%02h,%02h,%0d,%0d,%04h,%04h,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d\n",
                    $realtime - t0, kind, a, b,
                    p_init_busy, p_init_st, cur_addr[7:0],
                    p_init_done, p_init_healthy, p_online_mask, p_init_err,
                    p_fault_lat, p_m_ack_err, p_m_timeout, p_ar_ack_err,
                    p_i2c_tmo_cnt, p_i2c_err_cnt, n_nack, p_rec_state);
        end
    endtask

    always @(negedge sda_r) if (scl_r === 1'b1) begin
        if (in_frame) begin end else begin n_start = n_start + 1; csv_row("START", n_start, 0); end
        in_frame <= 1'b1; addr_phase <= 1'b1; ack_phase <= 1'b0; bitcnt <= 0; shift <= 0;
    end
    always @(posedge sda_r) if (scl_r === 1'b1 && in_frame) begin
        in_frame <= 1'b0; addr_phase <= 1'b1; ack_phase <= 1'b0; bitcnt <= 0;
        slave_sda_low <= 1'b0; n_stop = n_stop + 1;
        csv_row("STOP", n_stop, cur_addr[7:0]);
    end
    always @(posedge scl_r) if (in_frame && !ack_phase && bitcnt < 8) begin
        shift <= {shift[6:0], sda_r}; bitcnt <= bitcnt + 1;
    end
    always @(negedge scl_r) if (in_frame) begin
        if (!ack_phase && bitcnt == 8) begin
            if (addr_phase) begin
                cur_addr <= shift; addr_phase <= 0; wr_idx <= 0; pca_reg <= -1;
                if (shift[7:1] >= 7'h40 && shift[7:1] <= 7'h4F) last_pca <= shift[7:1];
                csv_row("ADDR", shift, 0);
            end else begin
                wr_idx = wr_idx + 1;
                if (cur_addr[7:1] == 7'h70) begin
                    tca_mask = shift; n_tca = n_tca + 1;
                    csv_row("TCA", shift, n_tca);
                end else begin
                    if (wr_idx == 1) pca_reg = shift; else pca_reg = pca_reg + 1;
                    n_pca_wr = n_pca_wr + 1;
                    csv_row("PCADATA", {pca_reg[7:0], shift[7:0]}, n_pca_wr);
                end
            end
            if (!nack_mode) begin slave_sda_low <= 1'b1; ack_phase <= 1'b1; n_ack = n_ack + 1; end
            else begin n_nack = n_nack + 1; ack_phase <= 1'b0; bitcnt <= 0; shift <= 0; end
        end else if (ack_phase) begin
            slave_sda_low <= 1'b0; ack_phase <= 1'b0; bitcnt <= 0; shift <= 0;
        end
    end

    // ================= FIRST FAULT CAPTURE =================
    integer  fault_log;
    reg      fault_d = 0;
    integer  first_fault_time = -1;
    always @(posedge clk) begin
        fault_d <= p_fault_lat;
        if (!fault_d && p_fault_lat && first_fault_time < 0) begin
            first_fault_time = $time;
            $fwrite(fault_log, "=== FIRST fault_latched 0->1 at t=%0t (%.3f ms) ===\n", $time,
                    ($realtime - t0)/1.0e6);
            $fwrite(fault_log, "init_done=%0b init_busy=%0b init_healthy=%0b runtime_run=%0b safety_off=%0b\n",
                    p_init_done, p_init_busy, p_init_healthy, p_runtime_run, p_safety_off);
            $fwrite(fault_log, "online_mask=0x%04h init_err=0x%04h run_err=0x%04h off_pend=0x%04h\n",
                    p_online_mask, p_init_err, p_run_err, p_off_pend);
            $fwrite(fault_log, "recovery_state=%0d fault_clear=%0b i2c_err_cnt=%0d off_retry=%0d i2c_tmo_cnt=%0d\n",
                    p_rec_state, p_fault_clear, p_i2c_err_cnt, p_off_retry, p_i2c_tmo_cnt);
            $fwrite(fault_log, "master: busy=%0b done=%0b ack_err=%0b timeout_err=%0b ar_done=%0b ar_ack_err=%0b\n",
                    p_m_busy, p_m_done, p_m_ack_err, p_m_timeout, p_ar_done, p_ar_ack_err);
            $fwrite(fault_log, "init_seq: st=%0d blk=%0d pca_txn=%0b pinit_sb(start=%0b done=%0b ack_err=%0b kind=%0d)\n",
                    p_init_st, p_init_blk, p_init_pca_tx,
                    p_pinit_sb_start, p_pinit_sb_done, p_pinit_sb_ack, p_pinit_sb_kind);
            $fwrite(fault_log, "inj_sb(start=%0b done=%0b ack_err=%0b blk=%0d)\n",
                    p_inj_sb_start, p_inj_sb_done, p_inj_sb_ack, p_inj_sb_blk);
            $fwrite(fault_log, "I2C slaves: starts=%0d stops=%0d acks=%0d nacks=%0d tca=%0d pca_wr=%0d last_pca=0x%02h\n",
                    n_start, n_stop, n_ack, n_nack, n_tca, n_pca_wr, {last_pca,1'b0});
            $fwrite(fault_log, "OE: oe_n=%0b armed=%0b reason=%0d sw_req=%0b\n\n",
                    oe_n, dbg_oe_armed, p_oe_reason, p_sw_oe_req);
        end
        // periodic state snapshot every 0.5 ms for the timeline
        if (($time % 5_000_000) < 10400) csv_row("TICK", $time/10000, 0);
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

    integer j;
    initial begin
        t0 = 0;
        csv        = $fopen("init_event_timeline.csv", "w");
        fault_log  = $fopen("fault_first_trigger.log", "w");
        $fwrite(csv, "t_ms,event,a,b,init_busy,init_st,cur_addr,init_done,init_healthy,online_mask,init_err_mask,fault_latched,ack_err,timeout_err,ar_ack_err,i2c_tmo_cnt,i2c_err_cnt,slave_nacks,recovery_state\n");

        $display("=== GAP1 diagnostic start ===");
        rstn = 0; repeat(20) @(posedge clk); rstn = 1; repeat(20) @(posedge clk);

        axi_write(16'h0000, 32'h0000_0001);
        for (j = 0; j < 256; j = j + 1) axi_write(16'h0100 + (j*4), (j % 5));
        axi_write(16'h0000, 32'h0000_0003);
        csv_row("COMMIT", 0, 0);

        repeat(3_000_000) @(posedge clk);   // ~31 ms
        csv_row("END", 0, 0);

        $display("  final: init_done=%0b init_healthy=%0b online=%04h fault=%0b rec_state=%0d",
                 p_init_done, p_init_healthy, p_online_mask, p_fault_lat, p_rec_state);
        $display("  i2c slaves: starts=%0d stops=%0d acks=%0d nacks=%0d tca=%0d pca_wr=%0d",
                 n_start, n_stop, n_ack, n_nack, n_tca, n_pca_wr);
        $display("  first_fault_time=%0d", first_fault_time);
        $fwrite(fault_log, "FINAL: init_done=%0b init_healthy=%0b online=0x%04h fault=%0b rec_state=%0d\n",
                p_init_done, p_init_healthy, p_online_mask, p_fault_lat, p_rec_state);
        $fclose(csv); $fclose(fault_log);
        $display("=== GAP1 diagnostic done ===");
        $finish;
    end
    initial begin #400_000_000; $display("=== GAP1 TIMEOUT ==="); $finish; end

endmodule
