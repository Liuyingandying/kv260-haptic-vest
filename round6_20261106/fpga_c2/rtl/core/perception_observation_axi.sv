// OBS2 independent observation bank. This module has NO legacy matrix/control
// outputs: nonzero uncertain observations can be inspected, never authorized.
// Host receipt recency is explicitly not exposure/capture freshness.
`timescale 1ns/1ps
module perception_observation_axi #(
    parameter integer TICKS_PER_US=100,
    parameter integer RECEIPT_LIMIT_US=250000
)(
    input wire clk,input wire rst_n,
    input wire [11:0] s_awaddr,input wire s_awvalid,output wire s_awready,
    input wire [31:0] s_wdata,input wire [3:0] s_wstrb,
    input wire s_wvalid,output wire s_wready,
    output reg [1:0] s_bresp,output reg s_bvalid,input wire s_bready,
    input wire [11:0] s_araddr,input wire s_arvalid,output wire s_arready,
    output reg [31:0] s_rdata,output reg [1:0] s_rresp,
    output reg s_rvalid,input wire s_rready
);
    localparam IDLE=0,CHECK=1,CRC=2,ACTIVATE=3;
    localparam F_NONE=0,F_WRITE=1,F_COMMAND=2,F_INCOMPLETE=3,F_HEADER=4,
               F_PAYLOAD=5,F_IDENTITY=6,F_AGE=7,F_CRC=17,F_ABORT=18;
    reg [1:0] state;
    reg [31:0] pending_pkt[0:95],active_pkt[0:95];
    reg [95:0] seen;
    reg rx_open,obs_present,error_flag;
    reg [31:0] generation,reason,last_session;
    reg [63:0] last_sequence,last_receipt;
    reg [63:0] now_us,begin_us,activated_us;
    reg [31:0] divider,active_initial_age;
    reg aw_have,w_have;
    reg [11:0] aw_q;reg [31:0] wd_q;reg [3:0] ws_q;
    reg [8:0] crc_index;
    reg [31:0] crc_acc;
    wire busy=(state!=IDLE);
    wire wexec=aw_have&&w_have&&!s_bvalid;
    wire [63:0] current_age={32'b0,active_initial_age}+(now_us-activated_us);
    wire receipt_recent=obs_present && current_age<RECEIPT_LIMIT_US;
    wire [63:0] offered_age={32'b0,pending_pkt[8]}+(now_us-begin_us);
    assign s_awready=rst_n&&!aw_have&&!s_bvalid;
    assign s_wready=rst_n&&!w_have&&!s_bvalid;
    assign s_arready=rst_n&&!s_rvalid;
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
    function automatic side_valid(input [31:0] kind,distance_mm,flags);
      begin
        side_valid=(kind<=5)&&((flags==2)||(flags==3)||(flags==5)||(flags==6));
        if(kind==0)side_valid=side_valid&&((flags==2)||(flags==6))&&(distance_mm==32'hffffffff);
        else side_valid=side_valid&&((flags==3)||(flags==5))&&(distance_mm!=0)&&(distance_mm!=32'hffffffff);
        if(kind==1)side_valid=side_valid&&(flags==5);
        if(kind==5)side_valid=side_valid&&(flags==3);
      end
    endfunction
    reg header_ok,payload_ok,semantic_ok,identity_ok;
    reg all_zero,all_unknown,no_unknown;
    reg [63:0] offered_sequence,offered_receipt;
    integer i,j;
    always @* begin
      offered_sequence={pending_pkt[5],pending_pkt[4]};
      offered_receipt={pending_pkt[7],pending_pkt[6]};
      header_ok=pending_pkt[0]==32'h324f5047 && pending_pkt[1]==2 && pending_pkt[2]==384 &&
        pending_pkt[3]!=0 && offered_sequence!=0 && offered_receipt!=0 && pending_pkt[8]<=RECEIPT_LIMIT_US &&
        pending_pkt[9]<=3 && pending_pkt[10]==7 && pending_pkt[18]==900000 &&
        (pending_pkt[19]==1||pending_pkt[19]==2) &&
        pending_pkt[20][15:0]>=16 && pending_pkt[20][15:0]<=4096 &&
        pending_pkt[20][31:16]>=16 && pending_pkt[20][31:16]<=4096 &&
        pending_pkt[21]==96 && pending_pkt[22]==32 && pending_pkt[23]==256;
      payload_ok=1;all_zero=1;all_unknown=1;no_unknown=1;
      for(i=24;i<88;i=i+1)for(j=0;j<4;j=j+1)begin
        if(pending_pkt[i][j*8+:8]>4)payload_ok=0;
        if(pending_pkt[i][j*8+:8]!=0)all_zero=0;
      end
      for(i=88;i<96;i=i+1)begin
        if(pending_pkt[i]!=32'hffffffff)all_unknown=0;
        if(pending_pkt[i]!=0)no_unknown=0;
      end
      payload_ok=payload_ok&&side_valid(pending_pkt[12],pending_pkt[13],pending_pkt[14])&&
                             side_valid(pending_pkt[15],pending_pkt[16],pending_pkt[17]);
      semantic_ok=1;
      if(pending_pkt[9]==0)semantic_ok=no_unknown&&pending_pkt[14]==5&&pending_pkt[17]==5;
      if(pending_pkt[9]==1)semantic_ok=!no_unknown||pending_pkt[14][1]||pending_pkt[17][1];
      if(pending_pkt[9]>=2)semantic_ok=all_zero&&all_unknown;
      identity_ok=pending_pkt[3]>last_session ||
          (pending_pkt[3]==last_session&&offered_sequence>last_sequence&&offered_receipt>last_receipt);
    end
    task automatic fail_frame(input [31:0] failure);
      begin state<=IDLE;rx_open<=0;seen<=0;error_flag<=1;reason<=failure;end
    endtask
    integer k;
    always @(posedge clk) begin
      if(!rst_n)begin
        state<=IDLE;seen<=0;rx_open<=0;obs_present<=0;error_flag<=0;
        generation<=0;reason<=0;last_session<=0;last_sequence<=0;last_receipt<=0;
        now_us<=0;begin_us<=0;activated_us<=0;divider<=0;active_initial_age<=0;
        aw_have<=0;w_have<=0;aw_q<=0;wd_q<=0;ws_q<=0;
        s_bvalid<=0;s_bresp<=0;s_rvalid<=0;s_rresp<=0;s_rdata<=0;
        crc_index<=0;crc_acc<=32'hffffffff;
        for(k=0;k<96;k=k+1)begin pending_pkt[k]<=0;active_pkt[k]<=0;end
      end else begin
        if(divider==TICKS_PER_US-1)begin divider<=0;now_us<=now_us+1;end
        else divider<=divider+1;
        if(s_awvalid&&s_awready)begin aw_have<=1;aw_q<=s_awaddr;end
        if(s_wvalid&&s_wready)begin w_have<=1;wd_q<=s_wdata;ws_q<=s_wstrb;end
        if(s_bvalid&&s_bready)begin s_bvalid<=0;aw_have<=0;w_have<=0;end
        if(s_rvalid&&s_rready)s_rvalid<=0;
        if(s_arvalid&&s_arready)begin
          s_rvalid<=1;s_rresp<=0;s_rdata<=0;
          if(s_araddr[1:0]!=0)s_rresp<=2'b10;
          else if(s_araddr>=12'ha00&&s_araddr<=12'hb7c)
            s_rdata<=active_pkt[(s_araddr-12'ha00)>>2];
          else case(s_araddr)
            12'h084:s_rdata<={24'b0,1'b1,error_flag,receipt_recent,busy,1'b1,1'b0,obs_present,rx_open};
            12'h088:s_rdata<=generation;
            12'h08c:s_rdata<=reason;
            12'h090:s_rdata<=active_pkt[3];
            12'h094:s_rdata<=active_pkt[4];
            12'h098:s_rdata<=active_pkt[5];
            12'h09c:s_rdata<=(current_age > 64'hffffffff) ? 32'hffffffff : current_age[31:0];
            12'h0a0:s_rdata<=active_pkt[11];
            12'h0a4:s_rdata<=32'h324f5047;
            default:s_rresp<=2'b10;
          endcase
        end
        if(rx_open&&now_us-begin_us>=RECEIPT_LIMIT_US)fail_frame(F_AGE);
        if(!wexec)begin case(state)
          CHECK:begin
            if(!header_ok)fail_frame(F_HEADER);
            else if(!payload_ok||!semantic_ok)fail_frame(F_PAYLOAD);
            else if(!identity_ok)fail_frame(F_IDENTITY);
            else if(offered_age>=RECEIPT_LIMIT_US)fail_frame(F_AGE);
            else begin crc_acc<=32'hffffffff;crc_index<=0;state<=CRC;end
          end
          CRC:begin
            crc_acc<=crc_next;
            if(crc_index==383)begin
              if((~crc_next)!=pending_pkt[11])fail_frame(F_CRC);
              else state<=ACTIVATE;
            end else crc_index<=crc_index+1;
          end
          ACTIVATE:begin
            if(offered_age>=RECEIPT_LIMIT_US)fail_frame(F_AGE);
            else begin
              for(k=0;k<96;k=k+1)active_pkt[k]<=pending_pkt[k];
              generation<=generation+1;obs_present<=1;error_flag<=0;reason<=F_NONE;
              active_initial_age<=offered_age[31:0];activated_us<=now_us;
              last_session<=pending_pkt[3];last_sequence<=offered_sequence;last_receipt<=offered_receipt;
              state<=IDLE;
            end
          end
          default:begin end
        endcase end
        // Write-side rejection wins over CRC/activation. A bad request cannot
        // race a partially verified frame into the active bank.
        if(wexec)begin
          s_bvalid<=1;s_bresp<=0;
          if(aw_q[1:0]!=0||ws_q!=4'hf)begin s_bresp<=2'b10;fail_frame(F_WRITE);end
          else if(aw_q==12'h080&&wd_q==3)begin
            state<=IDLE;rx_open<=0;seen<=0;error_flag<=0;reason<=F_ABORT;
          end else if(busy)begin s_bresp<=2'b10;fail_frame(F_WRITE);end
          else if(aw_q==12'h080)begin
            if(wd_q==1)begin rx_open<=1;seen<=0;begin_us<=now_us;error_flag<=0;reason<=F_NONE;end
            else if(wd_q==2)begin
              if(!rx_open||seen!={96{1'b1}})begin s_bresp<=2'b10;fail_frame(F_INCOMPLETE);end
              else begin rx_open<=0;state<=CHECK;end
            end else begin s_bresp<=2'b10;fail_frame(F_COMMAND);end
          end else if(aw_q>=12'h800&&aw_q<=12'h97c&&rx_open)begin
            if(seen[(aw_q-12'h800)>>2])begin s_bresp<=2'b10;fail_frame(F_WRITE);end
            else begin pending_pkt[(aw_q-12'h800)>>2]<=wd_q;seen[(aw_q-12'h800)>>2]<=1;end
          end else begin s_bresp<=2'b10;fail_frame(F_WRITE);end
        end
      end
    end
endmodule
