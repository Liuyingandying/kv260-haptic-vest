`timescale 1ns/1ps
// Phase 2F-IOBUF-FIX-R2 : open-drain IOBUF verification with REAL UNISIM IOBUF primitives.
// The IOBUF instances below are byte-for-byte the same form used in
// kv260_haptic_iobuf_wrapper.v, driven here by TB-controlled drv.
module tb_iobuf_od();
    reg  scl_drv = 1'b0;      // 1 = pull low
    reg  sda_drv = 1'b0;
    reg  slave_scl_low = 1'b0;
    reg  slave_sda_low = 1'b0;
    wire scl_sense, sda_sense;
    wire i2c_scl, i2c_sda;

    // ---- external pull-up model (Verilog standard pullup primitive) ----
    pullup (i2c_scl);
    pullup (i2c_sda);

    // ---- SLAVE model: strong 0 or release ----
    assign i2c_scl = slave_scl_low ? 1'b0 : 1'bz;
    assign i2c_sda = slave_sda_low ? 1'b0 : 1'bz;

    // ---- ACTUAL IOBUF INSTANCES (same connections as the RTL wrapper) ----
    IOBUF u_scl_iobuf (.I(1'b0), .T(~scl_drv), .O(scl_sense), .IO(i2c_scl));
    IOBUF u_sda_iobuf (.I(1'b0), .T(~sda_drv), .O(sda_sense), .IO(i2c_sda));

    integer errors = 0, checks = 0;
    task chk(input [255:0] nm, input cond);
        begin
            checks = checks + 1;
            if (cond) $display("[PASS] %0s", nm);
            else begin errors = errors + 1; $display("[FAIL] %0s", nm); end
        end
    endtask
    task show(input [127:0] tag);
        $display("   %0s : scl=%b sda=%b | scl_sense=%b sda_sense=%b | drv(scl=%b sda=%b)",
                 tag, i2c_scl, i2c_sda, scl_sense, sda_sense, scl_drv, sda_drv);
    endtask

    initial begin
        $display("=== Phase 2F-IOBUF-FIX-R2 : open-drain IOBUF test (UNISIM IOBUF) ===");
        #100;
        // 1. reset: drv=0 -> bus released -> HIGH
        scl_drv=0; sda_drv=0; slave_scl_low=0; slave_sda_low=0; #100;
        show("T1 reset");
        chk("T1a SCL released -> HIGH", i2c_scl === 1'bx ? 0 : (i2c_scl === 1'b1));
        chk("T1b SDA released -> HIGH", i2c_sda === 1'bx ? 0 : (i2c_sda === 1'b1));
        chk("T1c SCL sense HIGH", scl_sense === 1'b1);
        chk("T1d SDA sense HIGH", sda_sense === 1'b1);

        // 2. drv=1 -> LOW
        scl_drv=1; sda_drv=1; #100;
        show("T2 drive");
        chk("T2a SCL driven LOW", i2c_scl === 1'b0);
        chk("T2b SDA driven LOW", i2c_sda === 1'b0);
        chk("T2c SCL sense LOW", scl_sense === 1'b0);

        // 3. drv 1->0 -> back HIGH
        scl_drv=0; sda_drv=0; #100;
        show("T3 release");
        chk("T3a SCL back HIGH", i2c_scl === 1'b1);
        chk("T3b SDA back HIGH", i2c_sda === 1'b1);

        // 4. slave pulls low while master released -> sense must read LOW
        slave_scl_low=1; slave_sda_low=0; #100;
        show("T4 slave SCL low");
        chk("T4a SCL sense samples slave LOW", scl_sense === 1'b0);
        chk("T4b SDA unaffected (HIGH)", i2c_sda === 1'b1);
        slave_scl_low=0; #100;
        chk("T4c SCL recovers HIGH after slave release", i2c_scl === 1'b1);

        // 5. independence: drive SDA only
        scl_drv=0; sda_drv=1; #100;
        show("T5 SDA only");
        chk("T5a SCL stays HIGH", i2c_scl === 1'b1);
        chk("T5b SDA LOW", i2c_sda === 1'b0);
        sda_drv=0; #100;

        // 6. START / STOP
        // START: SCL high, SDA 1->0
        scl_drv=0; sda_drv=0; #100;
        sda_drv=1; #100;
        chk("T6a START: SDA falls while SCL HIGH", (i2c_scl===1'b1) && (i2c_sda===1'b0));
        // clock low, drive data LOW (SDA low while SCL low = legal data change)
        scl_drv=1; #100;
        sda_drv=1; #100;
        chk("T6b data change while SCL LOW ok", (i2c_scl===1'b0) && (i2c_sda===1'b0));
        // raise SCL (SDA still LOW)
        scl_drv=0; #100;
        chk("T6c before STOP: SCL HIGH and SDA LOW", (i2c_scl===1'b1) && (i2c_sda===1'b0));
        // STOP: release SDA while SCL HIGH -> SDA rises
        sda_drv=0; slave_sda_low=0; #100;
        chk("T6d STOP: SDA rises while SCL HIGH -> bus idle HIGH", (i2c_sda === 1'b1) && (i2c_scl === 1'b1));

        $display("");
        $display("=== RESULT: checks=%0d errors=%0d ===", checks, errors);
        if (errors==0) $display("=== R2 SIM: PASS ==="); else $display("=== R2 SIM: FAIL ===");
        $finish;
    end
    initial begin #20000; $display("=== TIMEOUT ==="); $finish; end
endmodule