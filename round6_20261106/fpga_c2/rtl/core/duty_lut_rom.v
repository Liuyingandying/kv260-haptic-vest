// ============================================================================
// duty_lut_rom.v -- 5 级触觉强度 -> PCA9685 12bit duty 查表 ROM（同步读, 1 clk 延迟）
//
// 工程   : AX7020 fpga/16x16 第二阶段（语义层 ROM）
// 来源   : STM32 HapticVest256 语义迁移（行为等价, 未复制代码）:
//   haptic_matrix.c:16-20  HAPTIC_GRAY_TO_PWM_5_LEVEL[5] = {0, 1632, 2448, 3264, 4080}
//   pca9685.c:238-251      level 0 走 FULL_OFF 位（LEDn_OFF[4]=0x10）而非 duty=0
//   main.c:836             level >4 钳位为 4（PL 侧重做一次, 双保险）
//
// 输出语义:
//   full_off = 1 : 本通道必须写 PCA9685 FULL_OFF 位（全断, 非 0 占空比）
//   full_off = 0 : pwm_value 为 12bit 比较值（LEDn_OFF 寄存器低 12bit）
// ============================================================================

`timescale 1ns / 1ps

module duty_lut_rom (
    input  wire        clk,
    input  wire [2:0]  level,         // 0~4 有效; 5~7 按钳位规则视为 4
    output reg  [11:0] pwm_value,     // 12bit duty（4096 满量程, 安全上限 4080）
    output reg         full_off       // 1 = 输出 FULL_OFF 位
);

    reg [11:0] pwm_comb;
    reg        off_comb;

    always @* begin
        case (level)
            3'd0:    begin pwm_comb = 12'h000; off_comb = 1'b1; end  // 全断（FULL_OFF 位）
            3'd1:    begin pwm_comb = 12'h660; off_comb = 1'b0; end  // 1632 (40%)
            3'd2:    begin pwm_comb = 12'h990; off_comb = 1'b0; end  // 2448 (60%)
            3'd3:    begin pwm_comb = 12'hCC0; off_comb = 1'b0; end  // 3264 (80%)
            default: begin pwm_comb = 12'hFF0; off_comb = 1'b0; end  // 4 及以上: 4080 (100%)
        endcase
    end

    always @(posedge clk) begin
        pwm_value <= pwm_comb;
        full_off  <= off_comb;
    end

endmodule
