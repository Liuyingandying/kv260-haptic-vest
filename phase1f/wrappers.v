`timescale 1ns/1ps
module tb_delay_96968();
    tb_phase1f_delay #(.CLK_HZ(96_968_727), .I2C_FREQ_HZ(378_700)) u();
endmodule
module tb_delay_50m();
    tb_phase1f_delay #(.CLK_HZ(50_000_000), .I2C_FREQ_HZ(400_000)) u();
endmodule