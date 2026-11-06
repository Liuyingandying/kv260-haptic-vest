// ============================================================================
// scheduler_fsm.v -- 触觉刷新控制器（第一阶段: 扫描控制, 无 I2C）
//
// 工程   : AX7020 fpga/16x16 第三阶段
// 职责   : 按 PCA9685 硬件结构生成刷新序列:
//          block 0~15 外层 × channel 0~15 内层（严格禁止单纯按 matrix index 扫描）,
//          每通道走 "查映射 -> 取矩阵 -> 查 duty" 三级 ROM/Buffer 流水,
//          在 OUTPUT 拍给出稳定的通道刷新数据（供后续 I2C Master 消费）。
//
// 数据通路（各从模块均为同步读 1 clk 延迟, 本机按"+1 拍等待"状态吸收）:
//   map_addr={blk,ch} -> [matrix_map_rom] -> source_index
//                     -> [matrix_buffer]  -> level_data
//                     -> [duty_lut_rom]   -> {pwm_value, full_off}
//
// 状态机（每通道 7 拍）:
//   IDLE       等 frame_ready（未来接 AXI COMMIT 的换帧事件）
//   SCAN       本通道第 1 拍: map_addr 已在本拍前驱更新, ROM 本拍末采样
//   MAP_WAIT   等映射 ROM 输出; 拍末采样 source_index -> rd_addr
//   LOAD_MATRIX本拍向 Matrix Buffer 送出 rd_addr（拍末采样）
//   BUF_WAIT   等矩阵数据; 拍末采样 level_data -> duty_level
//   DUTY_WAIT  等 duty ROM 输出
//   OUTPUT     拍末锁存 current_* 并给 1 拍 out_valid
//   NEXT       推进 ch/blk; blk 15/ch 15 完成 -> frame_done 脉冲回 IDLE
//
// 扫描序保证: blk 为外层计数器（NEXT 中 ch 到 15 才进位）, 天然排除
// "按 matrix index 0~255 扫描"的错误顺序。
//
// ----------------------------------------------------------------------------
// v1.2 (2026-09-26) 增量: dirty block 选择刷新（v1.1 行为保持, 非重写）
//   新增输入:
//     dirty_valid          1 = dirty_bitmap 本帧有效
//     dirty_bitmap[15:0]   bit b = block b 本帧需要刷新
//     force_off            1 = 全关闭刷新（最高优先级, 覆盖 dirty）
//   掩码规则（帧起点 IDLE->启动时锁存, 帧内不变）:
//     mask = force_off ? FFFF : (dirty_valid ? dirty_bitmap : FFFF)
//   -> 未连接(z)/dirty_valid=0 时 mask=FFFF: 与 v1.1 全刷行为逐拍等价
//     -> dirty_bitmap=0 帧: 不产生任何通道扫描/I2C 数据, ~17 拍空转后 frame_done
//   实现:
//   - 新增 S_SKIP 态（state 扩为 4bit）: 掩码外块 1 拍跳过, 不跑 7 拍通道流水
//   - force_off 帧: BUF_WAIT 拍强制 duty_level=0 -> duty LUT 输出 (pwm=0,
//     full_off=1); 坐标/地址/I2C 组包通路不变, 等价"全 256 通道发全关"
//   - blk_go_i 流控保持不变: 块边界推进（含跳块推进）仍等下游排空,
//     帧尾 frame_end 仍不受门控; 跳块本身不产生突发, 不引入新流控点
// ============================================================================

