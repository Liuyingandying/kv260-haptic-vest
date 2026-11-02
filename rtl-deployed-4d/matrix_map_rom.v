// ============================================================================
// matrix_map_rom.v -- 16x16 矩阵 <-> PCA9685 物理位置映射 ROM（同步读, 1 clk 延迟）
//
// 工程   : AX7020 fpga/16x16 第二阶段（语义层 ROM）
// 来源   : STM32 HapticVest256 语义迁移（行为等价, 未复制代码）:
//   1) haptic_matrix.c:229-257  HapticVest256_SourceIndex(block, channel)
//      -> 输入 {block[3:0], channel[3:0]} 解包, 输出 source_index = row*16+col
//   2) haptic_matrix.c:22-27    HAPTIC_VEST_256_BLOCK_MAP[16]
//      -> block -> 乱序 PCA9685 7bit 地址 + TCA9548 通道
//
// 地址语义: matrix_addr = {block[3:0], channel[3:0]}（硬件扫描序, 与函数入参一致）
//   block  0~7   : 左半区  row = block*2 + ch/8,       col = ch%8
//   block  8~11  : 右上区  row = (block-8)*2 + ch/8,   col = 15 - ch%8   <-- 布线镜像修正, 不可省略
//   block 12~15  : 右下区  row = (block-8)*2 + ch/8,   col = 8 + ch%8
//   source_index = {row, col}（row/col 各 4bit 拼接 = row*16+col, 行优先）
//
// 时序: 全部输出相对 matrix_addr 有 1 clk 延迟（同步 ROM 读, BRAM 推断友好）
// ============================================================================

`timescale 1ns / 1ps

module matrix_map_rom (
    input  wire        clk,
    input  wire [7:0]  matrix_addr,     // {block[3:0], channel[3:0]}

    output reg  [7:0]  source_index,    // 矩阵 cell 索引 row*16+col（SourceIndex 返回值）
    output reg  [3:0]  block_id,        // block 译码回显
    output reg  [3:0]  channel_id,      // channel 译码回显
    output reg  [6:0]  pca_addr,        // 本块 PCA9685 7bit 从机地址（乱序表）
    output reg  [1:0]  tca_ch           // 本块所在 TCA9548 通道
);

    // ------------------------------------------------------------------------
    // 组合译码（行为等价 HapticVest256_SourceIndex, 保留镜像修正）
    // ------------------------------------------------------------------------
    wire [3:0] blk = matrix_addr[7:4];
    wire [3:0] ch  = matrix_addr[3:0];

    reg [4:0] row;                       // 5bit 防中间溢出, 结果 <= 15
    reg [3:0] col;

    always @* begin
        if (blk < 4'd8) begin
            row = {1'b0, blk} * 2 + {4'b0, ch[3]};          // block*2 + channel/8
            col = {1'b0, ch[2:0]};                          // channel%8
        end else begin
            row = {1'b0, (blk - 4'd8)} * 2 + {4'b0, ch[3]};
            if (blk <= 4'd11)
                col = 4'd15 - {1'b0, ch[2:0]};              // 右上区: 列镜像（布线修正）
            else
                col = 4'd8 + {1'b0, ch[2:0]};               // 右下区
        end
    end

    // block -> 乱序 PCA9685 7bit 地址（HAPTIC_VEST_256_BLOCK_MAP, 禁止顺序遍历）
    reg [6:0] pca_comb;
    always @* begin
        case (blk)
            4'd0:    pca_comb = 7'h4D;
            4'd1:    pca_comb = 7'h4E;
            4'd2:    pca_comb = 7'h4F;
            4'd3:    pca_comb = 7'h44;
            4'd4:    pca_comb = 7'h49;
            4'd5:    pca_comb = 7'h4A;
            4'd6:    pca_comb = 7'h4B;
            4'd7:    pca_comb = 7'h4C;
            4'd8:    pca_comb = 7'h40;
            4'd9:    pca_comb = 7'h41;
            4'd10:   pca_comb = 7'h42;
            4'd11:   pca_comb = 7'h43;
            4'd12:   pca_comb = 7'h45;
            4'd13:   pca_comb = 7'h46;
            4'd14:   pca_comb = 7'h47;
            default: pca_comb = 7'h48;                      // 4'd15
        endcase
    end

    // TCA9548 通道: 块 0~3->ch0, 4~7->ch1, 8~11->ch2, 12~15->ch3
    wire [1:0] tca_comb = blk[3:2];

    // ------------------------------------------------------------------------
    // 同步读寄存（1 clk 延迟, 全输出对齐）
    // ------------------------------------------------------------------------
    always @(posedge clk) begin
        source_index <= {row[3:0], col};
        block_id     <= blk;
        channel_id   <= ch;
        pca_addr     <= pca_comb;
        tca_ch       <= tca_comb;
    end

endmodule
