`timescale 1ns / 1ps

module variant_triage_core_tb;

    localparam integer FEATURE_NUMBER = 16;
    localparam integer TEST_NUMBER    = 1000;

    localparam integer VECTOR_WIDTH =
        FEATURE_NUMBER * 8 + 32;

    localparam integer MAXIMUM_CYCLES = 100;

    parameter string VECTOR_FILE =
        "model_v1_core_vectors.mem";


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


    /*
     * 100 MHz clock:
     * period = 10 ns
     */
    always begin
        #5 clk = ~clk;
    end


    variant_triage_core #(
        .FEATURE_NUMBER(FEATURE_NUMBER),

        .HIDDEN_BIAS_0(MODEL_BIAS_0),
        .HIDDEN_BIAS_1(MODEL_BIAS_1),
        .HIDDEN_BIAS_2(MODEL_BIAS_2),
        .HIDDEN_BIAS_3(MODEL_BIAS_3),

        .OUTPUT_BIAS  (MODEL_OUTPUT_BIAS),
        .REQUANT_SHIFT(MODEL_QSHIFT),

        .HIDDEN_WEIGHT_FILE_0(
            "neuron_0_weights.mem"
        ),

        .HIDDEN_WEIGHT_FILE_1(
            "neuron_1_weights.mem"
        ),

        .HIDDEN_WEIGHT_FILE_2(
            "neuron_2_weights.mem"
        ),

        .HIDDEN_WEIGHT_FILE_3(
            "neuron_3_weights.mem"
        ),

        .OUTPUT_WEIGHT_FILE(
            "output_neuron_weights.mem"
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
             * "-: 8" means select eight bits
             * downward from the specified bit.
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


            expected_score = $signed(
                test_vector_ROM[test_index][31:0]
            );


            /*
             * Assert start for one clock cycle.
             *
             * Signals are changed on falling edges
             * so they are stable before rising edges.
             */
            @(negedge clk);
            start = 1'b1;

            @(negedge clk);
            start = 1'b0;


            /*
             * Wait for completion, with a timeout
             * in case the core FSM becomes stuck.
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
                    "Bit-exact score mismatch"
                );
            end


            passed_test_number++;

            if (
                passed_test_number == 1
                || passed_test_number % 100 == 0
            ) begin
                $display(
                    "Passed %0d/%0d tests",
                    passed_test_number,
                    TEST_NUMBER
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
        clk                = 1'b0;
        reset              = 1'b1;
        start              = 1'b0;
        passed_test_number = 0;

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
            "PASS: all %0d core tests matched",
            passed_test_number
        );

        $finish;
    end

endmodule