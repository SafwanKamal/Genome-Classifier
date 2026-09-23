`timescale 1ns / 1ps

module dense_engine_tb;

    localparam integer INPUT_NUMBER  = 3;
    localparam integer OUTPUT_NUMBER = 6;
    localparam integer MAC_LANES     = 4;

    localparam integer INPUT_WIDTH  = 8;
    localparam integer WEIGHT_WIDTH = 8;
    localparam integer ACC_WIDTH    = 32;

    localparam integer MAXIMUM_CYCLES = 100;

    // Run with -GPIPELINED=1 (or the simulator's equivalent) to test the pipelined schedule.
    parameter integer PIPELINED = 0;

    logic clk;
    logic reset;
    logic start;

    logic signed [INPUT_WIDTH-1:0] input_data
        [0:INPUT_NUMBER-1];

    logic busy;
    logic done;

    logic signed [ACC_WIDTH-1:0] output_data
        [0:OUTPUT_NUMBER-1];

    logic signed [ACC_WIDTH-1:0] expected_output
        [0:OUTPUT_NUMBER-1];


    dense_engine #(
        .INPUT_NUMBER (INPUT_NUMBER),
        .OUTPUT_NUMBER(OUTPUT_NUMBER),
        .MAC_LANES    (MAC_LANES),

        .INPUT_WIDTH (INPUT_WIDTH),
        .WEIGHT_WIDTH(WEIGHT_WIDTH),
        .ACC_WIDTH   (ACC_WIDTH),

        .WEIGHT_FILE(
            "dense_engine_test_weights.mem"
        ),

        .BIAS_FILE(
            "dense_engine_test_biases.mem"
        ),

        .PIPELINED(PIPELINED)
    ) dense_engine_inst (
        .clk        (clk),
        .reset      (reset),
        .start      (start),
        .input_data (input_data),
        .busy       (busy),
        .done       (done),
        .output_data(output_data)
    );


    always #5 clk = ~clk;


    task automatic run_current_test (
        input integer test_number
    );

        integer cycle_count;
        integer error_count;

        begin
            // Start is asserted away from the active clock edge.
            @(negedge clk);
            start = 1'b1;

            @(negedge clk);
            start = 1'b0;

            /*
            Change the external inputs after they have been captured.
            The result must still use the original input vector.
            */
            for (int i = 0; i < INPUT_NUMBER; i++) begin
                input_data[i] = 8'sd99;
            end

            cycle_count = 0;

            while (
                done !== 1'b1 &&
                cycle_count < MAXIMUM_CYCLES
            ) begin
                @(negedge clk);
                cycle_count++;
            end

            if (done !== 1'b1) begin
                $fatal(
                    1,
                    "Test %0d timed out after %0d cycles",
                    test_number,
                    cycle_count
                );
            end

            error_count = 0;

            for (int i = 0; i < OUTPUT_NUMBER; i++) begin
                if (output_data[i] !== expected_output[i]) begin
                    $error(
                        "Test %0d output %0d: expected %0d, received %0d",
                        test_number,
                        i,
                        $signed(expected_output[i]),
                        $signed(output_data[i])
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

            $display(
                "Test %0d passed in %0d cycles",
                test_number,
                cycle_count
            );

            for (int i = 0; i < OUTPUT_NUMBER; i++) begin
                $display(
                    "    output[%0d] = %0d",
                    i,
                    $signed(output_data[i])
                );
            end

            // Allow the OUTPUT state to return to IDLE.
            @(negedge clk);

            if (busy !== 1'b0) begin
                $fatal(
                    1,
                    "Engine remained busy after test %0d",
                    test_number
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

        for (int i = 0; i < OUTPUT_NUMBER; i++) begin
            expected_output[i] = '0;
        end

        repeat (3) @(posedge clk);

        @(negedge clk);
        reset = 1'b0;


        /*
        Test 1

        Input = [2, -3, 4]

        Output 0 =  10 + 1(2) +  2(-3) +  3(4) =  18
        Output 1 =  -5 - 1(2) +  4(-3) +  2(4) = -11
        Output 2 =   7 + 5(2) -  2(-3) -  1(4) =  19
        Output 3 =   1 + 0(2) +  3(-3) -  4(4) = -24
        Output 4 = -10 + 2(2) +  2(-3) +  2(4) =  -4
        Output 5 =  20 - 3(2) +  1(-3) +  5(4) =  31
        */

        input_data[0] =  8'sd2;
        input_data[1] = -8'sd3;
        input_data[2] =  8'sd4;

        expected_output[0] =  32'sd18;
        expected_output[1] = -32'sd11;
        expected_output[2] =  32'sd19;
        expected_output[3] = -32'sd24;
        expected_output[4] = -32'sd4;
        expected_output[5] =  32'sd31;

        run_current_test(1);


        /*
        Test 2

        Input = [-1, 0, 2]

        This verifies that the engine can accept another transaction
        after returning to IDLE.
        */

        input_data[0] = -8'sd1;
        input_data[1] =  8'sd0;
        input_data[2] =  8'sd2;

        expected_output[0] =  32'sd15;
        expected_output[1] =  32'sd0;
        expected_output[2] =  32'sd0;
        expected_output[3] = -32'sd7;
        expected_output[4] = -32'sd8;
        expected_output[5] =  32'sd33;

        run_current_test(2);


        $display(
            "PASS: dense_engine completed all tests correctly"
        );

        $finish;
    end

endmodule