`timescale 1ns / 1ps

module scheduler_fsm #(
    parameter [15:0] TARGET_MASK = 16'hffff
)(
    input  wire        clk,
    input  wire        rst_n,          // 低有效（与既有模块一致; 对应任务书 rst）

    input  wire        frame_ready,    // 换帧事件（未来接 COMMIT 换 bank; 电平采样）
    // ---- 来自 Matrix Buffer ----
    input  wire [7:0]  level_data,     // 当前 cell 强度 (0~4, 显示 bank)
    // ---- 来自 Map ROM ----
    input  wire [7:0]  source_index,
    input  wire [3:0]  block_id,
    input  wire [3:0]  channel_id,
    input  wire [6:0]  pca_addr,
    input  wire [1:0]  tca_ch,
    // ---- 来自 Duty LUT ----
    input  wire [11:0] pwm_value,
    input  wire        full_off,

    // ---- 送往 Matrix Map ROM ----
    output reg  [7:0]  map_addr,       // {blk, ch}
    // ---- 送往 Matrix Buffer ----
    output reg  [7:0]  rd_addr,        // = source_index
    // ---- 送往 Duty LUT ROM ----
    output reg  [2:0]  duty_level,     // = level_data

    // ---- 刷新序列输出（后续 I2C Master 消费; OUTPUT 拍后保持稳定）----
    output reg  [3:0]  current_block,
    output reg  [3:0]  current_channel,
    output reg  [7:0]  current_source_index,
    output reg  [11:0] current_pwm,
    output reg         current_full_off,
    output reg  [6:0]  current_pca_addr,   // 附加: I2C 组包需要
    output reg  [1:0]  current_tca_ch,     // 附加: TCA9548 选通需要
    output reg         out_valid,          // 1 拍: current_* 有效
    output reg         frame_done,         // 1 拍: 256 通道扫描完成
    output wire        busy_o,             // v1.1 扩展: 扫描进行中（非 IDLE）
    input  wire        blk_go_i,           // v1.1 扩展: 块推进流控（=1 允许进入下一块;
                                           //   下游忙时拉低, 扫描与 I2C 排空锁步,
                                           //   防 700x 速率失配丢块; 恒 1 = v1.0.1 语义）
    // ---- v1.2 扩展: dirty block 选择刷新 ----
    input  wire        dirty_valid,        // 1 = dirty_bitmap 有效; 0/未连 = 全刷(v1.1 语义)
    input  wire [15:0] dirty_bitmap,       // bit b = block b 本帧需刷新
    input  wire        force_off           // 1 = 全关闭刷新, 最高优先级（覆盖 dirty）
);

    // ------------------------------------------------------------------------
    // 状态编码（v1.2: 扩为 4bit, 新增 S_SKIP 跳块态）
    // ------------------------------------------------------------------------
    localparam S_IDLE        = 4'd0,
               S_SCAN        = 4'd1,
               S_MAP_WAIT    = 4'd2,
               S_LOAD_MATRIX = 4'd3,
               S_BUF_WAIT    = 4'd4,
               S_DUTY_WAIT   = 4'd5,
               S_OUTPUT      = 4'd6,
               S_NEXT        = 4'd7,
               S_SKIP        = 4'd8;      // v1.2: 跳过掩码外块（无扫描无 I2C）

    reg [3:0] state;
    reg [3:0] blk, ch;                   // 扫描计数器（blk 外层, ch 内层）

    // v1.2: 帧掩码/force_off 锁存（帧起点采样, 帧内保持 -> 扫描中途位图抖动不影响本帧）
    reg        force_off_q;
    reg [15:0] dirty_mask_q;

    // v1.2: 未驱动(z/x)输入按 v1.1 语义处理。
    // 注: 与 z 字面量的 case 相等比较仅为仿真期识别"端口未连接";
    //     综合时 1bit 信号 === 1'b1 即该信号本身, 硬件上 tie 0 = v1.1 行为
    wire force_off_eff = (force_off   === 1'b1);
    wire dirty_vld_eff = (dirty_valid === 1'b1);

    // v1.2: 本帧将采用的块掩码（组合版, 供帧起点锁存与块0入口判决）
    wire [15:0] mask_next = TARGET_MASK & (force_off_eff ? 16'hFFFF :
                            (dirty_vld_eff ? dirty_bitmap : 16'hFFFF));

    assign busy_o = (state != S_IDLE);

    // 下一个扫描坐标
    wire [3:0] nch = (ch == 4'd15) ? 4'd0 : ch + 4'd1;
    wire [3:0] nblk = (ch == 4'd15) ? ((blk == 4'd15) ? 4'd0 : blk + 4'd1) : blk;
    wire       frame_end = (ch == 4'd15) && (blk == 4'd15);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state              <= S_IDLE;
            blk                <= 4'd0;
            ch                 <= 4'd0;
            map_addr           <= 8'h00;
            rd_addr            <= 8'h00;
            duty_level         <= 3'd0;
            force_off_q        <= 1'b0;      // v1.2: 复位非强制关
            dirty_mask_q       <= 16'hFFFF;  // v1.2: 复位全刷（v1.1 安全语义）
            current_block      <= 4'd0;
            current_channel    <= 4'd0;
            current_source_index <= 8'h00;
            current_pwm        <= 12'h000;
            current_full_off   <= 1'b1;   // 复位态安全: 全断
            current_pca_addr   <= 7'h00;
            current_tca_ch     <= 2'd0;
            out_valid          <= 1'b0;
            frame_done         <= 1'b0;
        end else begin
            // 单拍脉冲默认值
            out_valid  <= 1'b0;
            frame_done <= 1'b0;

            case (state)
                // 等换帧事件; frame_ready 电平采样, 保持为 1 则连续刷帧
                S_IDLE: begin
                    if (frame_ready) begin
                        // v1.2: 帧起点锁存刷新掩码与 force_off（帧内不变）
                        force_off_q  <= force_off_eff;
                        dirty_mask_q <= mask_next;
                        blk      <= 4'd0;
                        ch       <= 4'd0;
                        map_addr <= {4'd0, 4'd0};      // SCAN 拍内 ROM 可见
                        // v1.2: 块 0 在掩码内才进通道流水, 否则直接进跳块态
                        state    <= mask_next[0] ? S_SCAN : S_SKIP;
                    end
                end

                // 本通道第 1 拍: map_addr 已稳定, ROM 拍末完成寄存
                S_SCAN: begin
                    state <= S_MAP_WAIT;
                end

                // 拍末采样映射 ROM: source_index -> Matrix Buffer 地址
                S_MAP_WAIT: begin
                    rd_addr <= source_index;
                    state   <= S_LOAD_MATRIX;
                end

                // 本拍 rd_addr 对 Buffer 可见（拍末寄存）
                S_LOAD_MATRIX: begin
                    state <= S_BUF_WAIT;
                end

                // 拍末采样矩阵数据: level -> Duty LUT
                S_BUF_WAIT: begin
                    // v1.2: force_off 帧 -> level 强制 0, duty LUT 输出
                    // (pwm=0, full_off=1); 坐标/地址通路不变 = 定向"全关"刷新
                    duty_level <= force_off_q ? 3'd0 : level_data[2:0];
                    state      <= S_DUTY_WAIT;
                end

                // 等 duty ROM 输出
                S_DUTY_WAIT: begin
                    state <= S_OUTPUT;
                end

                // 拍末锁存整条通路的刷新数据, 对外给出 1 拍 valid
                S_OUTPUT: begin
                    current_block        <= block_id;
                    current_channel      <= channel_id;
                    current_source_index <= source_index;
                    current_pwm          <= pwm_value;
                    current_full_off     <= full_off;
                    current_pca_addr     <= pca_addr;
                    current_tca_ch       <= tca_ch;
                    out_valid            <= 1'b1;
                    state                <= S_NEXT;
                end

                // 推进扫描坐标（blk 外层 / ch 内层）
                // v1.1 流控: 块推进需 blk_go_i（下游 I2C 排空后才扫下一块,
                // 与 STM32 服务循环的 I2C 节流一致; frame_end 不受门控）
                // v1.2: 块边界(ch=15)推进时按掩码判决 —— 掩码外块转入 S_SKIP
                //（仍受 blk_go_i 门控: 前一选中块的突发必须先排空, 流控语义不变）
                S_NEXT: begin
                    if (frame_end) begin
                        ch       <= 4'd0;
                        blk      <= 4'd0;
                        map_addr <= 8'h00;
                        frame_done <= 1'b1;
                        state      <= S_IDLE;      // 回 IDLE 等下一次 frame_ready
                    end else if (blk_go_i) begin
                        ch       <= nch;
                        blk      <= nblk;
                        map_addr <= {nblk, nch};
                        if ((ch == 4'd15) && !dirty_mask_q[nblk])
                            state <= S_SKIP;       // v1.2: 下一块未选 -> 跳过
                        else
                            state <= S_SCAN;
                    end
                    // blk_go_i=0: 原地等待（下游突发进行中）
                end

                // v1.2: 跳过掩码外块 —— 不跑通道流水, 不产生 out_valid/I2C 数据
                //（本块无突发, 无需等 blk_go_i; 走到 blk=15 即帧完成, 与
                //  v1.1 frame_end 路径同款收尾）
                S_SKIP: begin
                    if (blk == 4'd15) begin
                        ch         <= 4'd0;
                        blk        <= 4'd0;
                        map_addr   <= 8'h00;
                        frame_done <= 1'b1;
                        state      <= S_IDLE;
                    end else begin
                        blk <= blk + 4'd1;
                        if (dirty_mask_q[blk + 4'd1]) begin
                            ch       <= 4'd0;
                            map_addr <= {blk + 4'd1, 4'd0};
                            state    <= S_SCAN;    // 落到下一个选中块, 恢复流水
                        end
                        // 掩码为 0: 留在 S_SKIP 继续跳（dirty=0 帧 ~17 拍完成）
                    end
                end

                default: state <= S_IDLE;
            endcase
        end
    end

endmodule
