`timescale 1ns / 1ps

module score_clock_domain_crosser_tb;

    localparam integer DATA_WIDTH = 32;

    logic source_clk = 1'b0;
    logic source_reset = 1'b0;
    logic [DATA_WIDTH-1:0] source_data = '0;
    logic source_send = 1'b0;
    logic source_ready;

    logic destination_clk = 1'b0;
    logic destination_reset = 1'b0;
    logic [DATA_WIDTH-1:0] destination_data;
    logic destination_valid;
    logic destination_ready = 1'b0;

    score_clock_domain_crosser #(
        .DATA_WIDTH(DATA_WIDTH)
    ) dut (
        .source_clk        (source_clk),
        .source_reset      (source_reset),
        .source_data       (source_data),
        .source_send       (source_send),
        .source_ready      (source_ready),

        .destination_clk   (destination_clk),
        .destination_reset (destination_reset),
        .destination_data  (destination_data),
        .destination_valid (destination_valid),
        .destination_ready (destination_ready)
    );

    // Source domain: 100 MHz.
    always #5 source_clk = !source_clk;

    // Destination domain: 50 MHz with a phase offset.
    initial begin
        #3;

        forever #10 destination_clk = !destination_clk;
    end

    task automatic wait_for_source_ready;
        integer cycles;

        cycles = 0;

        while (!source_ready) begin
            @(posedge source_clk);
            cycles = cycles + 1;

            if (cycles > 20)
                $fatal(1, "Timeout waiting for source_ready");
        end
    endtask

    task automatic wait_for_destination_valid;
        integer cycles;

        cycles = 0;

        while (!destination_valid) begin
            @(posedge destination_clk);
            cycles = cycles + 1;

            if (cycles > 20)
                $fatal(1, "Timeout waiting for destination_valid");
        end
    endtask

    task automatic send_value(
        input logic [DATA_WIDTH-1:0] value,
        input integer hold_cycles
    );
        wait_for_source_ready();

        @(negedge source_clk);
        source_data = value;
        source_send = 1'b1;

        @(negedge source_clk);
        source_send = 1'b0;
        source_data = '0;

        wait_for_destination_valid();

        if (destination_data !== value)
            $fatal(
                1,
                "CDC data mismatch: expected %08h, received %08h",
                value,
                destination_data
            );

        if (source_ready)
            $fatal(1, "Source became ready before acknowledgment");

        repeat (hold_cycles) begin
            @(negedge destination_clk);

            if (!destination_valid)
                $fatal(1, "destination_valid dropped while stalled");

            if (destination_data !== value)
                $fatal(1, "Destination data changed while stalled");
        end

        destination_ready = 1'b1;

        @(negedge destination_clk);
        destination_ready = 1'b0;

        @(negedge destination_clk);

        if (destination_valid)
            $fatal(1, "destination_valid remained high after transfer");

        wait_for_source_ready();
    endtask

    task automatic test_busy_source_rejection;
        logic [DATA_WIDTH-1:0] accepted_value;
        logic [DATA_WIDTH-1:0] rejected_value;

        accepted_value = 32'h1234_ABCD;
        rejected_value = 32'hDEAD_BEEF;

        wait_for_source_ready();

        @(negedge source_clk);
        source_data = accepted_value;
        source_send = 1'b1;

        @(negedge source_clk);
        source_send = 1'b0;

        @(negedge source_clk);

        if (source_ready)
            $fatal(1, "Mailbox did not become busy");

        source_data = rejected_value;
        source_send = 1'b1;

        @(negedge source_clk);
        source_send = 1'b0;
        source_data = '0;

        wait_for_destination_valid();

        if (destination_data !== accepted_value)
            $fatal(1, "Busy mailbox overwrote the accepted value");

        destination_ready = 1'b1;

        @(negedge destination_clk);
        destination_ready = 1'b0;

        wait_for_source_ready();
    endtask

    initial begin
        source_reset = 1'b1;
        destination_reset = 1'b1;

        repeat (4)
            @(posedge source_clk);

        @(negedge source_clk);
        source_reset = 1'b0;
        destination_reset = 1'b0;

        wait_for_source_ready();

        if (destination_valid)
            $fatal(1, "destination_valid asserted after reset");

        send_value(32'h0000_00D8, 0);
        send_value(32'hFFFF_FE1C, 3);
        send_value(32'h8000_0000, 1);
        send_value(32'h7FFF_FFFF, 5);

        test_busy_source_rejection();

        $display(
            "PASS: score_clock_domain_crosser completed all tests"
        );

        $finish;
    end

endmodule
