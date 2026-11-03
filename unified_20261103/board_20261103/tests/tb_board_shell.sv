// OFFLINE BOARD-SHELL TEST ONLY: PS clock/GPIO/AXI and IOBUF are models.
// No real PS, USB, DDR, supply, electrical waveform, TCA ACK or motor proof.
// DUT core RTL is real and unchanged. The bench never forces DUT internals.
`timescale 1ns/1ps
module tb_board_shell;
    tri1 scl, sda;
    reg external_scl_low=0, external_sda_low=0;
    assign scl=external_scl_low ? 1'b0 : 1'bz;
    assign sda=external_sda_low ? 1'b0 : 1'bz;
    kv260_unified_board_top dut(.i2c_scl_io(scl),.i2c_sda_io(sda));
    integer checks=0, failures=0, cycle;
    reg output_violation=0;
    task check(input reg ok,input string message);
        begin checks=checks+1; if(ok !== 1'b1)begin
            failures=failures+1; $display("FAIL t=%0t %s",$time,message);
        end end
    endtask
    task edge_wait;
        begin @(posedge dut.pl_clk0); #1; end
    endtask
    task low_edge;
        begin @(negedge dut.pl_clk0); #1; end
    endtask
    always @(posedge dut.pl_clk0) begin
        if(dut.scl_low !== 1'b0 || dut.sda_low !== 1'b0 ||
           dut.u_scl_iobuf.T !== 1'b1 || dut.u_sda_iobuf.T !== 1'b1)
            output_violation=1;
    end
    initial begin
        #1;
        check(dut.pl_bus_resetn===0,"startup initially holds AXI bus reset");
        check(dut.core_resetn===0,"payload reset initially asserted");
        check(scl===1&&sda===1,"ideal pullups establish released idle pads");
        // Drive the SIMULATED PS GPIO source, not any DUT core state.
        dut.u_ps_bd.ctrl_data=32'h80000000;
        dut.u_ps_bd.ctrl_tri=32'h7fffffff;
        #1;
        check(dut.reset_override===1,"bit31 data1 TRI0 requests legacy reset override");
        check(dut.core_resetn===0,"override cannot bypass startup qualification");
        for(cycle=1;cycle<=15;cycle=cycle+1)begin
            edge_wait;
            check(dut.pl_bus_resetn===0&&dut.core_resetn===0,
                  $sformatf("startup edge%0d still inhibits bus/payload",cycle));
        end
        edge_wait;
        check(dut.pl_bus_resetn===1,"startup releases exactly on edge16");
        check(dut.core_resetn===1,"legacy override can release payload after startup");
        check(dut.diag_status_i[5:0]===6'b111100,"GPIO status reports actual legacy reset bits");
        low_edge;dut.u_ps_bd.ctrl_tri=32'hffffffff; #1;
        check(dut.reset_override===0&&dut.core_resetn===0,
              "TRI1 revokes override even when DATA31 remains1");
        low_edge;dut.u_ps_bd.ps_resetn_q=1;
        edge_wait;
        check(dut.ps_reset_sync[1]===0&&dut.core_resetn===0,
              "PS reset release does not bypass the first synchronizer stage");
        edge_wait;
        check(dut.ps_reset_sync[1]===1&&dut.core_resetn===1,
              "PS reset release reaches payload after second stage");
        low_edge;dut.u_ps_bd.ctrl_data=0; #1;
        check(dut.core_resetn===1,"normal PS reset path works without GPIO override");
        low_edge;dut.u_ps_bd.ps_resetn_q=0;
        edge_wait;
        check(dut.core_resetn===1,"PS reset assertion is sampled through first stage");
        edge_wait;
        check(dut.core_resetn===0,"PS reset assertion reaches payload through second stage");
        // Restore the simulated normal PS reset, leave bit31 inactive.
        low_edge;dut.u_ps_bd.ps_resetn_q=1;
        edge_wait;edge_wait;
        // Recovery bit29 DATA alone must do nothing while configured input.
        low_edge;dut.u_ps_bd.ctrl_data=32'h20000000;
        dut.u_ps_bd.ctrl_tri=32'hffffffff;
        edge_wait;edge_wait;edge_wait;
        check(dut.recovery_request===0&&dut.recovery_sync[1]===0,
              "bit29 DATA1 TRI1 cannot request recovery");
        low_edge;dut.u_ps_bd.ctrl_tri=32'hdfffffff;
        edge_wait;
        check(dut.recovery_sync[1]===0,"recovery request traverses first stage");
        edge_wait;
        check(dut.u_unified.recovery_request_i===1,
              "bit29 DATA1 TRI0 reaches core recovery interface after two stages");
        low_edge;dut.u_ps_bd.ctrl_data=0;
        edge_wait;edge_wait;
        check(dut.u_unified.recovery_request_i===0,"DATA29 clear withdraws recovery");
        // bit30 is intentionally disconnected from any output authorization.
        low_edge;dut.u_ps_bd.ctrl_data=32'h40000000;
        dut.u_ps_bd.ctrl_tri=32'hbfffffff;
        repeat(8)edge_wait;
        check(dut.scl_low===0&&dut.sda_low===0&&dut.oe_disable===1,
              "bit30 cannot enable pads or OE");
        check(dut.u_unified.simulation_permit_i===0&&dut.control_valid===0&&dut.force_off===1,
              "GPIO cannot supply simulation/control authority");
        // Even all GPIO bits set/output cannot turn the physical path on.
        low_edge;dut.u_ps_bd.ctrl_data=32'hffffffff;dut.u_ps_bd.ctrl_tri=0;
        repeat(4)edge_wait;
        check(dut.scl_low===0&&dut.sda_low===0&&dut.oe_disable===1,
              "all GPIO bits high cannot bypass fixed board inhibition");
        low_edge;dut.u_ps_bd.ctrl_data=0;dut.u_ps_bd.ctrl_tri=32'hffffffff;
        repeat(3)edge_wait;
        // Independent external pad changes must reach IOBUF.O immediately,
        // then the unchanged core ONLY through the second synchronizer stage.
        low_edge;external_scl_low=1;#1;
        check(scl===0&&dut.scl_sense===0&&dut.u_scl_iobuf.O===0,
              "IOBUF.O reflects external SCL low, not a constant-high loopback");
        edge_wait;
        check(dut.scl_meta===0&&dut.u_unified.scl_i===1,
              "SCL first stage changes while core still sees previous level");
        edge_wait;
        check(dut.scl_sync===0&&dut.u_unified.scl_i===0&&dut.diag_status_i[6]===0,
              "SCL second stage drives core and diagnostic readback");
        low_edge;external_scl_low=0;edge_wait;
        check(dut.scl_meta===1&&dut.u_unified.scl_i===0,"SCL release traverses first stage");
        edge_wait;
        check(dut.u_unified.scl_i===1&&scl===1,"SCL release reaches core after two stages");
        low_edge;external_sda_low=1;#1;
        check(sda===0&&dut.sda_sense===0&&dut.u_sda_iobuf.O===0,
              "IOBUF.O reflects external SDA low");
        edge_wait;
        check(dut.sda_meta===0&&dut.u_unified.sda_i===1,
              "SDA first stage changes while core sees previous level");
        edge_wait;
        check(dut.sda_sync===0&&dut.u_unified.sda_i===0&&dut.diag_status_i[7]===0,
              "SDA second stage drives core and readback");
        low_edge;external_sda_low=0;edge_wait;
        check(dut.sda_meta===1&&dut.u_unified.sda_i===0,"SDA release traverses first stage");
        edge_wait;
        check(dut.u_unified.sda_i===1&&sda===1,"SDA release reaches core after two stages");
        check(!output_violation,"pads remained high-impedance throughout every tested phase");
        check(dut.mail_awvalid===0&&dut.mail_wvalid===0&&dut.mail_arvalid===0&&
              dut.sts_awvalid===0&&dut.sts_wvalid===0&&dut.sts_arvalid===0,
              "PS model sent no hidden AXI transaction");
        $display("BOARD_SHELL_RESULT checks=%0d failures=%0d PS_MODEL_ONLY=1 HARDWARE_ACTION=0",checks,failures);
        if(failures)$fatal(1,"board shell failed");
        $display("BOARD_SHELL_PASS");$finish;
    end
    initial begin #10000; $fatal(1,"bounded10us shell timeout");end
endmodule

// Minimal ideal simulation primitive; not an electrical I/O model.
module IOBUF #(parameter IOSTANDARD="DEFAULT",parameter integer DRIVE=12,parameter SLEW="SLOW")
    (input wire I,input wire T,output wire O,inout wire IO);
    assign IO=T?1'bz:I;
    assign O=IO;
endmodule

// PS is deliberately a model. The actual generated BD is verified separately
// by Vivado; this bench proves only board-shell logic around these interfaces.
module kv260_unified_ps_wrapper (
    output wire [31:0] M_AXI_PERCEPTION_awaddr,
    output wire [2:0] M_AXI_PERCEPTION_awprot,
    output wire M_AXI_PERCEPTION_awvalid,
    input wire M_AXI_PERCEPTION_awready,
    output wire [31:0] M_AXI_PERCEPTION_wdata,
    output wire [3:0] M_AXI_PERCEPTION_wstrb,
    output wire M_AXI_PERCEPTION_wvalid,
    input wire M_AXI_PERCEPTION_wready,
    input wire [1:0] M_AXI_PERCEPTION_bresp,
    input wire M_AXI_PERCEPTION_bvalid,
    output wire M_AXI_PERCEPTION_bready,
    output wire [31:0] M_AXI_PERCEPTION_araddr,
    output wire [2:0] M_AXI_PERCEPTION_arprot,
    output wire M_AXI_PERCEPTION_arvalid,
    input wire M_AXI_PERCEPTION_arready,
    input wire [31:0] M_AXI_PERCEPTION_rdata,
    input wire [1:0] M_AXI_PERCEPTION_rresp,
    input wire M_AXI_PERCEPTION_rvalid,
    output wire M_AXI_PERCEPTION_rready,
    output wire [31:0] M_AXI_STATUS_awaddr,
    output wire [2:0] M_AXI_STATUS_awprot,
    output wire M_AXI_STATUS_awvalid,
    input wire M_AXI_STATUS_awready,
    output wire [31:0] M_AXI_STATUS_wdata,
    output wire [3:0] M_AXI_STATUS_wstrb,
    output wire M_AXI_STATUS_wvalid,
    input wire M_AXI_STATUS_wready,
    input wire [1:0] M_AXI_STATUS_bresp,
    input wire M_AXI_STATUS_bvalid,
    output wire M_AXI_STATUS_bready,
    output wire [31:0] M_AXI_STATUS_araddr,
    output wire [2:0] M_AXI_STATUS_arprot,
    output wire M_AXI_STATUS_arvalid,
    input wire M_AXI_STATUS_arready,
    input wire [31:0] M_AXI_STATUS_rdata,
    input wire [1:0] M_AXI_STATUS_rresp,
    input wire M_AXI_STATUS_rvalid,
    output wire M_AXI_STATUS_rready,
    output wire [31:0] diag_ctrl_o,
    output wire [31:0] diag_ctrl_t,
    input wire [31:0] diag_status_i,
    output wire pl_clk0,
    output wire pl_resetn0,
    input wire pl_bus_resetn
);
    reg clock_q=0;
    always #5 clock_q=~clock_q;
    reg ps_resetn_q=0;
    reg [31:0] ctrl_data=0, ctrl_tri=32'hffffffff;
    assign pl_clk0=clock_q;
    assign pl_resetn0=ps_resetn_q;
    assign diag_ctrl_o=ctrl_data;
    assign diag_ctrl_t=ctrl_tri;
    assign M_AXI_PERCEPTION_awaddr = 32'b0;
    assign M_AXI_PERCEPTION_awprot = 3'b0;
    assign M_AXI_PERCEPTION_awvalid = 1'b0;
    assign M_AXI_PERCEPTION_wdata = 32'b0;
    assign M_AXI_PERCEPTION_wstrb = 4'b0;
    assign M_AXI_PERCEPTION_wvalid = 1'b0;
    assign M_AXI_PERCEPTION_bready = 1'b1;
    assign M_AXI_PERCEPTION_araddr = 32'b0;
    assign M_AXI_PERCEPTION_arprot = 3'b0;
    assign M_AXI_PERCEPTION_arvalid = 1'b0;
    assign M_AXI_PERCEPTION_rready = 1'b1;
    assign M_AXI_STATUS_awaddr = 32'b0;
    assign M_AXI_STATUS_awprot = 3'b0;
    assign M_AXI_STATUS_awvalid = 1'b0;
    assign M_AXI_STATUS_wdata = 32'b0;
    assign M_AXI_STATUS_wstrb = 4'b0;
    assign M_AXI_STATUS_wvalid = 1'b0;
    assign M_AXI_STATUS_bready = 1'b1;
    assign M_AXI_STATUS_araddr = 32'b0;
    assign M_AXI_STATUS_arprot = 3'b0;
    assign M_AXI_STATUS_arvalid = 1'b0;
    assign M_AXI_STATUS_rready = 1'b1;
endmodule
