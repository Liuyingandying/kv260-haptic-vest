// R6 candidate: state-owned reset is a dedicated flip-flop, not LUT state decode.
// Based on R5B2 isolated candidate. Digital pad ownership, never physical PWM/OE safety.
// NXP UM10204 rev7 section3.1.16: one nine-clock clear for stuck SDA;
// stuck SCL or unsuccessful clear needs external HW reset/power intervention.
// All outputs mean pull-low intent (1) or release (0), never driven HIGH.
`timescale 1ns/1ps
module i2c_pad_lifecycle_guard #(
    parameter integer CLK_FREQ_HZ=100_000_000,
    parameter integer HALF_PERIOD_US=5,
    parameter integer SETTLE_US=100,
    parameter integer IDLE_STABLE_US=10,
    parameter integer SCL_RISE_TIMEOUT_US=50,
    parameter integer CLEAR_TIMEOUT_US=2000,
    parameter integer DRAIN_TIMEOUT_US=30000
)(
    input wire clk,input wire rst_n,input wire request_i,
    input wire core_busy_i,input wire raw_scl_low_i,input wire raw_sda_low_i,
    input wire scl_i,input wire sda_i,
    output wire pad_enable_o,output reg core_resetn_o,output wire issue_enable_o,
    output reg scl_low_o,output reg sda_low_o,
    output reg fault_o,output reg close_clean_o,
    output wire [31:0] status_o
);
    function automatic [63:0] cycles(input integer us);
      reg[63:0] value;
      begin value=(64'd1*CLK_FREQ_HZ*us+999999)/1000000;cycles=(value<1)?1:value;end
    endfunction
    localparam[63:0] HALF=cycles(HALF_PERIOD_US),SETTLE=cycles(SETTLE_US),
      STABLE=cycles(IDLE_STABLE_US),RISE=cycles(SCL_RISE_TIMEOUT_US),
      CLEAR_LIMIT=cycles(CLEAR_TIMEOUT_US),DRAIN_LIMIT=cycles(DRAIN_TIMEOUT_US);
    localparam[3:0] OFF=0,OPEN=1,CLEAR_LOW=2,CLEAR_HIGH=3,
      STOP_PREP=4,STOP_LOW=5,STOP_HIGH=6,VERIFY=7,RUN=8,DRAIN=9,FAULT=10;
    localparam[3:0] NONE=0,SCL_STUCK=1,SDA_STUCK=2,DRAIN_TIMEOUT=3,CLEAR_TIMEOUT=4,SCL_NOT_LOW=5;
    reg[3:0] state,clear_pulses,reason;
    reg[31:0] phase_count,wait_count,stable_count,operation_count;
    assign pad_enable_o=(state!=OFF)&&(state!=FAULT);
    // Registered reset output: assertion/release changes only at clk edges.
    // No multi-bit state decode is allowed to drive downstream asynchronous reset pins.
    // Revoke issue immediately, but retain ownership until the old master drains.
    assign issue_enable_o=(state==RUN)&&request_i;
    assign status_o={12'b0,reason,sda_i,scl_i,(state==DRAIN),close_clean_o,
                     clear_pulses,fault_o,issue_enable_o,core_resetn_o,pad_enable_o,state};
    always @* begin
      scl_low_o=0;sda_low_o=0;
      case(state)
        CLEAR_LOW,STOP_PREP:scl_low_o=1;
        STOP_LOW:begin scl_low_o=1;sda_low_o=1;end
        STOP_HIGH:sda_low_o=1;
        RUN,DRAIN:begin scl_low_o=raw_scl_low_i;sda_low_o=raw_sda_low_i;end
        default:begin end
      endcase
    end
    task automatic fail(input[3:0] why);
      begin state<=FAULT;core_resetn_o<=0;fault_o<=1;close_clean_o<=0;reason<=why;end
    endtask
    always @(posedge clk)begin
      if(!rst_n)begin
        state<=OFF;core_resetn_o<=0;clear_pulses<=0;reason<=NONE;fault_o<=0;close_clean_o<=0;
        phase_count<=0;wait_count<=0;stable_count<=0;operation_count<=0;
      end else begin
        if(state>=OPEN&&state<=VERIFY)operation_count<=operation_count+1;
        else operation_count<=0;
        // A single bounded clear session. Noise/stretch cannot extend it forever.
        if(state>=OPEN&&state<=VERIFY&&operation_count>=CLEAR_LIMIT)fail(CLEAR_TIMEOUT);
        else case(state)
          OFF:begin
            core_resetn_o<=0;
            phase_count<=0;wait_count<=0;stable_count<=0;
            if(request_i)begin
              state<=OPEN;fault_o<=0;close_clean_o<=0;reason<=NONE;clear_pulses<=0;
            end
          end
          OPEN:begin
            if(wait_count+1<SETTLE)wait_count<=wait_count+1;
            else begin
              wait_count<=0;phase_count<=0;
              if(!scl_i)fail(SCL_STUCK);
              else if(!sda_i)state<=CLEAR_LOW;
              else begin state<=VERIFY;stable_count<=0;end
            end
          end
          CLEAR_LOW:begin
            if(scl_i)begin
              phase_count<=0;
              if(wait_count+1>=RISE)fail(SCL_NOT_LOW);else wait_count<=wait_count+1;
            end else begin
              wait_count<=0;
              if(phase_count+1>=HALF)begin phase_count<=0;state<=CLEAR_HIGH;end
              else phase_count<=phase_count+1;
            end
          end
          CLEAR_HIGH:begin
            if(!scl_i)begin
              phase_count<=0;
              if(wait_count+1>=RISE)fail(SCL_STUCK);else wait_count<=wait_count+1;
            end else begin
              wait_count<=0;
              if(phase_count+1>=HALF)begin
                clear_pulses<=clear_pulses+1'b1;phase_count<=0;
                state<=(clear_pulses==8)?STOP_PREP:CLEAR_LOW;
              end else phase_count<=phase_count+1;
            end
          end
          // First make SCL LOW, then change SDA. Do not manufacture a START
          // by dropping SDA and SCL simultaneously from the ninth high phase.
          STOP_PREP:begin
            if(scl_i)begin
              phase_count<=0;
              if(wait_count+1>=RISE)fail(SCL_NOT_LOW);else wait_count<=wait_count+1;
            end else begin
              wait_count<=0;
              if(phase_count+1>=HALF)begin phase_count<=0;state<=STOP_LOW;end
              else phase_count<=phase_count+1;
            end
          end
          STOP_LOW:begin
            if(phase_count+1>=HALF)begin phase_count<=0;wait_count<=0;state<=STOP_HIGH;end
            else phase_count<=phase_count+1;
          end
          STOP_HIGH:begin
            if(!scl_i)begin
              phase_count<=0;
              if(wait_count+1>=RISE)fail(SCL_STUCK);else wait_count<=wait_count+1;
            end else begin
              wait_count<=0;
              if(phase_count+1>=HALF)begin
                // Releasing SDA with observed SCL HIGH is the STOP edge.
                phase_count<=0;wait_count<=0;stable_count<=0;state<=VERIFY;
              end else phase_count<=phase_count+1;
            end
          end
          VERIFY:begin
            if(scl_i&&sda_i)begin
              if(stable_count+1>=STABLE)begin
                stable_count<=0;wait_count<=0;
                if(request_i)begin state<=RUN;core_resetn_o<=1;end
                else begin state<=OFF;core_resetn_o<=0;close_clean_o<=1;end
              end else stable_count<=stable_count+1;
            end else begin
              stable_count<=0;
              if(wait_count+1>=SETTLE)fail(scl_i?SDA_STUCK:SCL_STUCK);
              else wait_count<=wait_count+1;
            end
          end
          RUN:begin
            wait_count<=0;stable_count<=0;
            if(!request_i)state<=DRAIN;
          end
          DRAIN:begin
            if(!core_busy_i&&!raw_scl_low_i&&!raw_sda_low_i&&scl_i&&sda_i)begin
              if(stable_count+1>=STABLE)begin state<=OFF;core_resetn_o<=0;close_clean_o<=1;stable_count<=0;end
              else stable_count<=stable_count+1;
            end else stable_count<=0;
            if(wait_count+1>=DRAIN_LIMIT)fail(DRAIN_TIMEOUT);
            else wait_count<=wait_count+1;
          end
          FAULT:begin
            // Released pads are not a verified FULL_OFF. No autonomous retry.
            // An explicit low request is required before a new manual arm.
            if(!request_i)state<=OFF;
          end
          default:fail(CLEAR_TIMEOUT);
        endcase
      end
    end
endmodule
