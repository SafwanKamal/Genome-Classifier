`timescale 1ns / 1ps

module variant_triage_core_tb;

    localparam integer FEATURE_NUMBER   = 16;
    localparam integer HIDDEN_NUMBER    = 8;
    localparam integer HIDDEN_MAC_LANES = 4;
    localparam integer TEST_NUMBER      = 1000;

    localparam integer VECTOR_WIDTH =
        FEATURE_NUMBER * 8 + 32;

    localparam integer MAXIMUM_CYCLES = 200;

    parameter string VECTOR_FILE =
        "model_v2_h8_core_vectors.mem";


    `include "model_parameters.svh"


    logic clk;
    logic reset;
    logic start;

    logic [7:0] feature
        [0:FEATURE_NUMBER - 1];

    logic busy;
    logic done;

    logic signed [31:0] score;

    logic [VECTOR_WIDTH - 1:0]
        test_vector_ROM [0:TEST_NUMBER - 1];

    integer passed_test_number;
    integer maximum_observed_cycles;


    /*
     * 100 MHz clock:
     * period = 10 ns
     */
    always begin
        #5 clk = ~clk;
    end


    variant_triage_core #(
        .FEATURE_NUMBER   (FEATURE_NUMBER),
        .REQUANT_SHIFT    (MODEL_QSHIFT),
        .HIDDEN_NUMBER    (HIDDEN_NUMBER),
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
    ) DUT (
        .clk    (clk),
        .reset  (reset),
        .start  (start),
        .feature(feature),
        .busy   (busy),
        .done   (done),
        .score  (score)
    );


    task automatic run_test (
        input integer test_index
    );

        logic signed [31:0] expected_score;
        integer cycle_number;

        begin
            /*
             * Do not start while the core is busy.
             */
            while (busy) begin
                @(negedge clk);
            end


            /*
             * Extract the 16 feature bytes.
             *
             * Feature zero occupies the most-significant
             * byte of the test-vector memory word.
             */
            for (
                int feature_index = 0;
                feature_index < FEATURE_NUMBER;
                feature_index++
            ) begin
                feature[feature_index] =
                    test_vector_ROM[test_index][
                        VECTOR_WIDTH
                        - 1
                        - feature_index * 8
                        -: 8
                    ];
            end


            /*
             * The expected signed INT32 score occupies
             * the least-significant 32 bits.
             */
            expected_score = $signed(
                test_vector_ROM[test_index][31:0]
            );


            /*
             * Assert start for one clock cycle.
             *
             * Testbench signals change on falling edges
             * so they are stable before rising edges.
             */
            @(negedge clk);
            start = 1'b1;

            @(negedge clk);
            start = 1'b0;


            /*
             * Wait for completion with a timeout.
             */
            cycle_number = 0;

            while (
                !done
                && cycle_number < MAXIMUM_CYCLES
            ) begin
                @(negedge clk);
                cycle_number++;
            end


            if (!done) begin
                $fatal(
                    1,
                    "Test %0d timed out after %0d cycles",
                    test_index,
                    MAXIMUM_CYCLES
                );
            end


            if (
                cycle_number
                > maximum_observed_cycles
            ) begin
                maximum_observed_cycles =
                    cycle_number;
            end


            /*
             * Case inequality also detects X and Z.
             */
            if (score !== expected_score) begin
                $display(
                    "FAIL: test %0d",
                    test_index
                );

                $display(
                    "Expected score: %0d",
                    expected_score
                );

                $display(
                    "Received score: %0d",
                    score
                );

                $display(
                    "Inference cycles: %0d",
                    cycle_number
                );

                for (
                    int feature_index = 0;
                    feature_index < FEATURE_NUMBER;
                    feature_index++
                ) begin
                    $display(
                        "feature[%0d] = %0d",
                        feature_index,
                        $signed(feature[feature_index])
                    );
                end

                $fatal(
                    1,
                    "Bit-exact V2 score mismatch"
                );
            end


            passed_test_number++;

            if (
                passed_test_number == 1
                || passed_test_number % 100 == 0
            ) begin
                $display(
                    "Passed %0d/%0d tests; current cycles = %0d",
                    passed_test_number,
                    TEST_NUMBER,
                    cycle_number
                );
            end


            /*
             * Allow the done pulse to clear before
             * starting the next test.
             */
            @(negedge clk);
        end

    endtask


    initial begin
        clk                     = 1'b0;
        reset                   = 1'b1;
        start                   = 1'b0;
        passed_test_number      = 0;
        maximum_observed_cycles = 0;

        for (
            int feature_index = 0;
            feature_index < FEATURE_NUMBER;
            feature_index++
        ) begin
            feature[feature_index] = 8'h00;
        end


        $readmemh(
            VECTOR_FILE,
            test_vector_ROM
        );


        /*
         * Hold reset for five clock cycles.
         */
        repeat (5) begin
            @(negedge clk);
        end

        reset = 1'b0;


        /*
         * Wait two additional cycles before
         * starting the first inference.
         */
        repeat (2) begin
            @(negedge clk);
        end


        for (
            int test_index = 0;
            test_index < TEST_NUMBER;
            test_index++
        ) begin
            run_test(test_index);
        end


        $display(
            "PASS: all %0d V2 core tests matched",
            passed_test_number
        );

        $display(
            "Maximum observed inference latency: %0d cycles",
            maximum_observed_cycles
        );

        $finish;
    end

endmodule