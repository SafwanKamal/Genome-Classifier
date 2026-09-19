module variant_triage_top_UART_ethernet #(
    parameter integer CLOCK_HZ      = 100_000_000,
    parameter integer BAUD_RATE     = 115_200,
    parameter integer FEATURE_NUMBER = 16,
    parameter integer HIDDEN_NUMBER = 8,
    parameter integer HIDDEN_MAC_LANES = 4
) (
    input  logic clk,
    input  logic reset,
    input  logic rx,
    output logic tx
);


    `include "model_parameters.svh"

    logic [7:0] packet [0:FEATURE_NUMBER - 1];
    logic       packet_valid;

    logic               core_busy, core_done;
    logic signed [31:0] core_score;
    logic               response_busy;
    logic signed [31:0] response [0:0];

    logic clk_domain_crosser_extracted;
    logic clk_domain_crosser_stable_score_store;
    logic [31:0] clk_domain_crosser_stable_score;

    assign response[0] = core_score;

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

    variant_triage_core #(
        .FEATURE_NUMBER(FEATURE_NUMBER),
        .REQUANT_SHIFT (MODEL_QSHIFT),
        .HIDDEN_NUMBER (HIDDEN_NUMBER),
        .HIDDEN_MAC_LANES (HIDDEN_MAC_LANES),

        .HIDDEN_WEIGHT_FILE(
            "dense_hidden_weights.mem"
        ),

        .HIDDEN_BIAS_FILE(
            "dense_hidden_biases.mem"
        ),

        .OUTPUT_WEIGHT_FILE(
            "dense_output_weights.mem"
        ),

        .OUTPUT_BIAS_FILE(
            "dense_output_biases.mem"
        )
    ) variant_triage_core_inst (
        .clk    (clk),
        .reset  (reset),
        .start  (packet_valid),
        .feature(packet),
        .busy   (core_busy),
        .done   (core_done),
        .score  (core_score)
    );

    // UART_response_TX #(
    //     .CLOCK_HZ (CLOCK_HZ),
    //     .BAUD_RATE(BAUD_RATE),
    //     .DATA_NUMBER(1)
    // ) UART_response_TX_inst (
    //     .clk   (clk),
    //     .reset (reset),
    //     .tx    (tx),
    //     .data32(response), // We need to still pass an array even if the array has only 1 element
    //     .send  (core_done),
    //     .busy  (response_busy)
    // );

    score_clock_domain_crosser score_crosser_inst (
        .clk_100MHz(clk),
        .reset(reset),
        .score(core_score),
        .score_done(core_done),
        .score_extracted(response_busy),
        .stable_score(clk_domain_crosser_stable_score),
        .stable_score_store(clk_domain_crosser_stable_score_store)
    );


endmodule 