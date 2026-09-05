module dense_engine_synthesis_top (
    input logic clk,
    input logic reset,
    input logic start,

    input logic signed [7:0] input_data
        [0:2],

    output logic busy,
    output logic done,

    output logic signed [31:0] output_data
        [0:5]
);

    dense_engine #(
        .INPUT_NUMBER (3),
        .OUTPUT_NUMBER(6),
        .MAC_LANES    (4),

        .INPUT_WIDTH (8),
        .WEIGHT_WIDTH(8),
        .ACC_WIDTH   (32),

        .WEIGHT_FILE(
            "dense_engine_test_weights.mem"
        ),

        .BIAS_FILE(
            "dense_engine_test_biases.mem"
        )
    ) dense_engine_inst (
        .clk        (clk),
        .reset      (reset),
        .start      (start),
        .input_data (input_data),
        .busy       (busy),
        .done       (done),
        .output_data(output_data)
    );

endmodule