// ============================================================================
// dirty_detector.v -- 帧间变化检测（脏块位图）v1.2
//
// 工程   : AX7020 fpga/16x16  Dirty Detector 阶段
// 职责   : 对比上一帧(old)与当前帧(new)各 256 cell, 输出 16 个 PCA9685 块的
//          脏标记位图, 供上层决定哪些块需要 I2C 刷新（干净块零总线开销）。
//
// 块划分复用（硬性要求）:
//   内部实例化冻结模块 matrix_map_rom —— HapticVest256_SourceIndex 的 RTL
//   等价实现（含 block 8~11 列镜像修正）。本模块零译码逻辑复制:
//     source_index -> old/new 帧读地址;  block_id -> dirty_bitmap 位索引
//
// 扫描序: matrix_addr = {blk[3:0], ch[3:0]} 从 0x00 递增到 0xFF,
//   与 scheduler_fsm 的 blk 外层/ch 内层扫描序同构, 每拍比对 1 cell。
//
// 端口契约（old/new 帧读口 = matrix_buffer rd 端口时序, 同步读 1 clk 延迟）:
//   old_frame : 上一帧（显示过的帧）
//   new_frame : 当前帧（待刷新的帧）
//   语义上可直连 matrix_buffer 的 rd(显示bank) / pend_rd(写入bank) 两个读口,
//   或任意满足"addr 拍末寄存、data 次拍有效"的 256x8 存储体。
//
// 时序（流水线, 每 cell 4 拍首延迟后每拍吞吐 1 cell）:
//   拍 N   : map_addr = {blk,ch} 对 map ROM 可见
//   拍 N+1 : ROM 出 source_index/block_id -> 送 old/new 读地址
//   拍 N+2 : 读地址对存储体可见
//   拍 N+3 : old/new data 有效 -> 比对 -> dirty_acc[block_id] 累积
//   末 cell 比对后 1 拍锁存结果（避开"最后一拍同边写入丢失"竞争）
//   start->dirty_valid 全程 261 拍; 50MHz 下 5.22us, 相对 25ms 帧周期可忽略
//
// 故障安全: 复位值 dirty_bitmap = 16'hFFFF（全脏）—— 上层若用位图门控块刷新,
//   复位后/首次扫描前的行为退化为 v1.1 的全量刷新, 不可能漏刷。
// ============================================================================

