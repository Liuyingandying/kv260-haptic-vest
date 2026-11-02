// Digital containment only: I2C FULL_OFF cannot stop a physically stuck bus.
`timescale 1ns / 1ps
module fault_recovery_ctrl #(
    parameter integer CLK_FREQ = 50_000_000,
    parameter integer RETRY_MS = 10
)(
    input wire clk, rst_n,
    input wire init_busy, init_done, init_healthy, fault_latched,
    input wire [15:0] off_pending,
    input wire pipeline_idle, runtime_busy, fault_clear,
    output wire runtime_run, safety_off,
    output reg init_restart,
    output wire [2:0] state_o
);
    localparam WAIT_INIT=3'd0, RUN=3'd1, QUIESCE=3'd2,
               OFF_SCAN=3'd3, BACKOFF=3'd4, HOLD=3'd5, REINIT=3'd6;
    localparam integer RETRY_CYCLES = (CLK_FREQ/1000)*RETRY_MS;
    reg [2:0] state;
    reg [31:0] delay_count;
    reg scan_started, reinit_started;
    assign runtime_run = ((state==RUN) && !fault_latched) || (state==OFF_SCAN);
    assign safety_off = (state==OFF_SCAN);
    assign state_o = state;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state<=WAIT_INIT; delay_count<=0; scan_started<=0;
            reinit_started<=0; init_restart<=0;
        end else begin
            init_restart<=0;
            case(state)
                WAIT_INIT: if(init_done && !init_busy)
                    state<=init_healthy ? RUN : QUIESCE;
                RUN: if(fault_latched) state<=QUIESCE;
                QUIESCE: if(pipeline_idle && !init_busy) begin
                    scan_started<=0; state<=OFF_SCAN;
                end
                OFF_SCAN: begin
                    if(runtime_busy) scan_started<=1;
                    if(scan_started && pipeline_idle) begin
                        if(off_pending==0) state<=HOLD;
                        else begin delay_count<=RETRY_CYCLES; state<=BACKOFF; end
                    end
                end
                BACKOFF: if(delay_count==0) state<=QUIESCE;
                         else delay_count<=delay_count-1;
                HOLD: if(fault_clear && off_pending==0 && pipeline_idle) begin
                    init_restart<=1; reinit_started<=0; state<=REINIT;
                end
                REINIT: begin
                    if(init_busy) reinit_started<=1;
                    if(reinit_started && init_done && !init_busy) begin
                        if(!init_healthy) state<=QUIESCE;
                        else if(!fault_latched) state<=RUN;
                    end
                end
                default: state<=WAIT_INIT;
            endcase
        end
    end
endmodule
