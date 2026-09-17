`timescale 1ns / 1ps

module rmii_tx_tb;

    logic clk = 1'b0;
    logic reset = 1'b1;

    logic [7:0] data_in = 8'h00;
    logic valid_in = 1'b0;
    logic ready_out;
    logic last_in = 1'b0;
    logic [1:0] rmii_txd;
    logic rmii_tx_en;
    logic underflow_out;

    logic [1:0] expected_pairs[$];
    logic [1:0] expected_pair;

    integer accepted_bytes = 0;
    integer transmitted_pairs = 0;
    integer underflow_count = 0;
    integer frame_count = 0;

    integer bytes_before;
    integer pairs_before;
    integer underflows_before;
    integer frames_before;

    logic previous_tx_en = 1'b0;
    logic previous_underflow = 1'b0;

    rmii_tx dut (
        .clk(clk),
        .reset(reset),
        .data_in(data_in),
        .valid_in(valid_in),
        .ready_out(ready_out),
        .last_in(last_in),
        .rmii_txd(rmii_txd),
        .rmii_tx_en(rmii_tx_en),
        .underflow_out(underflow_out)
    );

    // 50 MHz clock
    always #10 clk = ~clk;

    // Sample before the dut's nonblocking register updates.
    // The output pair sampled here has completed its clock interval.
    always @(posedge clk or posedge reset) begin
        if (reset) begin
            expected_pairs.delete();
            previous_tx_en = 1'b0;
            previous_underflow = 1'b0;
        end else begin
            if ($isunknown({ready_out, rmii_tx_en, underflow_out}))
                $fatal(1, "unknown control output");

            // Check outgoing data before adding newly accepted input.
            if (rmii_tx_en) begin
                if (expected_pairs.size() == 0)
                    $fatal(1, "unexpected extra output pair");

                expected_pair = expected_pairs.pop_front();

                if (rmii_txd !== expected_pair)
                    $fatal(1,
                        "pair mismatch: expected %b, received %b",
                        expected_pair, rmii_txd);

                transmitted_pairs = transmitted_pairs + 1;
            end

            if (rmii_tx_en && !previous_tx_en)
                frame_count = frame_count + 1;

            // A frame must not stop while accepted data remains unsent.
            if (!rmii_tx_en && previous_tx_en &&
                expected_pairs.size() != 0)
                $fatal(1, "transmission stopped before all pairs were sent");

            if (underflow_out) begin
                if (previous_underflow)
                    $fatal(1, "underflow lasted longer than one cycle");

                if (rmii_tx_en)
                    $fatal(1, "transmission still enabled during underflow");

                underflow_count = underflow_count + 1;
            end

            // Build expected output from accepted bytes.
            if (valid_in && ready_out) begin
                expected_pairs.push_back(data_in[1:0]);
                expected_pairs.push_back(data_in[3:2]);
                expected_pairs.push_back(data_in[5:4]);
                expected_pairs.push_back(data_in[7:6]);
                accepted_bytes = accepted_bytes + 1;
            end

            previous_tx_en = rmii_tx_en;
            previous_underflow = underflow_out;
        end
    end

    // Hold input stable until the byte is accepted.
    task automatic send_byte(
        input logic [7:0] value,
        input logic is_last
    );
        begin
            @(negedge clk);
            data_in = value;
            last_in = is_last;
            valid_in = 1'b1;

            @(posedge clk);
            while (!ready_out)
                @(posedge clk);

            @(negedge clk);
            valid_in = 1'b0;

            // Change inputs after acceptance to check that they were captured.
            data_in = ~value;
            last_in = !is_last;
        end
    endtask

    task automatic wait_for_idle;
        integer cycles;
        begin
            cycles = 0;

            @(negedge clk);
            while (rmii_tx_en || !ready_out) begin
                cycles = cycles + 1;
                if (cycles > 100)
                    $fatal(1, "timeout waiting for idle");
                @(negedge clk);
            end

            // Allow the monitor to observe completion and any error pulse.
            repeat (2) @(negedge clk);

            if (expected_pairs.size() != 0)
                $fatal(1, "unsent pairs remain");

            if (rmii_tx_en !== 1'b0 ||
                ready_out !== 1'b1 ||
                underflow_out !== 1'b0)
                $fatal(1, "incorrect idle outputs");
        end
    endtask

    task automatic mark_counts;
        begin
            bytes_before = accepted_bytes;
            pairs_before = transmitted_pairs;
            underflows_before = underflow_count;
            frames_before = frame_count;
        end
    endtask

    task automatic check_counts(
        input integer bytes_expected,
        input integer pairs_expected,
        input integer underflows_expected,
        input integer frames_expected
    );
        begin
            if (accepted_bytes - bytes_before != bytes_expected)
                $fatal(1, "incorrect accepted byte count");

            if (transmitted_pairs - pairs_before != pairs_expected)
                $fatal(1, "incorrect transmitted pair count");

            if (underflow_count - underflows_before != underflows_expected)
                $fatal(1, "incorrect underflow count");

            if (frame_count - frames_before != frames_expected)
                $fatal(1, "incorrect frame count or gap inside frame");
        end
    endtask

    initial begin
        repeat (3) @(negedge clk);
        reset = 1'b0;

        // Idle must not produce an underflow.
        mark_counts();
        repeat (6) @(negedge clk);
        wait_for_idle();
        check_counts(0, 0, 0, 0);
        $display("pass: idle");

        // E4 must produce 00, 01, 10, 11.
        mark_counts();
        send_byte(8'hE4, 1'b1);
        wait_for_idle();
        check_counts(1, 4, 0, 1);
        $display("pass: single byte and captured last flag");

        // One uninterrupted frame containing several distinct patterns.
        mark_counts();
        send_byte(8'hE4, 1'b0);
        send_byte(8'h1B, 1'b0);
        send_byte(8'h00, 1'b0);
        send_byte(8'hFF, 1'b0);
        send_byte(8'h96, 1'b1);
        wait_for_idle();
        check_counts(5, 20, 0, 1);
        $display("pass: consecutive bytes without gaps");

        // No next byte is supplied after a non-final byte.
        mark_counts();
        send_byte(8'hA6, 1'b0);
        wait_for_idle();
        check_counts(1, 4, 1, 1);
        $display("pass: underflow after completing current byte");

        // Transmission must work again after an underflow.
        mark_counts();
        send_byte(8'h39, 1'b1);
        wait_for_idle();
        check_counts(1, 4, 0, 1);
        $display("pass: recovery after underflow");

        // Interrupt an active byte with an asynchronous reset.
        send_byte(8'hD2, 1'b0);
        @(negedge clk);

        if (!rmii_tx_en)
            $fatal(1, "reset test did not reach active transmission");

        #3;
        reset = 1'b1;
        valid_in = 1'b0;
        last_in = 1'b0;

        #1;
        if (rmii_tx_en !== 1'b0 ||
            ready_out !== 1'b1 ||
            underflow_out !== 1'b0)
            $fatal(1, "asynchronous reset outputs incorrect");

        repeat (2) @(negedge clk);
        reset = 1'b0;
        wait_for_idle();
        $display("pass: reset during transmission");

        mark_counts();
        send_byte(8'h4B, 1'b1);
        wait_for_idle();
        check_counts(1, 4, 0, 1);
        $display("pass: transmission after reset");

        $display("all rmii_tx tests passed");
        $finish;
    end

    // Prevent a broken handshake from hanging the simulation.
    initial begin
        #100000;
        $fatal(1, "global simulation timeout");
    end

endmodule