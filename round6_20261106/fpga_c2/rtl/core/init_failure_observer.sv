// Read-only INIT transaction observer. No outputs feed command/ACK/permit paths.
// Owner fields are latched on sb_start: the sequencer advances kind/global/blk
// before its registered sb_done reaches this module, so live fields at DONE
// must never be used to attribute an error. This observes transactions, not
// address/data byte ACK phases; there is deliberately no byte-index claim.
`timescale 1ns/1ps
module init_failure_observer (
    input wire clk, input wire rst_n,
    input wire init_restart_i, input wire init_busy_i,
    input wire sb_start_i, input wire sb_done_i, input wire sb_ack_error_i,
    input wire [2:0] sb_kind_i, input wire [3:0] sb_block_i,
    input wire sb_global_i, input wire timeout_error_i,
    // bit0 valid, bits3:1 kind, bits7:4 block, bit8 global, bit9 timeout,
    // bits17:10 observed START ordinal (not the sequencer's skipped txn index).
    output reg [31:0] first_failure_info_o,
    output reg [15:0] init_tca_failure_mask_o,
    output reg [15:0] init_pca_failure_mask_o
);
    reg owner_valid;
    reg [2:0] owner_kind;
    reg [3:0] owner_block;
    reg owner_global;
    reg [7:0] owner_ordinal, next_ordinal;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            first_failure_info_o<=0; init_tca_failure_mask_o<=0;
            init_pca_failure_mask_o<=0; owner_valid<=0; owner_kind<=0;
            owner_block<=0; owner_global<=0; owner_ordinal<=0; next_ordinal<=0;
        end else if (init_restart_i) begin
            first_failure_info_o<=0; init_tca_failure_mask_o<=0;
            init_pca_failure_mask_o<=0; owner_valid<=0; owner_kind<=0;
            owner_block<=0; owner_global<=0; owner_ordinal<=0; next_ordinal<=0;
        end else begin
            // Complete old ownership before accepting any same-cycle START.
            if (sb_done_i && owner_valid) begin
                owner_valid<=0;
                if (sb_ack_error_i) begin
                    if (!first_failure_info_o[0])
                        first_failure_info_o<={14'b0,owner_ordinal,
                            timeout_error_i,owner_global,owner_block,owner_kind,1'b1};
                    // The global mask=0 transaction owns no individual block.
                    // Its failure is represented by first record global=1.
                    if (!owner_global && owner_kind==3'd0)
                        init_tca_failure_mask_o[owner_block]<=1'b1;
                    if (!owner_global && owner_kind==3'd1)
                        init_pca_failure_mask_o[owner_block]<=1'b1;
                end
            end
            if (sb_start_i) begin
                owner_valid<=init_busy_i;
                if (init_busy_i) begin
                    owner_kind<=sb_kind_i;owner_block<=sb_block_i;
                    owner_global<=sb_global_i;owner_ordinal<=next_ordinal;
                    if (next_ordinal!=8'hff) next_ordinal<=next_ordinal+1'b1;
                end
            end
        end
    end
endmodule
