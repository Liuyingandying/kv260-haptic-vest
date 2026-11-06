// ISOLATED API COPY; frozen source is retained unchanged.
// Independent runtime-named API fork. All KPF1/health/identity logic unchanged.
// CONTROL_PATH_ENABLE defaults zero; runtime_permit_i must come from the
// separately reviewed board policy. Neither proves physical OE or safe power.
`timescale 1ns/1ps
module perception_axi_runtime_adapter #(
    parameter integer TICKS_PER_US = 100,
    parameter integer FRESH_US = 250000,
    parameter integer FUTURE_US = 40000,
    parameter integer CONTROL_PATH_ENABLE = 0,
    parameter integer DIGITAL_BRINGUP_MODE = 0,
    // Opt-in DGM2 matrix bench ABI. Zero retains the accepted DGD1 ABI.
    // This does not establish exposure freshness or physical power safety.
    parameter integer DIGITAL_MATRIX_MODE = 0,
    parameter [15:0] DIGITAL_TARGET_MASK = 16'hf0f0,
    parameter integer DIGITAL_LIFETIME_US = 3000000
) (
    input wire clk, input wire rst_n,
    input wire [11:0] s_awaddr, input wire s_awvalid, output wire s_awready,
    input wire [31:0] s_wdata, input wire [3:0] s_wstrb,
    input wire s_wvalid, output wire s_wready,
    output reg [1:0] s_bresp, output reg s_bvalid, input wire s_bready,
    input wire [11:0] s_araddr, input wire s_arvalid, output wire s_arready,
    output reg [31:0] s_rdata, output reg [1:0] s_rresp,
    output reg s_rvalid, input wire s_rready,
    output wire [9:0] m_awaddr, output wire m_awvalid, input wire m_awready,
    output wire [31:0] m_wdata, output wire [3:0] m_wstrb,
    output wire m_wvalid, input wire m_wready,
    input wire [1:0] m_bresp, input wire m_bvalid, output wire m_bready,
    input wire legacy_commit_pending_i, input wire [15:0] legacy_active_gen_i,
    input wire local_healthy_i, input wire runtime_permit_i,
    input wire sched_busy_i, input wire dd_busy_i, input wire init_busy_i,
    output wire legacy_grant_o, output wire legacy_discard_o,
    output wire force_off_o, output wire control_valid_o,
    output reg active_valid_o, output wire [255:0] effective_unknown_o,
    output reg [31:0] observation_gen_o,
    output reg [31:0] control_session_o, output reg [63:0] control_seq_o,
    output wire [31:0] fault_reason_o,
    output reg control_commit_pulse_o,
    output wire [7:0] audit_matrix_addr_o,
    input wire [7:0] audit_matrix_data_i
);
    localparam IDLE=0, CHECK=1, BR_TX=2, BR_RESP=3, BR_GRANT=4, BR_CONFIRM=5, QUARANTINE=6, DIGITAL_CRC=7;
    localparam integer LIFETIME_US=DIGITAL_BRINGUP_MODE?DIGITAL_LIFETIME_US:FRESH_US;
    localparam F_NONE=0, F_WRITE=1, F_COMMAND=2, F_INCOMPLETE=3,
      F_HEADER=4, F_PAYLOAD=5, F_IDENTITY=6, F_AGE=7, F_QUALITY=8,
      F_GAP=9, F_PRODUCER=10, F_TIMEOUT=11, F_LOCAL=12,
      F_PERMIT=13, F_LEGACY=14, F_LATE=15, F_BRIDGE_TIMEOUT=16, F_CRC=17;
    reg [2:0] state;
    reg [31:0] pending_pkt [0:95];
    reg [31:0] active_pkt [0:95];
    reg [95:0] seen;
    reg rx_open, resume_required, new_session_required, protocol_fault;
    reg [31:0] reason;
    reg [31:0] last_session;
    reg [31:0] retired_session_floor;
    reg [63:0] last_seq, last_capture;
    reg [63:0] now_us, begin_us, activated_us, control_us, bridge_us;
    reg [31:0] divider;
    reg signed [63:0] active_initial_age, control_initial_age;
    reg abort_latched;
    reg control_valid_q;
    reg crc_verified;
    reg [8:0] crc_index;
    reg [31:0] crc_acc;
    reg audit_read_active;
    reg [2:0] audit_read_count;
    reg [7:0] audit_read_base;
    assign audit_matrix_addr_o=audit_read_base+{6'b0,audit_read_count[1:0]};
    reg quarantine_tx, quarantine_resp;
    reg [6:0] bridge_index;
    reg master_aw_done, master_w_done;
    reg [15:0] expected_legacy_gen;
    reg [11:0] awaddr_q;
    reg [31:0] wdata_q;
    reg [3:0] wstrb_q;
    reg aw_have, w_have;
    wire wexec=aw_have && w_have && !s_bvalid;
    wire busy=(state!=IDLE);
    wire write_failure=wexec && ((awaddr_q[1:0]!=0) || (wstrb_q!=4'hf) ||
      busy || ((awaddr_q!=12'h000) && !((awaddr_q>=12'h100)&&(awaddr_q<=12'h27c))));
    assign s_awready=rst_n && !aw_have && !s_bvalid;
    assign s_wready=rst_n && !w_have && !s_bvalid;
    assign s_arready=rst_n && !s_rvalid && !audit_read_active;
    assign m_awaddr=(bridge_index==64)?10'h000:(10'h100+{bridge_index,2'b00});
    assign m_awvalid=((state==BR_TX)||(state==QUARANTINE && quarantine_tx)) && !master_aw_done;
    assign m_wdata=(bridge_index==64)?32'h00000003:active_pkt[24+bridge_index];
    assign m_wstrb=4'hf;
    assign m_wvalid=((state==BR_TX)||(state==QUARANTINE && quarantine_tx)) && !master_w_done;
    assign m_bready=(state==BR_RESP)||(state==QUARANTINE && quarantine_resp);
    wire bridge_expired=(now_us-bridge_us)>=LIFETIME_US;
    wire signed [63:0] current_control_age=control_initial_age+$signed(now_us-control_us);
    wire signed [63:0] current_active_age=active_initial_age+$signed(now_us-activated_us);
    wire control_expired=control_valid_q && current_control_age>=LIFETIME_US;
    wire bridge_inflight=DIGITAL_BRINGUP_MODE?
        ((state>=BR_TX)&&(state<=BR_CONFIRM)):(state>=BR_TX);
    wire current_transaction_expired=bridge_inflight && current_active_age>=LIFETIME_US;
    // A single BR_GRANT cycle; fault/discard always has priority over grant.
    assign legacy_grant_o=(state==BR_GRANT) && legacy_commit_pending_i &&
      (CONTROL_PATH_ENABLE!=0) && runtime_permit_i && local_healthy_i &&
      !abort_latched && !write_failure && !current_transaction_expired &&
      !bridge_expired && !sched_busy_i && !dd_busy_i && !init_busy_i;
    assign legacy_discard_o=abort_latched || write_failure || !local_healthy_i ||
      current_transaction_expired || bridge_expired;
    assign control_valid_o=control_valid_q && CONTROL_PATH_ENABLE!=0 && local_healthy_i &&
      runtime_permit_i && !abort_latched && !control_expired;
    assign force_off_o=!control_valid_o;
    wire terminal_fault_now=control_expired ||
      (active_valid_o && current_active_age>=LIFETIME_US) ||
      (rx_open && now_us-begin_us>=LIFETIME_US) ||
      ((control_valid_q || bridge_inflight) && (!local_healthy_i || !runtime_permit_i)) ||
      (bridge_inflight && (current_transaction_expired || bridge_expired));
    assign fault_reason_o=reason;
    genvar b;
    generate for(b=0;b<8;b=b+1) begin:UNKNOWN_BITS
      assign effective_unknown_o[b*32+:32]=active_valid_o?active_pkt[88+b]:32'hffffffff;
    end endgenerate

    reg header_ok, payload_ok, semantic_ok, identity_ok, is_new_session;
    reg fresh_flag, gap_flag, resume_flag, all_mask_one, all_mask_zero, matrix_zero;
    reg mask_any;
    reg [31:0] quality;
    wire signed [63:0] offered_age=($signed(pending_pkt[8])<0?64'sd0:$signed(pending_pkt[8]))+$signed(now_us-begin_us);
    wire offered_late=DIGITAL_BRINGUP_MODE?((now_us-begin_us)>=LIFETIME_US):(offered_age>=FRESH_US);
    wire age_legal=($signed(pending_pkt[8])>=-FUTURE_US) && ($signed(pending_pkt[8])<=FRESH_US);
    wire candidate_control=DIGITAL_BRINGUP_MODE?
      (DIGITAL_MATRIX_MODE?digital_matrix_payload_ok:digital_payload_ok):
      (quality==0)&&fresh_flag&&!gap_flag&&(pending_pkt[11]==0)&&
      (pending_pkt[12]==1)&&(pending_pkt[15]==1)&&!offered_late&&
      (!resume_required || resume_flag)&&(!new_session_required || ((pending_pkt[3]>retired_session_floor)&&resume_flag));
    reg [63:0] offered_seq, offered_capture;
    reg digital_payload_ok;
    reg digital_matrix_payload_ok;
    integer nonzero_count,selected_row,selected_col,selected_block,selected_channel;
    integer matrix_source_index,matrix_block;
    function automatic [31:0] crc_byte(input [31:0] crc,input [7:0] value);
      reg [31:0] temp;integer bit_index;
      begin
        temp=crc^{24'b0,value};
        for(bit_index=0;bit_index<8;bit_index=bit_index+1)
          temp=temp[0]?((temp>>1)^32'hedb88320):(temp>>1);
        crc_byte=temp;
      end
    endfunction
    wire [7:0] crc_input=(crc_index>=44&&crc_index<48)?8'b0:
       pending_pkt[crc_index[8:2]][crc_index[1:0]*8+:8];
    wire [31:0] crc_next=crc_byte(crc_acc,crc_input);
    integer i, j;
    function automatic side_valid(input [31:0] kind, distance_mm, flags);
      begin
        side_valid=(kind<=5) && ((flags==2)||(flags==3)||(flags==5)||(flags==6));
        if(kind==0) side_valid=side_valid && ((flags==2)||(flags==6)) && (distance_mm==32'hffffffff);
        else side_valid=side_valid && ((flags==3)||(flags==5)) &&
          (distance_mm!=0) && (distance_mm!=32'hffffffff);
        if(kind==1) side_valid=side_valid && (flags==5);
        if(kind==5) side_valid=side_valid && (flags==3);
      end
    endfunction
    always @* begin
      quality=pending_pkt[9];
      fresh_flag=pending_pkt[10][0]; gap_flag=pending_pkt[10][1]; resume_flag=pending_pkt[10][2];
      offered_seq={pending_pkt[5],pending_pkt[4]};
      offered_capture={pending_pkt[7],pending_pkt[6]};
      // Signed age from BEGIN plus upload time. Negative tolerances do not
      // grant more than FRESH_US of control lifetime.
      header_ok=(pending_pkt[0]==32'h3146504b)&&(pending_pkt[1]==1)&&(pending_pkt[2]==384)&&
        (pending_pkt[3]!=0)&&(offered_seq!=0)&&(quality<=3)&&
        ((pending_pkt[10]&32'hfffffff8)==0)&&((pending_pkt[11]&32'hfffffff8)==0)&&
        (pending_pkt[18]==900000)&&((pending_pkt[19]==1)||(pending_pkt[19]==2))&&
        (((pending_pkt[20][15:0]>=16)&&(pending_pkt[20][15:0]<=4096)&&
          (pending_pkt[20][31:16]>=16)&&(pending_pkt[20][31:16]<=4096)) ||
          (gap_flag && quality==3 && pending_pkt[20]==0))&&(pending_pkt[21]==96)&&
        (pending_pkt[22]==32)&&(pending_pkt[23]==256);
      payload_ok=1; matrix_zero=1; all_mask_one=1; all_mask_zero=1; mask_any=0;
      nonzero_count=0;
      for(i=24;i<88;i=i+1) for(j=0;j<4;j=j+1) begin
        if(pending_pkt[i][j*8+:8]>4) payload_ok=0;
        if(pending_pkt[i][j*8+:8]!=0) matrix_zero=0;
        if(pending_pkt[i][j*8+:8]!=0) nonzero_count=nonzero_count+1;
      end
      for(i=88;i<96;i=i+1) begin
        if(pending_pkt[i]!=32'hffffffff) all_mask_one=0;
        if(pending_pkt[i]!=0) all_mask_zero=0;
        if(pending_pkt[i]!=0) mask_any=1;
      end
      payload_ok=payload_ok && side_valid(pending_pkt[12],pending_pkt[13],pending_pkt[14]) &&
        side_valid(pending_pkt[15],pending_pkt[16],pending_pkt[17]);
      semantic_ok=1;
      if(quality==0) semantic_ok=all_mask_zero && (pending_pkt[14]==5) && (pending_pkt[17]==5) &&
        fresh_flag && !gap_flag && (pending_pkt[11]==0);
      if(quality==1) semantic_ok=mask_any || pending_pkt[14][1] || pending_pkt[17][1];
      if(quality>=2) semantic_ok=matrix_zero && all_mask_one;
      if(quality<3) semantic_ok=semantic_ok && fresh_flag && !gap_flag && pending_pkt[11]==0;
      if(quality==3) semantic_ok=semantic_ok && !fresh_flag;
      if(gap_flag) semantic_ok=semantic_ok && (quality==3) && !fresh_flag;
      if(!fresh_flag) semantic_ok=semantic_ok && (quality==3);
      is_new_session=(pending_pkt[3]>last_session);
      identity_ok=(is_new_session || ((pending_pkt[3]==last_session)&&(offered_seq>last_seq)&&
        (fresh_flag ? (offered_capture>last_capture):(offered_capture>=last_capture))));
      if(fresh_flag && offered_capture<=last_capture) identity_ok=0;
      if(new_session_required && pending_pkt[3]<=retired_session_floor) identity_ok=0;
      // D1 is a separately identified, CRC-protected digital diagnostic ABI.
      // Capture is explicitly UNKNOWN and flag word has no FRESH semantics.
      // A single nonzero, known source cell is selected; every other cell is
      // disabled for this experiment, never reclassified as safe/free space.
      selected_row=pending_pkt[12]>>4;selected_col=pending_pkt[12]&15;
      selected_block=(selected_col<8)?(selected_row>>1):(8+(selected_row>>1));
      selected_channel=(selected_row&1)*8+
        ((selected_col<8)?selected_col:((selected_block<=11)?(15-selected_col):(selected_col-8)));
      digital_payload_ok=(pending_pkt[12]<256)&&(pending_pkt[13]<16)&&(pending_pkt[14]<16)&&
        (pending_pkt[13]==selected_block)&&(pending_pkt[14]==selected_channel)&&
        DIGITAL_TARGET_MASK[selected_block]&&(nonzero_count==1)&&
        (pending_pkt[24+(pending_pkt[12]>>2)][(pending_pkt[12]&3)*8+:8]!=0)&&
        !pending_pkt[88+(pending_pkt[12]>>5)][pending_pkt[12]&31];
      // DGM2 word12 explicitly enables PCA blocks, word13 caps output level,
      // word14 is reserved. Every commanded nonzero is checked, not just one
      // selected cell. Unknown/disabled cells remain zero commands, never an
      // assertion of observed-safe space; the original UNKNOWN map is retained.
      digital_matrix_payload_ok=(pending_pkt[12][31:16]==0)&&
        (pending_pkt[12][15:0]!=0)&&
        ((pending_pkt[12][15:0]&~DIGITAL_TARGET_MASK)==0)&&
        (pending_pkt[13]>=1)&&(pending_pkt[13]<=4)&&(pending_pkt[14]==0)&&
        !all_mask_one&&((quality!=0)||all_mask_zero);
      matrix_source_index=0;matrix_block=0;
      for(i=24;i<88;i=i+1)for(j=0;j<4;j=j+1)begin
        matrix_source_index=(i-24)*4+j;
        matrix_block=((matrix_source_index&15)<8)?(matrix_source_index>>5):
          (8+(matrix_source_index>>5));
        if(pending_pkt[i][j*8+:8]!=0 &&
           (!pending_pkt[12][matrix_block] ||
            pending_pkt[i][j*8+:8]>pending_pkt[13] ||
            pending_pkt[88+(matrix_source_index>>5)][matrix_source_index&31]))
          digital_matrix_payload_ok=0;
      end
      if(DIGITAL_BRINGUP_MODE)begin
        header_ok=(pending_pkt[0]==(DIGITAL_MATRIX_MODE?32'h324d4744:32'h31444744))&&
          (pending_pkt[1]==(DIGITAL_MATRIX_MODE?2:1))&&(pending_pkt[2]==384)&&
          pending_pkt[3]!=0&&offered_seq!=0&&pending_pkt[6]==0&&pending_pkt[7]==0&&
          pending_pkt[9]<=1&&pending_pkt[10]==3&&
          pending_pkt[15]==0&&pending_pkt[16]==0&&pending_pkt[17]==0&&
          pending_pkt[18]==900000&&(pending_pkt[19]==1||pending_pkt[19]==2)&&
          pending_pkt[20][15:0]>=16&&pending_pkt[20][15:0]<=4096&&
          pending_pkt[20][31:16]>=16&&pending_pkt[20][31:16]<=4096&&
          pending_pkt[21]==96&&pending_pkt[22]==32&&pending_pkt[23]==256;
        payload_ok=1;
        for(i=24;i<88;i=i+1)for(j=0;j<4;j=j+1)
          if(pending_pkt[i][j*8+:8]>4)payload_ok=0;
        semantic_ok=DIGITAL_MATRIX_MODE?digital_matrix_payload_ok:digital_payload_ok;
        identity_ok=is_new_session||((pending_pkt[3]==last_session)&&(offered_seq>last_seq));
        if(new_session_required&&pending_pkt[3]<=retired_session_floor)identity_ok=0;
      end
    end

    task automatic inhibit(input [31:0] why, input require_new);
      begin
        control_valid_q<=0; active_valid_o<=0; resume_required<=1;
        if(require_new) begin
          new_session_required<=1;
          if(last_session>retired_session_floor) retired_session_floor<=last_session;
        end
        abort_latched<=1; reason<=why;
      end
    endtask
    integer k;
    always @(posedge clk) begin
      if(!rst_n) begin
        state<=IDLE; seen<=0; rx_open<=0; resume_required<=1; new_session_required<=0;
        protocol_fault<=0; reason<=F_NONE; abort_latched<=0;
        now_us<=0; begin_us<=0; activated_us<=0; control_us<=0; bridge_us<=0; divider<=0;
        active_initial_age<=0; control_initial_age<=0;
        last_session<=0; retired_session_floor<=0; last_seq<=0; last_capture<=0;
        bridge_index<=0; master_aw_done<=0; master_w_done<=0; expected_legacy_gen<=0;
        quarantine_tx<=0; quarantine_resp<=0;
        aw_have<=0; w_have<=0; awaddr_q<=0; wdata_q<=0; wstrb_q<=0;
        s_bresp<=0; s_bvalid<=0; s_rresp<=0; s_rvalid<=0; s_rdata<=0;
        control_valid_q<=0; active_valid_o<=0; observation_gen_o<=0;
        control_session_o<=0; control_seq_o<=0;
        control_commit_pulse_o<=0;
        crc_verified<=0;crc_index<=0;crc_acc<=32'hffffffff;
        audit_read_active<=0;audit_read_count<=0;audit_read_base<=0;
        for(k=0;k<96;k=k+1) begin pending_pkt[k]<=0; active_pkt[k]<=0; end
      end else begin
        control_commit_pulse_o<=0;
        if(divider==TICKS_PER_US-1) begin now_us<=now_us+1; divider<=0; end
        else divider<=divider+1;
        if(s_awvalid&&s_awready) begin aw_have<=1; awaddr_q<=s_awaddr; end
        if(s_wvalid&&s_wready) begin w_have<=1; wdata_q<=s_wdata; wstrb_q<=s_wstrb; end
        if(s_bvalid&&s_bready) begin s_bvalid<=0; aw_have<=0; w_have<=0; end
        if(s_rvalid&&s_rready) s_rvalid<=0;
        if(audit_read_active)begin
          if(audit_read_count>=1)s_rdata<={audit_matrix_data_i,s_rdata[31:8]};
          if(audit_read_count==4)begin audit_read_active<=0;s_rvalid<=1;s_rresp<=0;end
          else audit_read_count<=audit_read_count+1;
        end
        if(s_arvalid&&s_arready) begin
          s_rvalid<=1; s_rresp<=0;
          if(s_araddr[1:0]!=0) begin s_rdata<=0; s_rresp<=2'b10; end
          else if(s_araddr>=12'he00 && s_araddr<=12'hefc)begin
            s_rvalid<=0;audit_read_active<=1;audit_read_count<=0;
            audit_read_base<={s_araddr[7:2],2'b0};
          end
          else if(s_araddr>=12'h400 && s_araddr<=12'h57c) s_rdata<=active_pkt[(s_araddr-12'h400)>>2];
          else case(s_araddr)
            12'h004:s_rdata<={24'b0,protocol_fault,new_session_required,resume_required,busy,force_off_o,control_valid_o,active_valid_o,rx_open};
            12'h008:s_rdata<=observation_gen_o;
            12'h00c:s_rdata<=reason;
            12'h010:s_rdata<=active_pkt[3];
            12'h014:s_rdata<=active_pkt[4];
            12'h018:s_rdata<=active_pkt[5];
            12'h020:s_rdata<=current_active_age[31:0];
            12'h028:s_rdata<={16'b0,legacy_active_gen_i};
            default:begin s_rdata<=0; s_rresp<=2'b10; end
          endcase
        end
        if(active_valid_o && current_active_age>=LIFETIME_US) inhibit(F_TIMEOUT,1);
        if(rx_open && now_us-begin_us>=LIFETIME_US) begin rx_open<=0; seen<=0; inhibit(F_TIMEOUT,1); end
        if(control_expired) inhibit(F_TIMEOUT,1);
        if((control_valid_q || bridge_inflight) && !local_healthy_i) inhibit(F_LOCAL,1);
        if((control_valid_q || bridge_inflight) && !runtime_permit_i) inhibit(F_PERMIT,1);
        if(bridge_inflight&&(current_transaction_expired || bridge_expired)) inhibit(F_BRIDGE_TIMEOUT,1);
        case(state)
          CHECK:if(terminal_fault_now) begin state<=IDLE; rx_open<=0; seen<=0; end
          else if(!write_failure)begin
            rx_open<=0;
            if(!header_ok) begin state<=IDLE; protocol_fault<=1; inhibit(F_HEADER,0); end
            else if(!payload_ok || !semantic_ok) begin state<=IDLE; protocol_fault<=1; inhibit(F_PAYLOAD,0); end
            else if(!identity_ok) begin state<=IDLE; protocol_fault<=1; inhibit(F_IDENTITY,0); end
            else if(!DIGITAL_BRINGUP_MODE && fresh_flag && !age_legal) begin state<=IDLE; protocol_fault<=1; inhibit(F_AGE,1); end
            else if(DIGITAL_BRINGUP_MODE&&!crc_verified)begin crc_index<=0;crc_acc<=32'hffffffff;state<=DIGITAL_CRC;end
            else begin
              // One clock atomic copy: no metadata, mask or matrix mix.
              for(k=0;k<96;k=k+1) active_pkt[k]<=pending_pkt[k];
              observation_gen_o<=observation_gen_o+1;
              active_initial_age<=DIGITAL_BRINGUP_MODE?0:(offered_age<0?0:offered_age);
              activated_us<=now_us; active_valid_o<=DIGITAL_BRINGUP_MODE?!offered_late:(fresh_flag && !gap_flag && !offered_late);
              last_session<=pending_pkt[3]; last_seq<=offered_seq;
              // Fault/gap events must not lower the monotonic source watermark.
              if(offered_capture>last_capture) last_capture<=offered_capture;
              control_valid_q<=0;
              if(!DIGITAL_BRINGUP_MODE&&(gap_flag || pending_pkt[11]!=0 || !fresh_flag)) begin
                new_session_required<=1; resume_required<=1; abort_latched<=1;
                retired_session_floor<=pending_pkt[3];
                reason<=gap_flag?F_GAP:F_PRODUCER; state<=IDLE;
              end else if(offered_late) begin
                new_session_required<=1; resume_required<=1; abort_latched<=1; reason<=F_LATE; state<=IDLE;
                retired_session_floor<=pending_pkt[3];
              end else if(candidate_control && CONTROL_PATH_ENABLE!=0 && local_healthy_i && runtime_permit_i) begin
                abort_latched<=0; bridge_index<=0; master_aw_done<=0; master_w_done<=0;
                bridge_us<=now_us; expected_legacy_gen<=legacy_active_gen_i+1;
                reason<=F_NONE; state<=BR_TX;
              end else begin
                resume_required<=1; abort_latched<=1;
                reason<=(!local_healthy_i)?F_LOCAL:(!runtime_permit_i)?F_PERMIT:F_QUALITY; state<=IDLE;
              end
            end
          end
          DIGITAL_CRC:begin
            if(now_us-begin_us>=LIFETIME_US)begin state<=IDLE;inhibit(F_TIMEOUT,1);end
            else begin
              crc_acc<=crc_next;
              if(crc_index==383)begin
                if((~crc_next)!=pending_pkt[11])begin state<=IDLE;protocol_fault<=1;inhibit(F_CRC,1);end
                else begin crc_verified<=1;state<=CHECK;end
              end else crc_index<=crc_index+1;
            end
          end
          BR_TX:begin
            if(m_awvalid&&m_awready) master_aw_done<=1;
            if(m_wvalid&&m_wready) master_w_done<=1;
            if((master_aw_done||(m_awvalid&&m_awready)) && (master_w_done||(m_wvalid&&m_wready))) state<=BR_RESP;
            if(bridge_expired) begin
              state<=QUARANTINE; quarantine_tx<=1; quarantine_resp<=0;
              inhibit(F_BRIDGE_TIMEOUT,1);
            end
          end
          BR_RESP:if(m_bvalid) begin
            if(m_bresp!=0 || abort_latched || current_transaction_expired || bridge_expired || !local_healthy_i || !runtime_permit_i || write_failure) begin
              state<=IDLE; inhibit(m_bresp!=0?F_LEGACY:F_LOCAL,1);
            end else if(bridge_index==64) state<=BR_GRANT;
            else begin bridge_index<=bridge_index+1; master_aw_done<=0; master_w_done<=0; state<=BR_TX; end
          end else if(bridge_expired) begin state<=QUARANTINE; quarantine_tx<=0; quarantine_resp<=1; inhibit(F_BRIDGE_TIMEOUT,1); end
          BR_GRANT:begin
            if(abort_latched || !local_healthy_i || !runtime_permit_i || current_transaction_expired || bridge_expired || write_failure) begin
              state<=IDLE; inhibit(F_LOCAL,1);
            end else if(legacy_grant_o) state<=BR_CONFIRM;
          end
          BR_CONFIRM:begin
            if(abort_latched || !local_healthy_i || !runtime_permit_i || current_transaction_expired || bridge_expired || write_failure) begin
              state<=IDLE; inhibit(F_LOCAL,1);
            end else if(legacy_active_gen_i==expected_legacy_gen) begin
              control_valid_q<=1; control_session_o<=active_pkt[3]; control_seq_o<={active_pkt[5],active_pkt[4]};
              control_commit_pulse_o<=1;
              control_us<=activated_us; control_initial_age<=active_initial_age;
              resume_required<=0; new_session_required<=0; abort_latched<=0;
              state<=IDLE; reason<=F_NONE;
            end
          end
          QUARANTINE:begin
            // Finish only the already-issued AXI transaction if the peer ever
            // returns. This interface remains quarantined until reset.
            if(quarantine_tx) begin
              if(m_awvalid&&m_awready) master_aw_done<=1;
              if(m_wvalid&&m_wready) master_w_done<=1;
              if((master_aw_done||(m_awvalid&&m_awready)) && (master_w_done||(m_wvalid&&m_wready))) begin quarantine_tx<=0; quarantine_resp<=1; end
            end
            if(quarantine_resp && m_bvalid) quarantine_resp<=0;
            control_valid_q<=0; active_valid_o<=0; abort_latched<=1;
          end
          default:begin end
        endcase
        if(wexec) begin
          s_bvalid<=1; s_bresp<=0;
          if(write_failure) begin
            s_bresp<=2'b10; protocol_fault<=1; rx_open<=0;
            if(state==CHECK) state<=IDLE;
            inhibit(F_WRITE,0);
          end else if(awaddr_q==12'h000) begin
            case(wdata_q)
              1:if(rx_open) begin
                s_bresp<=2'b10; rx_open<=0; seen<=0; protocol_fault<=1; inhibit(F_COMMAND,0);
              end else begin rx_open<=1; seen<=0; begin_us<=now_us; end
              2:begin
                rx_open<=0;
                if(rx_open && (&seen))begin state<=CHECK;crc_verified<=0;end
                else begin s_bresp<=2'b10; protocol_fault<=1; inhibit(F_INCOMPLETE,0); end
              end
              3:begin rx_open<=0; seen<=0; inhibit(F_COMMAND,0); end
              default:begin s_bresp<=2'b10; protocol_fault<=1; rx_open<=0; inhibit(F_COMMAND,0); end
            endcase
          end else if(!rx_open || seen[(awaddr_q-12'h100)>>2]) begin
            s_bresp<=2'b10; protocol_fault<=1; rx_open<=0; inhibit(F_WRITE,0);
          end else begin pending_pkt[(awaddr_q-12'h100)>>2]<=wdata_q; seen[(awaddr_q-12'h100)>>2]<=1; end
        end
      end
    end
endmodule
