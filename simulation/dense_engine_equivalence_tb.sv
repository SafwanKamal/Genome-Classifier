`timescale 1ns / 1ps

/*
Runs the sequential (PIPELINED = 0) and pipelined (PIPELINED = 1) dense_engine
side by side on random inputs, and checks both against a reference computed here
from the same weight and bias files.

The shape and files are parameters so one testbench covers several cases, e.g.
    -GINPUT_NUMBER=5 -GOUTPUT_NUMBER=6 -GMAC_LANES=4
    -GWEIGHT_FILE=\"dense_engine_equivalence_5x6x4_weights.mem\" ...
The default shape is the one the files in this directory were generated for.
*/
module dense_engine_equivalence_tb;

    parameter integer INPUT_NUMBER  = 5;
    parameter integer OUTPUT_NUMBER = 6;
    parameter integer MAC_LANES     = 4;

    parameter integer INPUT_WIDTH  = 8;
    parameter integer WEIGHT_WIDTH = 8;
    parameter integer ACC_WIDTH    = 32;

    parameter string WEIGHT_FILE = "dense_engine_equivalence_5x6x4_weights.mem";
    parameter string BIAS_FILE   = "dense_engine_equivalence_5x6x4_biases.mem";
    parameter string ROM_STYLE   = "";

    parameter integer TEST_NUMBER = 200;

    localparam integer OUTPUT_GROUP_NUMBER = (OUTPUT_NUMBER + MAC_LANES - 1) / MAC_LANES;
    localparam integer WEIGHT_DEPTH = OUTPUT_GROUP_NUMBER * INPUT_NUMBER;
    localparam integer WEIGHT_WORD_WIDTH = MAC_LANES * WEIGHT_WIDTH;
    localparam integer BIAS_WORD_WIDTH = MAC_LANES * ACC_WIDTH;

    // sequential: 3 clocks per input, pipelined: 1 clock per input plus 2 to fill
    localparam integer SEQUENTIAL_CYCLES = 3 * WEIGHT_DEPTH;
    localparam integer PIPELINED_CYCLES  = WEIGHT_DEPTH + 2;
    localparam integer MAXIMUM_CYCLES    = SEQUENTIAL_CYCLES + 10;

    logic clk;
    logic reset;
    logic start;

    logic signed [INPUT_WIDTH-1:0] input_data
        [0:INPUT_NUMBER-1];

    logic sequential_busy, sequential_done;
    logic pipelined_busy, pipelined_done;

    logic signed [ACC_WIDTH-1:0] sequential_output
        [0:OUTPUT_NUMBER-1];

    logic signed [ACC_WIDTH-1:0] pipelined_output
        [0:OUTPUT_NUMBER-1];

    logic signed [ACC_WIDTH-1:0] expected_output
        [0:OUTPUT_NUMBER-1];

    // Copies of the ROMs for the reference model
    logic [WEIGHT_WORD_WIDTH-1:0] weight_ROM [0:WEIGHT_DEPTH-1];
    logic [BIAS_WORD_WIDTH-1:0] bias_ROM [0:OUTPUT_GROUP_NUMBER-1];


    dense_engine #(
        .INPUT_NUMBER (INPUT_NUMBER),
        .OUTPUT_NUMBER(OUTPUT_NUMBER),
        .MAC_LANES    (MAC_LANES),

        .INPUT_WIDTH (INPUT_WIDTH),
        .WEIGHT_WIDTH(WEIGHT_WIDTH),
        .ACC_WIDTH   (ACC_WIDTH),

        .WEIGHT_FILE(WEIGHT_FILE),
        .BIAS_FILE  (BIAS_FILE),
        .ROM_STYLE  (ROM_STYLE),
        .PIPELINED  (0)
    ) sequential_engine_inst (
        .clk        (clk),
        .reset      (reset),
        .start      (start),
        .input_data (input_data),
        .busy       (sequential_busy),
        .done       (sequential_done),
        .output_data(sequential_output)
    );

    dense_engine #(
        .INPUT_NUMBER (INPUT_NUMBER),
        .OUTPUT_NUMBER(OUTPUT_NUMBER),
        .MAC_LANES    (MAC_LANES),

        .INPUT_WIDTH (INPUT_WIDTH),
        .WEIGHT_WIDTH(WEIGHT_WIDTH),
        .ACC_WIDTH   (ACC_WIDTH),

        .WEIGHT_FILE(WEIGHT_FILE),
        .BIAS_FILE  (BIAS_FILE),
        .ROM_STYLE  (ROM_STYLE),
        .PIPELINED  (1)
    ) pipelined_engine_inst (
        .clk        (clk),
        .reset      (reset),
        .start      (start),
        .input_data (input_data),
        .busy       (pipelined_busy),
        .done       (pipelined_done),
        .output_data(pipelined_output)
    );


    always #5 clk = ~clk;


    /*
    Reference model: bias plus the dot product, using the same packing as
    dense_engine (lane 0 in the most significant bits of each word).
    */
    task automatic compute_expected_output;
        logic signed [ACC_WIDTH-1:0] accumulator;
        logic signed [WEIGHT_WIDTH-1:0] weight;
        integer group_index;
        integer lane;

        begin
            for (int output_index = 0; output_index < OUTPUT_NUMBER; output_index++) begin
                group_index = output_index / MAC_LANES;
                lane = output_index % MAC_LANES;

                accumulator = $signed(
                    bias_ROM[group_index][BIAS_WORD_WIDTH - lane * ACC_WIDTH - 1 -: ACC_WIDTH]
                );

                for (int input_index = 0; input_index < INPUT_NUMBER; input_index++) begin
                    weight = $signed(
                        weight_ROM[group_index * INPUT_NUMBER + input_index][
                            WEIGHT_WORD_WIDTH - lane * WEIGHT_WIDTH - 1 -: WEIGHT_WIDTH
                        ]
                    );
                    accumulator = accumulator + weight * input_data[input_index];
                end

                expected_output[output_index] = accumulator;
            end
        end
    endtask


    task automatic run_current_test (
        input integer test_number
    );

        integer cycle_count;
        integer sequential_cycles;
        integer pipelined_cycles;
        integer error_count;

        begin
            for (int i = 0; i < INPUT_NUMBER; i++) begin
                input_data[i] = $urandom();
            end

            compute_expected_output();

            // Start is asserted away from the active clock edge.
            @(negedge clk);
            start = 1'b1;

            @(negedge clk);
            start = 1'b0;

            // Both engines must have captured the inputs on start.
            for (int i = 0; i < INPUT_NUMBER; i++) begin
                input_data[i] = $urandom();
            end

            cycle_count = 0;
            sequential_cycles = -1;
            pipelined_cycles = -1;

            while (
                (sequential_cycles < 0 || pipelined_cycles < 0) &&
                cycle_count < MAXIMUM_CYCLES
            ) begin
                if (pipelined_done === 1'b1 && pipelined_cycles < 0) begin
                    pipelined_cycles = cycle_count;
                end

                if (sequential_done === 1'b1 && sequential_cycles < 0) begin
                    sequential_cycles = cycle_count;
                end

                @(negedge clk);
                cycle_count++;
            end

            if (sequential_cycles < 0 || pipelined_cycles < 0) begin
                $fatal(
                    1,
                    "Test %0d timed out after %0d cycles",
                    test_number,
                    cycle_count
                );
            end

            if (
                sequential_cycles != SEQUENTIAL_CYCLES ||
                pipelined_cycles != PIPELINED_CYCLES
            ) begin
                $fatal(
                    1,
                    "Test %0d: cycles sequential %0d (expected %0d), pipelined %0d (expected %0d)",
                    test_number,
                    sequential_cycles,
                    SEQUENTIAL_CYCLES,
                    pipelined_cycles,
                    PIPELINED_CYCLES
                );
            end

            error_count = 0;

            for (int i = 0; i < OUTPUT_NUMBER; i++) begin
                if (
                    sequential_output[i] !== expected_output[i] ||
                    pipelined_output[i] !== expected_output[i]
                ) begin
                    $error(
                        "Test %0d output %0d: expected %0d, sequential %0d, pipelined %0d",
                        test_number,
                        i,
                        $signed(expected_output[i]),
                        $signed(sequential_output[i]),
                        $signed(pipelined_output[i])
                    );

                    error_count++;
                end
            end

            if (error_count != 0) begin
                $fatal(
                    1,
                    "Test %0d failed with %0d incorrect outputs",
                    test_number,
                    error_count
                );
            end
        end
    endtask


    initial begin
        clk   = 1'b0;
        reset = 1'b1;
        start = 1'b0;

        for (int i = 0; i < INPUT_NUMBER; i++) begin
            input_data[i] = '0;
        end

        $readmemh(WEIGHT_FILE, weight_ROM);
        $readmemh(BIAS_FILE, bias_ROM);

        repeat (3) @(posedge clk);

        @(negedge clk);
        reset = 1'b0;

        for (int test_number = 1; test_number <= TEST_NUMBER; test_number++) begin
            run_current_test(test_number);
        end

        $display(
            "PASS: %0d random tests, %0dx%0d with %0d lanes, sequential %0d cycles, pipelined %0d cycles",
            TEST_NUMBER,
            INPUT_NUMBER,
            OUTPUT_NUMBER,
            MAC_LANES,
            SEQUENTIAL_CYCLES,
            PIPELINED_CYCLES
        );

        $finish;
    end

endmodule
