// Host-only DGM2 AXI/atomic-buffer contract test. The peer is an explicit
// synchronous RAM/commit model, not a PCA or hardware-success certificate.
`timescale 1ns/1ps
module tb_dgm2_adapter;
  parameter integer CASE_ID=0, MATRIX_MODE=1, EXPECT_ACCEPT=1;
  parameter [15:0] TARGET_MASK=16'hf0f0;
  reg clk=0;always #500 clk=~clk;
  reg rst_n=0,healthy=1,permit=1;
  reg [11:0] awaddr=0,araddr=0;
  reg awvalid=0,wvalid=0,bready=0,arvalid=0,rready=0;
  reg [31:0] wdata=0;
  wire awready,wready,bvalid,arready,rvalid;
  wire [1:0] bresp,rresp;
  wire [31:0] rdata;
  wire [9:0] ma;wire mav,mwv,mbr;wire [31:0] md;wire [3:0] ms;
  reg mbv=0,pending=0;reg [15:0] generation=0;
  wire grant,discard,force_off,control_valid,active_valid,commit;
  wire [255:0] unknown;wire [31:0] ogen,session,reason;wire [63:0] control_sequence;
  wire [7:0] audit_addr;reg [7:0] audit_data=0;
  reg [31:0] staged[0:63],actual[0:63],pkt[0:95];
  integer i,checks=0,failures=0,commits=0,grants=0;
  string packet_file;
  perception_axi_runtime_adapter #(.TICKS_PER_US(1),.CONTROL_PATH_ENABLE(1),
    .DIGITAL_BRINGUP_MODE(1),.DIGITAL_MATRIX_MODE(MATRIX_MODE),
    .DIGITAL_TARGET_MASK(TARGET_MASK),.DIGITAL_LIFETIME_US(12000)) dut (
    .clk(clk),.rst_n(rst_n),.s_awaddr(awaddr),.s_awvalid(awvalid),.s_awready(awready),
    .s_wdata(wdata),.s_wstrb(4'hf),.s_wvalid(wvalid),.s_wready(wready),
    .s_bresp(bresp),.s_bvalid(bvalid),.s_bready(bready),
    .s_araddr(araddr),.s_arvalid(arvalid),.s_arready(arready),
    .s_rdata(rdata),.s_rresp(rresp),.s_rvalid(rvalid),.s_rready(rready),
    .m_awaddr(ma),.m_awvalid(mav),.m_awready(1'b1),.m_wdata(md),
    .m_wstrb(ms),.m_wvalid(mwv),.m_wready(1'b1),.m_bresp(2'b00),.m_bvalid(mbv),.m_bready(mbr),
    .legacy_commit_pending_i(pending),.legacy_active_gen_i(generation),
    .local_healthy_i(healthy),.runtime_permit_i(permit),
    .sched_busy_i(1'b0),.dd_busy_i(1'b0),.init_busy_i(1'b0),
    .legacy_grant_o(grant),.legacy_discard_o(discard),.force_off_o(force_off),
    .control_valid_o(control_valid),.active_valid_o(active_valid),.effective_unknown_o(unknown),
    .observation_gen_o(ogen),.control_session_o(session),.control_seq_o(control_sequence),
    .fault_reason_o(reason),.control_commit_pulse_o(commit),
    .audit_matrix_addr_o(audit_addr),.audit_matrix_data_i(audit_data));

  // Independent peer accepts the 64 data words and separate COMMIT, then
  // changes its active bank only when the DUT grants the pending commit.
  always @(posedge clk) begin
    audit_data<=actual[audit_addr>>2][(audit_addr&3)*8+:8];
    if(!rst_n)begin
      mbv<=0;pending<=0;generation<=0;commits<=0;grants<=0;
      for(integer n=0;n<64;n=n+1)begin staged[n]<=0;actual[n]<=0;end
    end else begin
      if(mav&&mwv)begin
        if(ms!==4'hf)$fatal(1,"full-width legacy write required");
        if(ma==0)begin
          if(md!==3)$fatal(1,"legacy commit command mismatch");
          pending<=1;
        end else if(ma>=10'h100&&ma<=10'h1fc)staged[(ma-10'h100)>>2]<=md;
        else $fatal(1,"unexpected legacy AXI address");
        mbv<=1;
      end
      if(mbv&&mbr)mbv<=0;
      if(grant&&pending)begin
        for(integer n=0;n<64;n=n+1)actual[n]<=staged[n];
        generation<=generation+1;pending<=0;grants<=grants+1;
      end
      if(discard)pending<=0;
      if(commit)commits<=commits+1;
    end
  end
  task check(input reg ok,input string msg);
    begin checks=checks+1;if(ok!==1'b1)begin failures=failures+1;$display("FAIL case=%0d %s",CASE_ID,msg);end end
  endtask
  task write_axi(input [11:0] a,input [31:0] d);
    integer n;
    begin
      @(negedge clk);awaddr=a;wdata=d;awvalid=1;wvalid=1;n=0;
      while((!awready||!wready)&&n<100)begin @(negedge clk);n=n+1;end
      if(n==100)$fatal(1,"AXI write handshake timeout");
      @(negedge clk);awvalid=0;wvalid=0;n=0;
      while(!bvalid&&n<100)begin @(negedge clk);n=n+1;end
      if(n==100||bresp!=0)$fatal(1,"AXI write response timeout/error");
      bready=1;@(negedge clk);bready=0;repeat(2)@(negedge clk);
    end
  endtask
  task read_axi(input [11:0] a,output [31:0] d);
    integer n;
    begin
      @(negedge clk);araddr=a;arvalid=1;n=0;
      while(!arready&&n<100)begin @(negedge clk);n=n+1;end
      if(n==100)$fatal(1,"AXI read handshake timeout");
      @(negedge clk);arvalid=0;n=0;
      while(!rvalid&&n<100)begin @(negedge clk);n=n+1;end
      if(n==100||rresp!=0)$fatal(1,"AXI read response timeout/error");
      d=rdata;rready=1;@(negedge clk);rready=0;repeat(2)@(negedge clk);
    end
  endtask
  task stage_packet;
    begin write_axi(0,1);for(integer n=0;n<96;n=n+1)write_axi(12'h100+4*n,pkt[n]);end
  endtask
  function [31:0] crc_byte(input [31:0] c,input [7:0] v);
    reg[31:0] t;integer n;
    begin t=c^v;for(n=0;n<8;n=n+1)t=t[0]?((t>>1)^32'hedb88320):(t>>1);crc_byte=t;end
  endfunction
  task set_crc;
    reg[31:0] c;integer n;
    begin pkt[11]=0;c=32'hffffffff;for(n=0;n<384;n=n+1)c=crc_byte(c,pkt[n/4][(n%4)*8+:8]);pkt[11]=~c;end
  endtask
  reg[31:0] rd;
  initial begin
    if(!$value$plusargs("PACKET=%s",packet_file))$fatal(1,"PACKET fixture required");
    $readmemh(packet_file,pkt);
    repeat(5)@(negedge clk);rst_n=1;
    stage_packet;
    check(generation==0&&ogen==0&&grants==0,"staging alone never changes an active bank");
    if(CASE_ID==13)permit=0;
    write_axi(0,2);repeat(1800)@(negedge clk);
    if(CASE_ID==13)begin
      check(!control_valid&&force_off&&generation==0&&grants==0,"permit withdrawn before COMMIT never grants active matrix");
    end else if(!EXPECT_ACCEPT)begin
      check(!control_valid&&force_off&&generation==0&&grants==0,"invalid or wrong-mode frame never reaches active bank");
      check(ogen==0,"rejected frame does not advance accepted generation");
    end else begin
      check(control_valid&&!force_off&&generation==1&&ogen==1&&commits==1,"one accepted frame is atomically committed");
      check(session==pkt[3]&&control_sequence=={pkt[5],pkt[4]},"session and sequence retained");
      for(i=0;i<96;i=i+1)begin read_axi(12'h400+4*i,rd);check(rd==pkt[i],"accepted96-word packet exact");end
      for(i=0;i<64;i=i+1)begin read_axi(12'he00+4*i,rd);check(rd==pkt[24+i],"actual synchronous active-bank matrix exact");end
      for(i=0;i<8;i=i+1)check(unknown[i*32+:32]==pkt[88+i],"UNKNOWN bitmap retained exactly");
      if(CASE_ID==7)begin
        for(i=24;i<88;i=i+1)pkt[i]=0;pkt[4]=pkt[4]+1;set_crc;
        stage_packet;check(generation==1,"second frame remains pending until COMMIT");
        write_axi(0,2);repeat(1800)@(negedge clk);
        check(control_valid&&generation==2&&commits==2,"known all-zero OFF frame accepted");
        for(i=0;i<64;i=i+1)begin read_axi(12'he00+4*i,rd);check(rd==0,"OFF frame removes previous nonzero data");end
      end
      if(CASE_ID==9||CASE_ID==10)begin
        if(CASE_ID==9)healthy=0;else permit=0;
        repeat(4)@(negedge clk);
        check(!control_valid&&force_off&&unknown=={256{1'b1}},"health/permit loss inhibits and invalidates effective cells");
      end
      if(CASE_ID==11)begin
        repeat(12500)@(negedge clk);
        check(!control_valid&&force_off&&unknown=={256{1'b1}},"unchanged digital lease expires without refresh");
      end
      if(CASE_ID==12)begin
        stage_packet;write_axi(0,2);repeat(1800)@(negedge clk);
        check(!control_valid&&force_off&&generation==1&&ogen==1,"duplicate session/sequence cannot recommit");
      end
    end
    $display("DGM2_ADAPTER_RESULT case=%0d checks=%0d failures=%0d generation=%0d reason=%0d",CASE_ID,checks,failures,generation,reason);
    if(failures)$fatal(1,"DGM2 adapter test failed");
    $display("DGM2_ADAPTER_SIM_PASS case=%0d",CASE_ID);$finish;
  end
  initial begin #100000000;$fatal(1,"bounded Host simulation watchdog");end
endmodule
