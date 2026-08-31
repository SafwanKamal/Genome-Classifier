module variant_triage_top #(
    parameter integer CLOCK_HZ      = 100_000_000,
    parameter integer BAUD_RATE     = 115_200,
    parameter integer FEATURE_NUMBER = 16
) (
    input  logic clk,
    input  logic reset,
    input  logic rx,
    output logic tx
);

    `include "model_parameters.svh"

    logic [7:0] packet [0:FEATURE_NUMBER - 1];
    logic       packet_valid;

//    logic signed [31:0] neuron_score;
    logic signed [31:0] layer_score [0:3];
    logic signed [7:0]  normal_score [0:3];
    logic        [7:0]  output_neuron_input [0:3];
    logic signed [31:0] output_neuron_score;
    logic signed [31:0] response [0:0];
    logic               neuron_busy;
    logic               neuron_done;
    logic               layer_busy, layer_done;
    logic               normal_done;
    logic               output_neuron_busy, output_neuron_done;

    logic response_busy;

    assign response[0] = output_neuron_score;

    always_comb begin
        for (int i = 0; i < 4; i++) begin
            output_neuron_input[i] = {normal_score[i]};
        end
    end

    UART_packet_RX #(
        .CLOCK_HZ          (CLOCK_HZ),
        .BAUD_RATE         (BAUD_RATE),
        .PACKET_BYTE_NUMBER(FEATURE_NUMBER)
    ) UART_packet_RX_inst (
        .clk         (clk),
        .reset       (reset),
        .rx          (rx),
        .packet      (packet),
        .packet_valid(packet_valid)
    );


//    dense_neuron #(
//        .FEATURE_NUMBER(FEATURE_NUMBER),
//        .BIAS          (32'sd25)
//    ) dense_neuron_inst (
//        .clk    (clk),
//        .reset  (reset),
//        .start  (packet_valid),
//        .feature(packet),
//        .busy   (neuron_busy),
//        .done   (neuron_done),
//        .score  (neuron_score)
//    );
    
    // dense_layer #(
    //     .FEATURE_NUMBER(FEATURE_NUMBER)
    // ) dense_layer_inst (
    //     .clk    (clk),
    //     .reset  (reset),
    //     .start  (packet_valid),
    //     .feature(packet),
    //     .busy   (layer_busy),
    //     .done   (layer_done),
    //     .layer_output  (layer_score)
    // );
    dense_layer #(
        .FEATURE_NUMBER(FEATURE_NUMBER),
        .BIAS_0        (MODEL_BIAS_0),
        .BIAS_1        (MODEL_BIAS_1),
        .BIAS_2        (MODEL_BIAS_2),
        .BIAS_3        (MODEL_BIAS_3)
    ) dense_layer_inst (
        .clk         (clk),
        .reset       (reset),
        .start       (packet_valid),
        .feature     (packet),
        .layer_output(layer_score),
        .busy        (layer_busy),
        .done        (layer_done)
    );

    ReLU_quantizer #(
        .DATA_NUMBER(4),
        .QSHIFT     (MODEL_QSHIFT)
    ) ReLU_quantizer_inst (
        .clk(clk),
        .reset(reset),
        .start(layer_done),
        .data32(layer_score),
        .data8(normal_score),
        .done(normal_done)
    );

    dense_neuron #(
        .FEATURE_NUMBER(4),
        .BIAS          (MODEL_OUTPUT_BIAS),
        .WEIGHT_FILE("output_neuron_weights.mem")
    ) output_neuron_inst (
        .clk    (clk),
        .reset  (reset),
        .start  (normal_done),
        .feature(output_neuron_input),
        .busy   (output_neuron_busy),
        .done   (output_neuron_done),
        .score  (output_neuron_score)
    );

    UART_response_TX #(
        .CLOCK_HZ (CLOCK_HZ),
        .BAUD_RATE(BAUD_RATE),
        .DATA_NUMBER(1)
    ) UART_response_TX_inst (
        .clk   (clk),
        .reset (reset),
        .tx    (tx),
        .data32(response), // We need to still pass an array even if the array has only 1 element
        .send  (output_neuron_done),
        .busy  (response_busy)
    );



endmodule