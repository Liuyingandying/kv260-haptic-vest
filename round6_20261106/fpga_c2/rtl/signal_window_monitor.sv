// SIG2 signal-only diagnostic. Clock-cycle deadline, NOT an independent watchdog.
// 5,000,000,000 clocks / >=90 MHz <55.56 s, ONLY while PL0 keeps running.
// Explicit disarm is required after expiry; holding arm high never renews it.
`timescale 1ns/1ps
module signal_window_monitor #(
    parameter [63:0] WINDOW_CYCLES = 64'd5_000_000_000
)(
    input wire clk, input wire rst_n, input wire arm_request_async,
    input wire scl_sense, input wire sda_sense,
    input wire raw_scl_low, input wire raw_sda_low,
    output reg window_active=0, output reg expired=0,
    output wire arm_synced_o, output reg start_pulse_o=0,
    output reg [63:0] elapsed_cycles=0,
    output reg [31:0] scl_rise_count=0, output reg [31:0] scl_fall_count=0,
    output reg [31:0] sda_rise_count=0, output reg [31:0] sda_fall_count=0,
    output reg [31:0] raw_scl_transition_count=0,
    output reg [31:0] raw_sda_transition_count=0
);
    localparam [63:0] LIMIT = WINDOW_CYCLES < 64'd1 ? 64'd1 : WINDOW_CYCLES;
    (* ASYNC_REG="TRUE" *) reg arm_meta=0;
    (* ASYNC_REG="TRUE" *) reg arm_sync=0;
    reg arm_previous=0;
    reg previous_scl=1, previous_sda=1;
    reg previous_raw_scl=0, previous_raw_sda=0;
    assign arm_synced_o=arm_sync;
    always @(posedge clk) begin
        arm_meta <= arm_request_async;
        arm_sync <= arm_meta;
        arm_previous <= arm_sync;
        start_pulse_o <= 0;
        // Track outside the window too; start samples current levels so an
        // initially LOW wire is not invented as a falling edge.
        previous_scl <= scl_sense;
        previous_sda <= sda_sense;
        previous_raw_scl <= raw_scl_low;
        previous_raw_sda <= raw_sda_low;
        if (!rst_n) begin
            arm_meta<=0; arm_sync<=0; arm_previous<=0;
            window_active<=0; expired<=0; elapsed_cycles<=0;
            scl_rise_count<=0; scl_fall_count<=0;
            sda_rise_count<=0; sda_fall_count<=0;
            raw_scl_transition_count<=0; raw_sda_transition_count<=0;
        end else if (!arm_sync) begin
            // Disarm clears the expiry latch, but retains all measurements.
            window_active<=0; expired<=0;
        end else if (!arm_previous && !expired) begin
            window_active<=1; start_pulse_o<=1; elapsed_cycles<=0;
            scl_rise_count<=0; scl_fall_count<=0;
            sda_rise_count<=0; sda_fall_count<=0;
            raw_scl_transition_count<=0; raw_sda_transition_count<=0;
            previous_scl<=scl_sense; previous_sda<=sda_sense;
            previous_raw_scl<=raw_scl_low; previous_raw_sda<=raw_sda_low;
        end else if (window_active) begin
            if (elapsed_cycles + 64'd1 >= LIMIT) begin
                elapsed_cycles<=LIMIT;
                window_active<=0; expired<=1;
            end else elapsed_cycles<=elapsed_cycles+64'd1;
            // Counts are sampled, synchronized digital transitions, not
            // analog measurements or proof of remote TCA/PCA ACK.
            if (scl_sense && !previous_scl && scl_rise_count!=32'hffffffff)
                scl_rise_count<=scl_rise_count+1'b1;
            if (!scl_sense && previous_scl && scl_fall_count!=32'hffffffff)
                scl_fall_count<=scl_fall_count+1'b1;
            if (sda_sense && !previous_sda && sda_rise_count!=32'hffffffff)
                sda_rise_count<=sda_rise_count+1'b1;
            if (!sda_sense && previous_sda && sda_fall_count!=32'hffffffff)
                sda_fall_count<=sda_fall_count+1'b1;
            if (raw_scl_low!=previous_raw_scl && raw_scl_transition_count!=32'hffffffff)
                raw_scl_transition_count<=raw_scl_transition_count+1'b1;
            if (raw_sda_low!=previous_raw_sda && raw_sda_transition_count!=32'hffffffff)
                raw_sda_transition_count<=raw_sda_transition_count+1'b1;
        end
    end
endmodule
