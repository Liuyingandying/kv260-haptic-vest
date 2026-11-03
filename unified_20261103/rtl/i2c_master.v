// ============================================================================
// i2c_master.v -- I2C 主机位引擎（开漏两线, START/STOP/8bit 发送/ACK 检测）
//
// 工程   : AX7020 fpga/16x16 第八阶段（I2C Master 第一阶段）
// 时钟   : 可配置。默认 CLK 50MHz, I2C 400kHz:
//          TICKS = CLK/(4*I2C) = 31 拍/相位 -> SCL 周期 4*31=124 拍 = 2.48us
//          实际频率 50MHz/124 = 403.2kHz（Fast Mode 容差内）
//
// 总线约定（开漏+上拉, "1 = 拉低"）:
//   scl_drv/sda_drv = 1 -> 本端拉低该线; = 0 -> 释放（上拉为高）
//   TB 侧: assign SCL = scl_drv ? 1'b0 : 1'b1;  SDA 同理并线或从机低驱动
//
// 命令协议:
//   cmd_start (IDLE 时采样, 1 拍): 等总线空闲后发 START 并清错误标志
//   byte_ready/byte_valid/byte_data/byte_stop: 字节握手;
//     byte_stop 与末字节同拍 = 该字节 ACK 后发 STOP -> done 脉冲
//   未给字节时主机保持 SCL 低（自拉伸）, TIMEOUT_US 后错误终结
//   ack_error: 任一从机 NACK 置位并保持, 下次 cmd_start 清除
//   SCL 高电平相位检查 scl_i（支持从机时钟拉伸）
//   done 也是 timeout/abort 的终结脉冲; 成功必须 done 且 !ack_error。
//   ABORT_ON_NACK=1 时首个 NACK 后尽早发合法 STOP, 默认 0 保持旧字节流。
// ============================================================================