`timescale 1ns / 1ps

module dirty_detector (
    input  wire        clk,
    input  wire        rst_n,          // 异步低有效（与既有模块一致）

    input  wire        start,          // 1 拍脉冲: 启动一次扫描（仅 IDLE 态采样, 忙时忽略）

    // ---- 上一帧读口（同步读, 1 clk 延迟; 时序同 matrix_buffer.rd）----
    output reg  [7:0]  old_frame_addr,
    input  wire [7:0]  old_frame_data,
    // ---- 当前帧读口（同步读, 1 clk 延迟）----
    output reg  [7:0]  new_frame_addr,
    input  wire [7:0]  new_frame_data,

    // ---- 结果（锁存保持, 直到下次扫描覆盖）----
    output reg         dirty_valid,    // 1 拍: dirty_bitmap/dirty_count 本次扫描有效
    output reg  [15:0] dirty_bitmap,   // bit b = 块 b 任一 cell 变化需刷新
    output reg  [4:0]  dirty_count,    // 置位块数 0..16
    output wire        busy            // 扫描进行中（busy 下降与 dirty_valid 同拍）
);

    // ------------------------------------------------------------------------
    // 状态机: IDLE -> SCAN(256 拍发地址) -> TAIL(3 拍排空流水) -> LATCH(1 拍)
    // ------------------------------------------------------------------------
    localparam S_IDLE  = 2'd0,
               S_SCAN  = 2'd1,
               S_TAIL  = 2'd2,
               S_LATCH = 2'd3;

    reg [1:0] state;
    reg [7:0] map_addr;                  // 扫描计数器 = {blk, ch}, 直连 map ROM

    // 地址/块号流水跟踪（与数据通路逐级对齐）
    //   a_d3 == 本次比对 cell 的 {blk,ch}; b_d2 == 其块号
    reg [7:0] a_d1, a_d2, a_d3;
    reg [3:0] b_d1, b_d2;
    reg [2:0] pipe_cnt;                  // 流水预热计数: ==3 起比对有效

    reg [15:0] dirty_acc;                // 本次扫描累积位图

    assign busy = (state != S_IDLE);

    // ------------------------------------------------------------------------
    // 块划分严格复用: 实例化 matrix_map_rom（HapticVest256_SourceIndex）
    // ------------------------------------------------------------------------
    wire [7:0] rom_source_index;
    wire [3:0] rom_block_id;

    matrix_map_rom u_map_rom (
        .clk          (clk),
        .matrix_addr  (map_addr),
        .source_index (rom_source_index),
        .block_id     (rom_block_id),
        .channel_id   (),               // 本模块不需要
        .pca_addr     (),
        .tca_ch       ()
    );

    // ------------------------------------------------------------------------
    // 比对与累加
    // ------------------------------------------------------------------------
    wire       cmp_en = (pipe_cnt == 3'd3) && (state == S_SCAN || state == S_TAIL);
    wire       diff   = (old_frame_data != new_frame_data);
    wire       last_cmp = cmp_en && (a_d3 == 8'hFF);

    // 16bit 置位计数
    function [4:0] popcount16;
        input [15:0] v;
        integer i;
        begin
            popcount16 = 5'd0;
            for (i = 0; i < 16; i = i + 1)
                popcount16 = popcount16 + {4'd0, v[i]};
        end
    endfunction

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state          <= S_IDLE;
            map_addr       <= 8'h00;
            a_d1           <= 8'h00;
            a_d2           <= 8'h00;
            a_d3           <= 8'h00;
            b_d1           <= 4'd0;
            b_d2           <= 4'd0;
            pipe_cnt       <= 3'd0;
            dirty_acc      <= 16'h0000;
            dirty_valid    <= 1'b0;
            dirty_bitmap   <= 16'hFFFF;  // 故障安全: 复位后全刷（= v1.1 行为）
            dirty_count    <= 5'd16;
            old_frame_addr <= 8'h00;
            new_frame_addr <= 8'h00;
        end else begin
            dirty_valid <= 1'b0;         // 单拍脉冲默认值

            case (state)
                // 等启动; 忙时 start 被忽略（不采样）
                S_IDLE: begin
                    if (start) begin
                        map_addr  <= 8'h00;
                        dirty_acc <= 16'h0000;
                        a_d1      <= 8'h00;
                        a_d2      <= 8'h00;
                        a_d3      <= 8'h00;
                        b_d1      <= 4'd0;
                        b_d2      <= 4'd0;
                        pipe_cnt  <= 3'd0;
                        state     <= S_SCAN;
                    end
                end

                // 发扫描地址: 本拍 map_addr={blk,ch} 对 ROM 可见
                S_SCAN: begin
                    a_d1 <= map_addr;
                    a_d2 <= a_d1;
                    a_d3 <= a_d2;
                    b_d1 <= rom_block_id;
                    b_d2 <= b_d1;
                    if (pipe_cnt != 3'd3)
                        pipe_cnt <= pipe_cnt + 3'd1;
                    if (map_addr == 8'hFF)
                        state <= S_TAIL;         // 末地址已发出
                    else
                        map_addr <= map_addr + 8'd1;
                end

                // 排空流水: map_addr 保持 FF, 移位链继续推进
                S_TAIL: begin
                    a_d1 <= map_addr;
                    a_d2 <= a_d1;
                    a_d3 <= a_d2;
                    b_d1 <= rom_block_id;
                    b_d2 <= b_d1;
                    if (last_cmp)
                        state <= S_LATCH;        // 末 cell 已比对, 下拍锁存
                end

                // 锁存结果: 晚 1 拍取 dirty_acc, 避开末 cell 同边写入丢失
                S_LATCH: begin
                    dirty_bitmap <= dirty_acc;
                    dirty_count  <= popcount16(dirty_acc);
                    dirty_valid  <= 1'b1;
                    state        <= S_IDLE;
                end

                default: state <= S_IDLE;
            endcase

            // 比对累积（SCAN 末段与 TAIL 都处于 cmp_en 窗口）
            if (cmp_en)
                dirty_acc[b_d2] <= dirty_acc[b_d2] | diff;

            // 读地址随流水推进（S_IDLE 不驱动, 保持末值）
            if (state == S_SCAN || state == S_TAIL) begin
                old_frame_addr <= rom_source_index;
                new_frame_addr <= rom_source_index;
            end
        end
    end

endmodule
