module variant_triage_core #(
    parameter integer FEATURE_NUMBER = 16,
    parameter integer REQUANT_SHIFT  = 4,
    parameter integer HIDDEN_NUMBER = 4,
    parameter integer HIDDEN_MAC_LANES = 4,
    parameter string HIDDEN_WEIGHT_FILE =
        "dense_hidden_weights.mem",

    parameter string HIDDEN_BIAS_FILE =
        "dense_hidden_biases.mem",

    parameter string OUTPUT_WEIGHT_FILE =
        "dense_output_weights.mem",

    parameter string OUTPUT_BIAS_FILE =
        "dense_output_biases.mem",

    // Passed to both dense engines. 0 keeps the original 3-clocks-per-input schedule.
    parameter integer PIPELINED = 0
) (
    input logic clk,
    input logic reset,
    input logic start,

    input logic [7:0] feature
        [0:FEATURE_NUMBER-1],

    output logic busy,
    output logic done,

    output logic signed [31:0] score
);

    logic signed [31:0] hidden_score [0:HIDDEN_NUMBER-1];
    logic signed [7:0] quantized_score [0:HIDDEN_NUMBER-1];
    logic signed [31:0] output_score [0:0];

    logic signed [7:0] feature_signed [0:FEATURE_NUMBER-1];

    always_comb begin
        // for (int i = 0; i < 4; i++) begin
        //     output_neuron_input[i] =
        //         {normal_score[i]};
        // end
        for (int i = 0; i < FEATURE_NUMBER; i++) begin
            feature_signed[i] = {feature[i]};
        end
    end

    logic hidden_busy;
    logic hidden_done;

    logic quantizer_done;

    logic output_busy;
    logic output_done;


    dense_engine #(
        .INPUT_NUMBER (FEATURE_NUMBER),
        .OUTPUT_NUMBER(HIDDEN_NUMBER),
        .MAC_LANES    (HIDDEN_MAC_LANES),

        .INPUT_WIDTH (8),
        .WEIGHT_WIDTH(8),
        .ACC_WIDTH   (32),

        .WEIGHT_FILE(
            HIDDEN_WEIGHT_FILE
        ),

        .BIAS_FILE(
            HIDDEN_BIAS_FILE
        ),

        .PIPELINED(PIPELINED)
    ) hidden_dense_engine_inst (
        .clk        (clk),
        .reset      (reset),
        .start      (start),
        .input_data (feature_signed),
        .busy       (hidden_busy),
        .done       (hidden_done),
        .output_data(hidden_score)
    );


    ReLU_quantizer #(
        .DATA_NUMBER(HIDDEN_NUMBER),
        .QSHIFT     (REQUANT_SHIFT)
    ) ReLU_quantizer_inst (
        .clk   (clk),
        .reset (reset),
        .start (hidden_done),
        .data32(hidden_score),
        .data8 (quantized_score),
        .done  (quantizer_done)
    );


    dense_engine #(
        .INPUT_NUMBER (HIDDEN_NUMBER),
        .OUTPUT_NUMBER(1),
        .MAC_LANES    (1),

        .INPUT_WIDTH (8),
        .WEIGHT_WIDTH(8),
        .ACC_WIDTH   (32),

        .WEIGHT_FILE(
            OUTPUT_WEIGHT_FILE
        ),

        .BIAS_FILE(
            OUTPUT_BIAS_FILE
        ),

        .PIPELINED(PIPELINED)
    ) output_dense_engine_inst (
        .clk        (clk),
        .reset      (reset),
        .start      (quantizer_done),
        .input_data (quantized_score),
        .busy       (output_busy),
        .done       (output_done),
        .output_data(output_score)
    );


    assign busy =
        hidden_busy
        | output_busy;

    assign done = output_done;
    assign score = output_score[0];

endmodule