`timescale 1ns / 1ps

module i2c_master #(
    parameter integer CLK_FREQ_HZ = 50_000_000,
    parameter integer I2C_FREQ_HZ = 400_000,
    // Phase 4A: max time for SCL to actually reach VIH after release.
    // Exceeding it is reported as a dedicated error, not a generic transaction timeout.
    parameter integer SCL_RISE_TIMEOUT_US = 50,
    parameter integer TIMEOUT_US = 5000,
    parameter integer ABORT_ON_NACK = 0
)(
    input  wire       clk,
    input  wire       rst_n,
    // ---- 命令接口 ----
    input  wire       cmd_start,     // 1 拍: 空闲时发起 START
    input  wire       byte_valid,
    input  wire [7:0] byte_data,
    input  wire       byte_stop,     // 与末字节同拍: ACK 后发 STOP
    input  wire       abort_req,     // 1 = 终止当前事务; 未连接保持旧接口行为
    output reg        byte_ready,    // 主机请求下一字节
    output reg        ack_error,     // NACK 粘滞标志
    output reg        timeout_error, // 超时粘滞; 新事务清除, abort 不置位
    output reg        done,          // 1 拍: 成功 STOP 或错误终结
    output wire       busy,
    // ---- 开漏总线 ----
    output reg        scl_drv,       // 1 = 拉低 SCL
    output reg        sda_drv,       // 1 = 拉低 SDA
    input  wire       scl_i,
    input  wire       sda_i
);

    // Phase 4A: TICKS is 60 @400k, 242 @100k, 484 @50k, 2424 @10k.
    // The original 8-bit divider could not represent >255, which silently
    // froze the FSM below ~95 kHz. Width is now derived from TICKS.
    localparam integer TICKS = (CLK_FREQ_HZ / (4 * I2C_FREQ_HZ) < 1)
                             ? 1 : (CLK_FREQ_HZ / (4 * I2C_FREQ_HZ));
    localparam integer DIV_W = (TICKS > 65536) ? 24 : (TICKS > 256 ? 16 : 8);

    localparam M_IDLE  = 3'd0,
               M_START = 3'd1,
               M_PRE   = 3'd2,
               M_SEND  = 3'd3,
               M_ACK   = 3'd4,
                M_STOP  = 3'd5,
                M_WAITFREE = 3'd6;

    // 整笔事务有界, 包括等待总线空闲、时钟拉伸和上游缺字节。
    // 64bit 常量运算避免 CLK_FREQ_HZ * TIMEOUT_US 的整数溢出。
    localparam [63:0] TIMEOUT_RAW =
        (64'd1 * CLK_FREQ_HZ * TIMEOUT_US + 64'd999_999) / 64'd1_000_000;
    localparam [63:0] TIMEOUT_CYCLES = (TIMEOUT_RAW < 1) ? 64'd1 : TIMEOUT_RAW;
    localparam integer TIMEOUT_W = (TIMEOUT_CYCLES > 1) ? $clog2(TIMEOUT_CYCLES) : 1;
    reg [TIMEOUT_W-1:0] timeout_count;

    // ---- Phase 4A: dedicated SCL rise / stuck-low timeout ----
    localparam [63:0] RISE_TIMEOUT_RAW =
        (64'd1 * CLK_FREQ_HZ * SCL_RISE_TIMEOUT_US + 64'd999_999) / 64'd1_000_000;
    localparam [63:0] RISE_TIMEOUT_CYCLES = (RISE_TIMEOUT_RAW < 1) ? 64'd1 : RISE_TIMEOUT_RAW;
    localparam integer RISE_W = (RISE_TIMEOUT_CYCLES > 1) ? $clog2(RISE_TIMEOUT_CYCLES) : 1;
    reg [RISE_W-1:0] rise_count;
    reg              scl_rise_timeout;

    reg [2:0] mst;
    reg [DIV_W-1:0] div;                 // Phase 4A: width derived from TICKS
    reg [1:0] phase;                     // SCL 四相位: 0/1 低, 2/3 高
    reg [3:0] bitcnt;                    // 当前字节剩余位（含符号位后）
    reg [7:0] shreg;
    reg       stop_pend;
    reg       sda_hold;                  // SDA 延迟装载: 与 SCL 下降沿错开 1 拍
    reg       sda_load;                  // （避免总线同拍双变化, 保护带）

    assign busy = (mst != M_IDLE);
    wire   tick = (mst != M_IDLE) && (div == TICKS[DIV_W-1:0] - 1'b1);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            mst        <= M_IDLE;
            div        <= {DIV_W{1'b0}};
            phase      <= 2'd0;
            bitcnt     <= 4'd0;
            shreg      <= 8'h00;
            stop_pend  <= 1'b0;
            sda_hold   <= 1'b0;
            sda_load   <= 1'b0;
            scl_drv    <= 1'b0;          // 复位释放总线
            sda_drv    <= 1'b0;
            byte_ready <= 1'b0;
            ack_error  <= 1'b0;
            timeout_error <= 1'b0;
            timeout_count <= {TIMEOUT_W{1'b0}};
            rise_count       <= {RISE_W{1'b0}};
            scl_rise_timeout <= 1'b0;
            done       <= 1'b0;
        end else begin
            done <= 1'b0;

            if ((mst != M_IDLE) &&
                ((abort_req === 1'b1) || (timeout_count >= TIMEOUT_CYCLES - 1) ||
                 (scl_rise_timeout === 1'b1))) begin
                // 这是错误终结, 不是成功 STOP。外部拉低不能被本端释放解除。
                mst           <= M_IDLE;
                div           <= {DIV_W{1'b0}};
                phase         <= 2'd0;
                stop_pend     <= 1'b0;
                sda_load      <= 1'b0;
                sda_hold      <= 1'b0;
                scl_drv       <= 1'b0;
                sda_drv       <= 1'b0;
                byte_ready    <= 1'b0;
                ack_error     <= 1'b1;
                timeout_error <= timeout_error || !(abort_req === 1'b1);
                timeout_count <= {TIMEOUT_W{1'b0}};
                rise_count       <= {RISE_W{1'b0}};
                scl_rise_timeout <= 1'b0;
                done          <= 1'b1;
            end else if (mst == M_IDLE) begin
                scl_drv    <= 1'b0;      // 释放总线（上拉为高）
                sda_drv    <= 1'b0;
                byte_ready <= 1'b0;
                div        <= {DIV_W{1'b0}};
                phase      <= 2'd0;
                timeout_count <= {TIMEOUT_W{1'b0}};
                // 接受命令后才产生终结事件。即使 abort 已置位, 下一活动拍
                // 也会错误终结, 避免上游已经发 cmd 却永久等待 ready/done。
                rise_count       <= {RISE_W{1'b0}};
                scl_rise_timeout <= 1'b0;
                if (cmd_start) begin
                    ack_error <= 1'b0;   // 新事务清 NACK 标志
                    timeout_error <= 1'b0;
                    stop_pend <= 1'b0;
                    sda_load  <= 1'b0;
                    mst       <= M_WAITFREE;
                end
            end else begin
                timeout_count <= timeout_count + 1'b1;

                // ---- Phase 4A: SCL rise / stuck-low watchdog ----
                // SCL released (scl_drv=0) but the pad still reads low: either the
                // pull-up cannot charge the bus in time, or a slave is stretching.
                // Report a dedicated error instead of hanging the FSM.
                if ((scl_drv == 1'b0) && (scl_i == 1'b0) && (mst != M_IDLE)) begin
                    if (rise_count >= RISE_TIMEOUT_CYCLES[RISE_W-1:0]) begin
                        scl_rise_timeout <= 1'b1;
                    end else begin
                        rise_count <= rise_count + 1'b1;
                    end
                end else begin
                    rise_count <= {RISE_W{1'b0}};
                end
                // SDA 延迟装载: 在 SCL 下降沿后的第 1 拍切换数据（保护带,
                // 避免 SCL/SDA 同拍变化造成总线监视歧义）
                if (sda_load && div == {DIV_W{1'b0}}) begin
                    sda_drv  <= sda_hold;
                    sda_load <= 1'b0;
                end

                if (tick) begin
                    div  <= {DIV_W{1'b0}};
                    phase <= phase + 2'd1;       // 默认推进（状态内可覆盖）
                end else begin
                    div <= div + 1'b1;
                end

                case (mst)
                    // START 前两线必须持续为高; 任一低电平重启空闲保护窗。
                    M_WAITFREE: begin
                        if (scl_i && sda_i) begin
                            if (tick && phase == 2'd2) begin
                                sda_drv <= 1'b1;
                                phase   <= 2'd0;
                                mst     <= M_START;
                            end
                        end else begin
                            div   <= {DIV_W{1'b0}};
                            phase <= 2'd0;
                        end
                    end

                    // START: SDA 低保持 2 相位（tHD;STA）, 随后 SCL 拉低
                    M_START: begin
                        if (tick && phase == 2'd1) begin
                            if (scl_i) begin
                                scl_drv    <= 1'b1;
                                phase      <= 2'd0;
                                byte_ready <= 1'b1;  // 请求地址字节（SCL 低期间）
                                mst        <= M_PRE;
                            end else begin
                                phase <= 2'd1;
                            end
                        end
                    end

                    // SCL 低, 等待字节握手; 接受后立即给出 MSB
                    M_PRE: begin
                        if (byte_valid && byte_ready) begin
                            shreg      <= byte_data;
                            stop_pend  <= byte_stop;
                            byte_ready <= 1'b0;
                            sda_drv    <= ~byte_data[7];  // 位1=释放(高), 位0=拉低
                            bitcnt     <= 4'd7;
                            phase      <= 2'd0;
                            div        <= {DIV_W{1'b0}};
                            mst        <= M_SEND;
                        end
                    end

                    // 8bit 移位发送: PH0 数据建立(SCL低), PH1 末 SCL 升,
                    // PH2 高电平(从机采样, 检查拉伸), PH3 末 SCL 降+移位
                    M_SEND: begin
                        if (tick) begin
                            case (phase)
                                2'd1: scl_drv <= 1'b0;               // SCL 上升
                                2'd2: begin                          // SCL 高窗口
                                          if (scl_i) phase <= 2'd3;
                                          else phase <= 2'd2;
                                      end
                                2'd3: begin
                                          scl_drv <= 1'b1;           // SCL 下降
                                          if (bitcnt == 4'd0) begin
                                              sda_hold <= 1'b0;      // 延迟 1 拍释放 SDA 给 ACK
                                              sda_load <= 1'b1;
                                              mst      <= M_ACK;
                                          end else begin
                                              bitcnt   <= bitcnt - 4'd1;
                                              sda_hold <= ~shreg[6]; // 延迟 1 拍换下一位(取反=拉低语义)
                                              sda_load <= 1'b1;
                                              shreg    <= {shreg[6:0], 1'b0};
                                          end
                                          phase <= 2'd0;
                                      end
                                default: ;                           // PH0: 数据建立
                            endcase
                        end
                    end

                    // 第 9 拍: 释放 SDA, PH2 采样从机 ACK
                    M_ACK: begin
                        if (tick) begin
                            case (phase)
                                2'd1: scl_drv <= 1'b0;
                                2'd2: begin
                                          // Phase 4A: only wait for SCL to actually reach
                                          // VIH; the ACK is sampled one full TICKS window
                                          // later, in phase 3, so SDA has settled.
                                          if (scl_i) phase <= 2'd3;
                                          else phase <= 2'd2;
                                      end
                                2'd3: begin
                                          // Sample SDA at the end of the high period:
                                          // both SCL and SDA have had TICKS to settle.
                                          if (sda_i) ack_error <= 1'b1;  // NACK
                                          scl_drv <= 1'b1;
                                          phase   <= 2'd0;
                                          if (stop_pend || ((ABORT_ON_NACK != 0) && ack_error)) begin
                                              sda_hold <= 1'b1;      // 延迟 1 拍拉低 SDA（SCL 低期间）, 备 STOP
                                              sda_load <= 1'b1;
                                              mst      <= M_STOP;
                                          end else begin
                                              byte_ready <= 1'b1;
                                              mst        <= M_PRE;
                                          end
                                      end
                                default: ;
                            endcase
                        end
                    end

                    // STOP: 先补满 ACK 拍的完整低相位, 再升 SCL、升 SDA
                    // （否则 ACK 时钟只有 1 拍低, 周期 3 拍违反 tLOW）
                    M_STOP: begin
                        case (phase)
                            2'd0: if (tick) phase <= 2'd1;                             // 低保持
                            2'd1: if (tick) begin scl_drv <= 1'b0; phase <= 2'd2; end  // SCL 升
                            2'd2: if (tick) begin
                                      if (scl_i) begin
                                          sda_drv <= 1'b0; // 实际 SCL 高时释放 SDA = STOP
                                          phase   <= 2'd3;
                                      end else phase <= 2'd2;
                                  end
                            2'd3: if (tick) begin
                                      if (scl_i && sda_i) begin
                                          done <= 1'b1;
                                          mst  <= M_IDLE;
                                      end else phase <= 2'd3;
                                  end
                            default: phase <= 2'd0;
                        endcase
                    end

                    default: mst <= M_IDLE;
                endcase
            end
        end
    end

endmodule
