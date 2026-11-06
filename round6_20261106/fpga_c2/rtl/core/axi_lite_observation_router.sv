// R5 observation extension: one write and one read in flight. No control policy.
// The existing v1 slave is untouched; only explicitly reserved OBS2 offsets go
// to the independent observer. AW/W may arrive in either order and are latched.
`timescale 1ns/1ps
module axi_lite_observation_router (
    input wire clk, input wire rst_n,
    // Legacy payload may stay reset while the independent bus/observer runs.
    input wire legacy_ready_i,
    input wire [11:0] s_awaddr, input wire s_awvalid, output wire s_awready,
    input wire [31:0] s_wdata, input wire [3:0] s_wstrb,
    input wire s_wvalid, output wire s_wready,
    output wire [1:0] s_bresp, output wire s_bvalid, input wire s_bready,
    input wire [11:0] s_araddr, input wire s_arvalid, output wire s_arready,
    output wire [31:0] s_rdata, output wire [1:0] s_rresp,
    output wire s_rvalid, input wire s_rready,
    output wire [11:0] v_awaddr, output wire v_awvalid, input wire v_awready,
    output wire [31:0] v_wdata, output wire [3:0] v_wstrb,
    output wire v_wvalid, input wire v_wready,
    input wire [1:0] v_bresp, input wire v_bvalid, output wire v_bready,
    output wire [11:0] v_araddr, output wire v_arvalid, input wire v_arready,
    input wire [31:0] v_rdata, input wire [1:0] v_rresp,
    input wire v_rvalid, output wire v_rready,
    output wire [11:0] o_awaddr, output wire o_awvalid, input wire o_awready,
    output wire [31:0] o_wdata, output wire [3:0] o_wstrb,
    output wire o_wvalid, input wire o_wready,
    input wire [1:0] o_bresp, input wire o_bvalid, output wire o_bready,
    output wire [11:0] o_araddr, output wire o_arvalid, input wire o_arready,
    input wire [31:0] o_rdata, input wire [1:0] o_rresp,
    input wire o_rvalid, output wire o_rready
);
    function automatic observation_address(input [11:0] a);
      begin observation_address=((a>=12'h080)&&(a<=12'h0bf)) ||
                                ((a>=12'h800)&&(a<=12'hbff)); end
    endfunction
    reg aw_have, w_have, wr_active, wr_target, aw_sent, w_sent;
    reg [11:0] aw_q;
    reg [31:0] wd_q; reg [3:0] ws_q;
    reg rd_active, rd_target, ar_sent;
    reg wr_aborted,rd_aborted;
    reg wr_response_valid,rd_response_valid;
    reg [1:0] wr_response,rd_response;
    reg [31:0] rd_response_data;
    wire wr_legacy_error=wr_active && !wr_target && (wr_aborted || !legacy_ready_i);
    wire rd_legacy_error=rd_active && !rd_target && (rd_aborted || !legacy_ready_i);
    reg [11:0] ar_q;
    assign s_awready=rst_n && !aw_have && !wr_active;
    assign s_wready=rst_n && !w_have && !wr_active;
    assign v_awaddr=aw_q; assign o_awaddr=aw_q;
    assign v_wdata=wd_q; assign o_wdata=wd_q;
    assign v_wstrb=ws_q; assign o_wstrb=ws_q;
    assign v_awvalid=wr_active && !wr_target && legacy_ready_i && !wr_aborted && !aw_sent;
    assign o_awvalid=wr_active && wr_target && !aw_sent;
    assign v_wvalid=wr_active && !wr_target && legacy_ready_i && !wr_aborted && !w_sent;
    assign o_wvalid=wr_active && wr_target && !w_sent;
    assign s_bvalid=wr_active && wr_response_valid;
    assign s_bresp=wr_response;
    assign v_bready=wr_active && !wr_target && !wr_legacy_error && aw_sent && w_sent && !wr_response_valid;
    assign o_bready=wr_active && wr_target && aw_sent && w_sent && !wr_response_valid;
    assign s_arready=rst_n && !rd_active;
    assign v_araddr=ar_q; assign o_araddr=ar_q;
    assign v_arvalid=rd_active && !rd_target && legacy_ready_i && !rd_aborted && !ar_sent;
    assign o_arvalid=rd_active && rd_target && !ar_sent;
    assign s_rvalid=rd_active && rd_response_valid;
    assign s_rdata=rd_response_data;
    assign s_rresp=rd_response;
    assign v_rready=rd_active && !rd_target && !rd_legacy_error && ar_sent && !rd_response_valid;
    assign o_rready=rd_active && rd_target && ar_sent && !rd_response_valid;
    always @(posedge clk) begin
      if(!rst_n) begin
        aw_have<=0;w_have<=0;wr_active<=0;wr_target<=0;aw_sent<=0;w_sent<=0;
        aw_q<=0;wd_q<=0;ws_q<=0;rd_active<=0;rd_target<=0;ar_sent<=0;ar_q<=0;wr_aborted<=0;rd_aborted<=0;
        wr_response_valid<=0;rd_response_valid<=0;wr_response<=0;rd_response<=0;rd_response_data<=0;
      end else begin
        if(wr_active&&!wr_response_valid)begin
          if(wr_legacy_error)begin wr_response_valid<=1;wr_response<=2'b10;end
          else if(aw_sent&&w_sent&&(wr_target?o_bvalid:v_bvalid))begin
            wr_response_valid<=1;wr_response<=wr_target?o_bresp:v_bresp;
          end
        end
        if(rd_active&&!rd_response_valid)begin
          if(rd_legacy_error)begin rd_response_valid<=1;rd_response<=2'b10;rd_response_data<=0;end
          else if(ar_sent&&(rd_target?o_rvalid:v_rvalid))begin
            rd_response_valid<=1;rd_response<=rd_target?o_rresp:v_rresp;
            rd_response_data<=rd_target?o_rdata:v_rdata;
          end
        end
        if(wr_active&&!wr_target&&!legacy_ready_i)wr_aborted<=1;
        if(rd_active&&!rd_target&&!legacy_ready_i)rd_aborted<=1;
        if(s_awvalid&&s_awready)begin aw_have<=1;aw_q<=s_awaddr;end
        if(s_wvalid&&s_wready)begin w_have<=1;wd_q<=s_wdata;ws_q<=s_wstrb;end
        if(aw_have&&w_have&&!wr_active)begin
          wr_active<=1;wr_target<=observation_address(aw_q);aw_sent<=0;w_sent<=0;wr_aborted<=0;wr_response_valid<=0;
        end
        if((v_awvalid&&v_awready)||(o_awvalid&&o_awready))aw_sent<=1;
        if((v_wvalid&&v_wready)||(o_wvalid&&o_wready))w_sent<=1;
        if(s_bvalid&&s_bready)begin
          aw_have<=0;w_have<=0;wr_active<=0;aw_sent<=0;w_sent<=0;wr_aborted<=0;wr_response_valid<=0;
        end
        if(s_arvalid&&s_arready)begin
          ar_q<=s_araddr;rd_active<=1;rd_target<=observation_address(s_araddr);ar_sent<=0;rd_aborted<=0;rd_response_valid<=0;
        end
        if((v_arvalid&&v_arready)||(o_arvalid&&o_arready))ar_sent<=1;
        if(s_rvalid&&s_rready)begin rd_active<=0;ar_sent<=0;rd_aborted<=0;rd_response_valid<=0;end
      end
    end
endmodule
