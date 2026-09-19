module score_clock_domain_crosser (
    input logic clk_100MHz,
    input logic reset,
    input logic [31:0] score,
    input logic score_done,
    input logic score_extracted,
    output logic [31:0] stable_score,
    output logic stable_score_stored
);


    logic [31:0] stable_score_reg, stable_score_next;
    logic stable_score_stored_reg, stable_score_stored_next;


    always_ff @(posedge clk_100MHz or posedge reset) begin
        if (reset) begin
            stable_score_reg <= 32'b0;
            stable_score_stored_reg <= 1'b0;
        end else begin
            stable_score_reg <= stable_score_next;
            stable_score_stored_reg <= stable_score_stored_next;
        end
    end

    always_comb begin
        stable_score_next = stable_score_reg;
        stable_score_stored_next = stable_score_stored_reg;

        if (score_done && !stable_score_stored_reg) begin
            stable_score_next = score;
            stable_score_stored_next = 1'b1;
        end

        if (score_extracted) begin
            stable_score_stored_next = 1'b0;
        end
    end


    assign stable_score = stable_score_reg;
    assign stable_score_stored = stable_score_stored_reg;

endmodule 