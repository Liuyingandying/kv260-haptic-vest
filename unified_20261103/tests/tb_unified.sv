// Offline-only integration: unmodified KPF1 receiver -> copied 4D pipeline ->
// corrected IIC engine -> independent pin-level ACK/NACK slave model.
// No DUT health/state is forced. Physical outputs are permanently disabled.
`timescale 1ns/1ps
module tb_unified;
  parameter integer CASE_ID=0; // 0=flow, 1=no ACK, 2=default inhibit, 3=saved real frame
  // One simulated cycle per microsecond reduces event count. The real master
  // derives SCL edges from its configured divider; low-clock quantization is
  // explicitly simulation-only, not a board performance/electrical result.
  localparam integer CLK_HZ=1_000_000;
  reg clk=0; always #500 clk=~clk;
  reg rst_n=0;
  reg recovery_request=0;
  reg [11:0] awaddr=0, araddr=0;
  reg awvalid=0,wvalid=0,bready=0,arvalid=0,rready=0;
  reg [31:0] wdata=0; reg [3:0] wstrb=15;
  wire awready,wready,bvalid,arready,rvalid;
  wire [1:0] bresp,rresp;wire [31:0] rdata;
  wire raw_scl_low,raw_sda_low,pad_scl_low,pad_sda_low,oe_disable;
  reg slave_sda_low=0;
  wire scl=~raw_scl_low;
  wire sda=~(raw_sda_low|slave_sda_low);
  wire force_off,control_valid,active_valid,control_commit,healthy,fault,quiescent;
  wire [255:0] unknown;
  wire [31:0] ogen,reason,control_session;
  wire [63:0] control_seq;
  wire [15:0] hgen,online;
  wire [7:0] frame_count;
  unified_perception_haptic #(
    .CLK_FREQ_HZ(CLK_HZ),.TICKS_PER_US(1),.I2C_FREQ_HZ(100_000),
    .I2C_TIMEOUT_US(20000),.SIM_CONTROL_ENABLE(CASE_ID>=2?0:1),
    .ACTUATOR_PINS_ENABLE(0),.MIN_ONLINE_BLOCKS(16)
  ) dut (
    .clk(clk),.rst_n(rst_n),.simulation_permit_i(1'b1),.recovery_request_i(recovery_request),
    .s_awaddr(awaddr),.s_awvalid(awvalid),.s_awready(awready),
    .s_wdata(wdata),.s_wstrb(wstrb),.s_wvalid(wvalid),.s_wready(wready),
    .s_bresp(bresp),.s_bvalid(bvalid),.s_bready(bready),
    .s_araddr(araddr),.s_arvalid(arvalid),.s_arready(arready),
    .s_rdata(rdata),.s_rresp(rresp),.s_rvalid(rvalid),.s_rready(rready),
    .sts_awaddr(10'b0),.sts_awvalid(1'b0),.sts_awready(),
    .sts_wdata(32'b0),.sts_wstrb(4'b0),.sts_wvalid(1'b0),.sts_wready(),
    .sts_bresp(),.sts_bvalid(),.sts_bready(1'b1),
    .sts_araddr(10'b0),.sts_arvalid(1'b0),.sts_arready(),
    .sts_rdata(),.sts_rresp(),.sts_rvalid(),.sts_rready(1'b1),
    .scl_i(scl),.sda_i(sda),.raw_scl_drive_low_o(raw_scl_low),
    .raw_sda_drive_low_o(raw_sda_low),.scl_drive_low_o(pad_scl_low),
    .sda_drive_low_o(pad_sda_low),.oe_disable_o(oe_disable),
    .force_off_o(force_off),.control_valid_o(control_valid),.active_valid_o(active_valid),
    .effective_unknown_o(unknown),.observation_gen_o(ogen),
    .haptic_active_gen_o(hgen),.control_commit_pulse_o(control_commit),
    .control_session_o(control_session),.control_seq_o(control_seq),
    .fault_reason_o(reason),.init_healthy_o(healthy),.fault_latched_o(fault),
    .online_mask_o(online),.pipeline_quiescent_o(quiescent),.frame_count_o(frame_count)
  );

  integer checks=0,failures=0,scenarios=0,control_commits=0;
  integer transactions=0,total_bytes=0,init_full_off=0,runtime_nonzero=0;
  integer runtime_full_off=0,aborted_bulk_transactions=0,phase=0,current_pattern=1;
  reg [15:0] runtime_blocks=0;
  reg [31:0] pkt[0:95];
  reg [63:0] capture_counter=64'd1000000000;
  task check(input reg ok,input string msg);
    begin checks=checks+1;if(ok!==1'b1)begin
      failures=failures+1;$display("FAIL case=%0d phase=%0d t=%0t %s",CASE_ID,phase,$time,msg);
    end end
  endtask
  task mark(input string msg);
    begin scenarios=scenarios+1;$display("SCENARIO case=%0d n=%0d t=%0t %s",CASE_ID,scenarios,$time,msg);end
  endtask
  always @(posedge clk) if(rst_n) begin
    if(control_commit)control_commits=control_commits+1;
    if(pad_scl_low!==1'b0 || pad_sda_low!==1'b0 || oe_disable!==1'b1)
      $fatal(1,"physical outputs must remain disabled in every simulation case");
  end
  always @(posedge fault) if(rst_n)
    $display("OBSERVED_FAULT t=%0t init_healthy=%0d runtime_run=%0d recovery=%0d off_pending=%04h i2c_errors=%0d",
      $time,healthy,dut.u_haptic.runtime_run,dut.u_haptic.recovery_state,
      dut.u_haptic.off_pend_mask,dut.u_haptic.i2c_err_cnt);

  // The external model consumes ONLY raw pad-intent-derived SCL/SDA. It never
  // sees DUT byte-valid, internal state, online mask or health to generate ACK.
  reg bus_started=0; reg [7:0] shift=0;
  integer bit_index=0,txn_len=0;
  reg [7:0] txn[0:79];
  integer selected_tca_mask=0;
  function integer block_of_address(input [7:0] a);
    begin case(a)
      8'h9a:block_of_address=0;8'h9c:block_of_address=1;
      8'h9e:block_of_address=2;8'h88:block_of_address=3;
      8'h92:block_of_address=4;8'h94:block_of_address=5;
      8'h96:block_of_address=6;8'h98:block_of_address=7;
      8'h80:block_of_address=8;8'h82:block_of_address=9;
      8'h84:block_of_address=10;8'h86:block_of_address=11;
      8'h8a:block_of_address=12;8'h8c:block_of_address=13;
      8'h8e:block_of_address=14;8'h90:block_of_address=15;
      default:block_of_address=-1;
    endcase end
  endfunction
  function integer source_of(input integer b,input integer c);
    integer row,col;
    begin
      if(b<8)begin row=b*2+c/8;col=c%8;end
      else begin row=(b-8)*2+c/8;col=(b<12)?15-c%8:8+c%8;end
      source_of=row*16+col;
    end
  endfunction
  function integer duty_of(input integer level);
    begin case(level)
      0:duty_of=0;1:duty_of=1632;2:duty_of=2448;
      3:duty_of=3264;default:duty_of=4080;
    endcase end
  endfunction
  always @(negedge sda) if(rst_n&&scl===1'b1&&raw_sda_low===1'b1)begin
    check(!bus_started,"pin model: unexpected/repeated START");
    bus_started=1;bit_index=0;shift=0;txn_len=0;transactions=transactions+1;
  end
  always @(posedge scl) if(rst_n&&bus_started)begin
    if(bit_index<8)begin
      shift={shift[6:0],sda};
      if(bit_index==7)begin
        if(txn_len<80)txn[txn_len]=shift;
        txn_len=txn_len+1;total_bytes=total_bytes+1;
      end
      bit_index=bit_index+1;
    end else if(bit_index==8)bit_index=9;
  end
  always @(negedge scl) if(rst_n&&bus_started)begin
    if(bit_index==8)begin #100;slave_sda_low=(CASE_ID!=1);end
    else if(bit_index==9)begin #100;slave_sda_low=0;bit_index=0;end
  end
  integer ch,b,src,level,expected,duty;reg all_off;
  always @(posedge sda) if(rst_n&&bus_started&&scl===1'b1)begin
    bus_started=0;
    if(CASE_ID!=1)begin
      if(txn_len==2&&txn[0]==8'he0)selected_tca_mask=txn[1];
      if(txn_len>=2&&txn[0]!=8'he0&&txn[1]==8'h06)begin
        if(txn_len<66 && (force_off||fault) && phase==2)begin
          // Deliberate bridge revocation can STOP a partially transmitted
          // burst. It is not counted as a successful full PWM update. The
          // subsequent fault/HOLD/reinitialization tests must still pass.
          aborted_bulk_transactions=aborted_bulk_transactions+1;
          $display("OBSERVED_ABORTED_BULK t=%0t bytes=%0d force_off=%0d fault=%0d",$time,txn_len,force_off,fault);
        end else begin
        check(txn_len==66,"PCA bulk update contains address + register + 64 bytes");
        b=block_of_address(txn[0]);
        check(b>=0,"PCA address belongs to frozen hardware mapping");
        if(b>=0)check(selected_tca_mask==(1<<(b/4)),"PCA selected on correct TCA branch");
        all_off=1;
        for(ch=0;ch<16;ch=ch+1)begin
          if(txn[2+4*ch+3]!==8'h10 || txn[2+4*ch+2]!==0)all_off=0;
        end
        if(!healthy)begin
          check(all_off,"initializer writes only FULL_OFF channel payloads");
          init_full_off=init_full_off+1;
        end else if(all_off)runtime_full_off=runtime_full_off+1;
        else begin
          runtime_nonzero=runtime_nonzero+1;
          if(phase==1&&b>=0)begin
            check(!runtime_blocks[b],"one complete frame emits each block exactly once");
            runtime_blocks[b]=1;
            for(ch=0;ch<16;ch=ch+1)begin
              src=source_of(b,ch);level=(src+current_pattern)%5;expected=duty_of(level);
              duty={txn[2+4*ch+3][3:0],txn[2+4*ch+2]};
              check(txn[2+4*ch]==0&&txn[2+4*ch+1]==0,"PCA ON register pair remains zero");
              check(duty==expected,$sformatf("block%0d ch%0d cell%0d PWM expected%0d got%0d",b,ch,src,expected,duty));
              check(txn[2+4*ch+3][4]==(level==0),"FULL_OFF follows level0 semantics");
            end
          end
        end
        end
      end
    end
  end

  task write_axi(input [11:0] a,input [31:0] d);
    integer wait_cycles;
    begin
      @(negedge clk);awaddr=a;wdata=d;wstrb=15;awvalid=1;
      wait_cycles=0;while(!awready&&wait_cycles<300)begin @(negedge clk);wait_cycles=wait_cycles+1;end
      if(!awready)$fatal(1,"bounded AW handshake timeout");
      @(negedge clk);awvalid=0;repeat(2)@(negedge clk);wvalid=1;
      wait_cycles=0;while(!wready&&wait_cycles<300)begin @(negedge clk);wait_cycles=wait_cycles+1;end
      if(!wready)$fatal(1,"bounded W handshake timeout");
      @(negedge clk);wvalid=0;
      wait_cycles=0;while(!bvalid&&wait_cycles<300)begin @(negedge clk);wait_cycles=wait_cycles+1;end
      if(!bvalid)$fatal(1,"bounded B response timeout");
      check(bresp==0,"AXI mailbox write returns OKAY");
      bready=1;@(negedge clk);bready=0;repeat(2)@(negedge clk);
    end
  endtask
  task read_axi(input [11:0] a,output [31:0] d);
    integer wait_cycles;
    begin
      @(negedge clk);araddr=a;arvalid=1;
      wait_cycles=0;while(!arready&&wait_cycles<300)begin @(negedge clk);wait_cycles=wait_cycles+1;end
      if(!arready)$fatal(1,"bounded AR handshake timeout");
      @(negedge clk);arvalid=0;
      wait_cycles=0;while(!rvalid&&wait_cycles<300)begin @(negedge clk);wait_cycles=wait_cycles+1;end
      if(!rvalid)$fatal(1,"bounded R response timeout");
      check(rresp==0,"AXI mailbox read returns OKAY");
      d=rdata;rready=1;@(negedge clk);rready=0;repeat(2)@(negedge clk);
    end
  endtask
  task make_packet(input [31:0] session,input [63:0] seq,input integer quality,input integer pattern);
    integer k,l;
    begin
      capture_counter=capture_counter+1000000;
      for(k=0;k<96;k=k+1)pkt[k]=0;
      pkt[0]=32'h3146504b;pkt[1]=1;pkt[2]=384;pkt[3]=session;
      pkt[4]=seq[31:0];pkt[5]=seq[63:32];pkt[6]=capture_counter[31:0];pkt[7]=capture_counter[63:32];
      pkt[8]=0;pkt[9]=quality;pkt[10]=5;
      pkt[12]=1;pkt[13]=3000;pkt[14]=5;pkt[15]=1;pkt[16]=3100;pkt[17]=5;
      pkt[18]=900000;pkt[19]=1;pkt[20]={16'd400,16'd640};pkt[21]=96;pkt[22]=32;pkt[23]=256;
      for(k=24;k<88;k=k+1)for(l=0;l<4;l=l+1)pkt[k][8*l+:8]=(4*(k-24)+l+pattern)%5;
      if(quality==1)begin pkt[88]=8;pkt[12]=4;pkt[13]=507;pkt[14]=3;end
      if(quality>=2)begin
        for(k=24;k<88;k=k+1)pkt[k]=0;
        for(k=88;k<96;k=k+1)pkt[k]=32'hffffffff;
        pkt[12]=0;pkt[13]=32'hffffffff;pkt[14]=2;
        pkt[15]=0;pkt[16]=32'hffffffff;pkt[17]=2;
      end
      if(quality==3)pkt[10]=2; // GAP, never FRESH
    end
  endtask
  task upload;
    integer k;
    begin write_axi(0,1);for(k=0;k<96;k=k+1)write_axi(12'h100+4*k,pkt[k]);write_axi(0,2);end
  endtask
  task wait_control;
    integer cycles;
    begin cycles=0;while(!control_valid&&cycles<(CLK_HZ/1000)*30)begin @(negedge clk);cycles=cycles+1;end
      check(control_valid,"complete fresh frame becomes digital control-valid");
      if(!control_valid)$fatal(1,"control did not assert reason=%0d healthy=%0d hgen=%0d pending=%0d",reason,healthy,hgen,dut.legacy_pending);
      // The adapter publishes control_commit and control_valid together with
      // nonblocking updates. Let the separate pulse monitor sample that edge.
      repeat(2)@(negedge clk);
    end
  endtask
  task wait_idle;
    integer cycles;
    begin cycles=0;repeat(10)@(negedge clk);
      while(!quiescent&&cycles<(CLK_HZ/1000)*225)begin @(negedge clk);cycles=cycles+1;end
      check(quiescent,"downstream finishes within bounded wait");
      if(!quiescent)$fatal(1,"pipeline did not become quiescent");
    end
  endtask
  task recover_after_abort;
    integer cycles,previous_init_payloads;
    begin
      // Observe the actual existing recovery FSM; do not assign or force it.
      // A software clear is admitted only after successful all-channel OFF.
      cycles=0;
      while(!(fault && dut.u_haptic.recovery_state==3'd5 &&
          dut.u_haptic.off_pend_mask==0 && quiescent) && cycles<(CLK_HZ/1000)*250)begin
        @(negedge clk);cycles=cycles+1;
      end
      check(fault&&dut.u_haptic.recovery_state==3'd5&&dut.u_haptic.off_pend_mask==0&&quiescent,
        "intentional abort reaches latched HOLD only after verified FULL_OFF drain");
      if(!(fault&&dut.u_haptic.recovery_state==3'd5&&dut.u_haptic.off_pend_mask==0&&quiescent))
        $fatal(1,"recovery HOLD not reached state=%0d pending=%04h",dut.u_haptic.recovery_state,dut.u_haptic.off_pend_mask);
      $display("RECOVERY_HOLD t=%0t state=%0d off_pending=%04h fault=%0d",$time,
        dut.u_haptic.recovery_state,dut.u_haptic.off_pend_mask,fault);
      previous_init_payloads=init_full_off;
      recovery_request=1;repeat(2)@(negedge clk);recovery_request=0;
      cycles=0;
      while(!(healthy&&!fault&&dut.u_haptic.runtime_run)&&cycles<(CLK_HZ/1000)*300)begin
        @(negedge clk);cycles=cycles+1;
      end
      check(healthy&&!fault&&online==16'hffff&&dut.u_haptic.runtime_run,
        "explicit recovery performs full real modeled initialization and restores healthy RUN");
      check(init_full_off==previous_init_payloads+16,"recovery initializes all16 PCA FULL_OFF payloads again");
      if(!(healthy&&!fault&&dut.u_haptic.runtime_run))$fatal(1,"recovery did not restore healthy RUN");
      $display("RECOVERY_REINITIALIZED t=%0t state=%0d online=%04h fault=%0d",$time,
        dut.u_haptic.recovery_state,online,fault);
      wait_idle;
    end
  endtask
  integer cycles,k,l,old_commits,old_nonzero,unknown_count,nonzero_cells;
  reg [15:0]old_hgen;reg[31:0]old_ogen,rd;
  initial begin
    $display("TEST_SCOPE offline pin model only; CLK=%0dHz, IIC configured100k, frame deadline250ms; no board-performance claim",CLK_HZ);
    repeat(6)@(negedge clk);rst_n=1;
    if(CASE_ID==3)begin
      mark("saved REAL depth core/quality/codec KPF1 replay, never LIVE/control");
      $readmemh("fixtures/saved_00_expected.mem",pkt);
      check(pkt[0]==32'h3146504b&&pkt[2]==384&&pkt[9]==1,"saved frame is original384B PARTIAL");
      upload;repeat(50)@(negedge clk);
      check(ogen==1,"saved real frame accepted atomically as observation");
      unknown_count=0;nonzero_cells=0;
      for(k=0;k<256;k=k+1)begin
        if(unknown[k])unknown_count=unknown_count+1;
        if(pkt[24+k/4][8*(k%4)+:8]!=0)nonzero_cells=nonzero_cells+1;
      end
      for(k=0;k<96;k=k+1)begin read_axi(12'h400+4*k,rd);check(rd==pkt[k],$sformatf("saved original active word%0d",k));end
      check(unknown_count==85,"all85 saved UNKNOWN cells preserved");
      check(nonzero_cells==218,"all218 saved nonzero candidate levels preserved");
      for(k=0;k<8;k=k+1)check(unknown[32*k+:32]==pkt[88+k],"saved UNKNOWN bitmap exact");
      check(!control_valid&&force_off&&hgen==0&&control_commits==0,"saved frame cannot reach haptic control");
      $display("SAVED_REPLAY_RESULT unknown_cells=%0d nonzero_cells=%0d original_words=96 hardware_live=0",unknown_count,nonzero_cells);
    end else begin
    mark("real 4D initializer through independent pin-level slave");
    cycles=0;while(!healthy&&cycles<(CLK_HZ/1000)*275)begin @(negedge clk);cycles=cycles+1;end
    if(CASE_ID==1)begin
      check(!healthy&&online==0&&fault,"address NACK prevents hardware health and all blocks online");
      make_packet(1,1,0,1);upload;repeat(100)@(negedge clk);
      check(!control_valid&&force_off&&hgen==0&&control_commits==0,"NACK path cannot promote a complete control frame");
      check(runtime_nonzero==0,"NACK path emits no nonzero PWM payload");
    end else begin
      check(healthy&&online==16'hffff&&!fault,"all16 real modeled peripherals initialize successfully");
      check(init_full_off==16,"all16 PCA initial payloads verified FULL_OFF");
      if(!healthy)$fatal(1,"initializer failed to reach healthy");
      wait_idle;
      if(CASE_ID==2)begin
        mark("default SIM_CONTROL_ENABLE=0 inhibits even valid COMPLETE");
        make_packet(1,1,0,1);upload;repeat(1000)@(negedge clk);
        check(ogen==1&&active_valid,"default build still accepts perception observation");
        check(!control_valid&&force_off&&hgen==0&&control_commits==0,"default build never receives control authorization");
        check(runtime_nonzero==0,"default build emits no nonzero PWM");
      end else begin
        mark("COMPLETE384B reaches real scheduler/map/LUT and all16 PCA PWM transactions");
        phase=1;current_pattern=1;make_packet(1,1,0,current_pattern);upload;wait_control;
        check(control_commits==1&&hgen==1,"one atomic control commit advances haptic generation exactly once");
        for(k=0;k<96;k=k+1)begin read_axi(12'h400+4*k,rd);check(rd==pkt[k],$sformatf("active KPF1 word%0d preserved",k));end
        cycles=0;while(runtime_blocks!=16'hffff&&cycles<(CLK_HZ/1000)*212)begin @(negedge clk);cycles=cycles+1;end
        check(runtime_blocks==16'hffff,"all16 blocks receive geometrically mapped PWM");
        if(runtime_blocks!=16'hffff)$fatal(1,"runtime block set incomplete=%04h",runtime_blocks);
        wait_idle;check(control_valid&&!force_off,"complete frame remains fresh at end of output");

        mark("PARTIAL retains near matrix/UNKNOWN and revokes output authorization");
        phase=2;old_hgen=hgen;old_commits=control_commits;old_ogen=ogen;
        make_packet(1,2,1,2);upload;repeat(50)@(negedge clk);
        check(ogen==old_ogen+1&&active_valid,"PARTIAL becomes an observation");
        check(!control_valid&&force_off&&hgen==old_hgen&&control_commits==old_commits,"PARTIAL creates no haptic commit");
        check(unknown[3]&&unknown!=0,"PARTIAL UNKNOWN is explicit");
        read_axi(12'h460,rd);check(rd==pkt[24],"PARTIAL candidate levels remain in active observation");
        wait_idle;old_nonzero=runtime_nonzero;repeat(2000)@(negedge clk);
        check(runtime_nonzero==old_nonzero,"no later nonzero PWM after PARTIAL quiescence");
        check(runtime_full_off>=16,"revocation emits all-channel FULL_OFF updates");

        mark("UNKNOWN and UNUSABLE remain explicit and never promote control");
        make_packet(1,3,2,0);upload;repeat(50)@(negedge clk);
        check(!control_valid&&force_off&&unknown=={256{1'b1}}&&hgen==old_hgen,"UNKNOWN retained without safe-zero interpretation");
        make_packet(1,4,3,0);upload;repeat(50)@(negedge clk);
        check(!active_valid&&!control_valid&&force_off&&unknown=={256{1'b1}},"GAP UNUSABLE retires old stream");

        mark("old session cannot recover after GAP; new session requires genuine new frame");
        old_ogen=ogen;make_packet(1,5,0,3);upload;repeat(50)@(negedge clk);
        check(!control_valid&&ogen==old_ogen,"retired session rejected");
        wait_idle;make_packet(2,1,0,3);upload;wait_control;
        check(control_session==2&&control_seq==1&&hgen==old_hgen+1,"new-session complete frame recovers");
        wait_idle;

        mark("duplicate frame cannot renew control; fresh next sequence recovers");
        old_ogen=ogen;old_hgen=hgen;make_packet(2,1,0,3);upload;repeat(50)@(negedge clk);
        check(!control_valid&&force_off&&ogen==old_ogen&&hgen==old_hgen,"duplicate sequence rejected without silent output");
        wait_idle;make_packet(2,2,0,4);upload;wait_control;
        mark("PARTIAL arriving during real IIC refresh immediately revokes authority");
        repeat(100)@(negedge clk);
        check(!quiescent,"precondition: actual downstream refresh remains in progress");
        old_hgen=hgen;old_commits=control_commits;
        make_packet(2,3,1,2);upload;repeat(50)@(negedge clk);
        check(!control_valid&&force_off&&hgen==old_hgen&&control_commits==old_commits,
          "busy PARTIAL revokes permission without promoting another haptic frame");
        wait_idle;old_nonzero=runtime_nonzero;repeat(2000)@(negedge clk);
        check(runtime_nonzero==old_nonzero,"after bounded old-frame/STOP drain no later nonzero output");

        mark("busy revocation requires latched-OFF, explicit reinit and new-session recovery");
        recover_after_abort;
        make_packet(3,1,0,1);upload;wait_control;
        check(control_session==3&&control_seq==1,"post-fault recovery uses a genuinely new session and RESUME");
        wait_idle;

        mark("software silence expires within unchanged250ms lifetime");
        repeat(250050)@(negedge clk);
        check(!control_valid&&!active_valid&&force_off&&unknown=={256{1'b1}},"PL watchdog expires without further PS traffic");
        wait_idle;
        mark("declared stale250001us frame never enables control");
        old_hgen=hgen;make_packet(4,1,0,1);pkt[8]=250001;upload;repeat(50)@(negedge clk);
        check(!control_valid&&force_off&&hgen==old_hgen,"250ms freshness threshold remains unchanged");
      end
    end
    end
    check(pad_scl_low==0&&pad_sda_low==0&&oe_disable,"physical outputs remained disabled");
    $display("UNIFIED_RESULT case=%0d scenarios=%0d checks=%0d failures=%0d bytes=%0d transactions=%0d init_full_off=%0d runtime_nonzero=%0d runtime_full_off=%0d control_commits=%0d hgen=%0d aborted_bulk=%0d elapsed_ps=%0t",CASE_ID,scenarios,checks,failures,total_bytes,transactions,init_full_off,runtime_nonzero,runtime_full_off,control_commits,hgen,aborted_bulk_transactions,$time);
    if(failures)$fatal(1,"unified integration failed");
    $display("UNIFIED_SIM_PASS case=%0d",CASE_ID);$finish;
  end
  initial begin #2500000000;$fatal(1,"offline integration watchdog2.5s");end
endmodule
