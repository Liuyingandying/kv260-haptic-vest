// ============================================================================
// matrix_buffer.v -- 16x16 触觉矩阵双 bank 帧缓冲 (256 x 8bit)
//
// 工程   : AX7020 fpga/16x16  第一阶段 Matrix Buffer
// 契约   : docs/MATRIX_BUFFER_INTERFACE_SPEC.md（v1.0）
//          语义来源为 STM32 HapticVest256 的 pending/active 双缓冲行为，
//          此处以 RTL 重新表达；未复制任何 STM32 代码。
//
// 数据契约（与 UART payload / HapticVest256 帧一致）:
//   - 256 个单元，每单元 1 字节，行优先 addr = row*16 + col
//   - 单元值 0~4（触觉强度级）；本模块对字节内容不感知，只管存取
//
// Bank 语义:
//   - act_bank = 0 : Bank A 为显示 bank（读端口来自 Bank A），写入 Bank B
//   - act_bank = 1 : Bank B 为显示 bank，写入 Bank A
//   - commit 上升沿: act_bank 单时钟翻转 => 帧原子切换
//     （PS 契约: 必须写满整帧 256 字节后才发 commit，硬件不校验）
//
// 时序:
//   - rd_data 为同步读，rd_addr 后 1 个 clk 有效（BRAM 常规时序）
//   - commit 内部边沿检测: 电平保持多拍只会切换一次，不会连翻
// ============================================================================

`timescale 1ns / 1ps

module matrix_buffer (
    input  wire        clk,
    input  wire        rst_n,       // 异步低有效复位: 仅复位 bank 选择，不清存储内容

    // ---- 写端口（PS 侧，写入非显示 bank 的下一帧）----
    input  wire        wr_en,
    input  wire [7:0]  wr_addr,     // 0..255, row*16+col
    input  wire [7:0]  wr_data,     // 0..4 级强度（本模块按不透明字节处理）

    // ---- 帧提交（上升沿有效，内部边沿检测）----
    input  wire        commit,

    // ---- 读端口（后续 Scheduler FSM 侧，读取显示 bank）----
    input  wire [7:0]  rd_addr,
    output reg  [7:0]  rd_data,     // 1 clk 延迟

    // ---- PEND 读端口（AXI 封装侧，读取写入 bank，用于 PS 写后回读校验）----
    input  wire [7:0]  pend_rd_addr,
    output reg  [7:0]  pend_rd_data, // 1 clk 延迟

    // ---- 状态输出 ----
    output wire        act_bank,    // 当前显示 bank: 0=Bank A, 1=Bank B
    output wire        commit_ack,   // 接受一次 commit 的 1 clk 脉冲（调试/中断源）
    input wire [7:0] audit_rd_addr,
    output reg [7:0] audit_rd_data
);

    // ------------------------------------------------------------------------
    // bank 选择与 commit 边沿检测
    // ------------------------------------------------------------------------
    reg  act_bank_r;
    reg  commit_d;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            act_bank_r <= 1'b0;      // 复位后 Bank A 为显示 bank（契约规定）
            commit_d   <= 1'b0;
        end else begin
            commit_d   <= commit;
            // 仅在 commit 的上升沿翻转，一次翻转即完成两 bank 交换 => 原子切换
            if (commit && !commit_d)
                act_bank_r <= ~act_bank_r;
        end
    end

    assign act_bank = act_bank_r;

    // commit 确认脉冲: 比翻转晚 1 拍输出，便于观测/作 IRQ
    reg commit_ack_r;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            commit_ack_r <= 1'b0;
        else
            commit_ack_r <= commit && !commit_d;
    end
    assign commit_ack = commit_ack_r;

    // ------------------------------------------------------------------------
    // 双 bank 存储: 2 x 256 x 8, 同步写/同步读（BRAM 可推断）
    // ------------------------------------------------------------------------
    reg [7:0] bank_a [0:255];
    reg [7:0] bank_b [0:255];

    // 写端口: 永远写入非显示 bank（~act_bank）
    //   act_bank=0 -> 写 Bank B ; act_bank=1 -> 写 Bank A
    always @(posedge clk) begin
        if (wr_en) begin
            if (!act_bank_r)
                bank_b[wr_addr] <= wr_data;
            else
                bank_a[wr_addr] <= wr_data;
        end
    end

    // 读端口: 永远来自显示 bank
    always @(posedge clk) begin
        if (!act_bank_r)
            rd_data <= bank_a[rd_addr];
        else
            rd_data <= bank_b[rd_addr];
    end

    // D1 independent read address, SAME actual active bank arrays. It cannot
    // alter the scheduler read port or return the submitted AXI packet cache.
    always @(posedge clk) begin
        if (!act_bank_r) audit_rd_data <= bank_a[audit_rd_addr];
        else audit_rd_data <= bank_b[audit_rd_addr];
    end

    // PEND 读端口: 永远来自写入 bank（显示 bank 的补集），供 PS 写后回读
    always @(posedge clk) begin
        if (act_bank_r)
            pend_rd_data <= bank_a[pend_rd_addr];
        else
            pend_rd_data <= bank_b[pend_rd_addr];
    end

endmodule
