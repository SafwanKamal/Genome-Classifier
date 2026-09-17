`timescale 1ns / 1ps

module ethernet_TX_tb;

    logic clk = 0, reset = 0;
    logic [7:0] data_in = 0;
    logic valid_in = 0, last_in = 0;
    logic ready_out, busy_out, done_out, underflow_out;
    logic [1:0] rmii_txd;
    logic rmii_tx_en;

    logic [7:0] expected_bytes[$];
    integer expected_lengths[$];

    logic [7:0] received_byte, expected_byte;
    logic previous_en = 0;
    logic previous_done = 0;
    logic previous_underflow = 0;
    logic seen_frame_end = 0;

    integer pair_index = 0;
    integer remaining_bytes = 0;
    integer idle_cycles = 0;

    integer starts = 0;
    integer ends = 0;
    integer completions = 0;
    integer underflows = 0;
    integer accepted = 0;

    integer starts_before, ends_before, completions_before;
    integer underflows_before, accepted_before;

    ethernet_TX dut (
        .clk(clk),
        .reset(reset),
        .data_in(data_in),
        .valid_in(valid_in),
        .ready_out(ready_out),
        .last_in(last_in),
        .rmii_txd(rmii_txd),
        .rmii_tx_en(rmii_tx_en),
        .busy_out(busy_out),
        .done_out(done_out),
        .underflow_out(underflow_out)
    );

    // 50 mhz clock
    always #10 clk = ~clk;

    // broadcast destination, local source, experimental ethertype
    function automatic logic [7:0] frame_byte(
        input integer index,
        input integer seed
    );
        case (index)
            0, 1, 2, 3, 4, 5:
                frame_byte = 8'hFF;

            6:
                frame_byte = 8'h02;

            7, 8, 9, 10:
                frame_byte = 8'h00;

            11:
                frame_byte = 8'h01;

            12:
                frame_byte = 8'h88;

            13:
                frame_byte = 8'hB5;

            default:
                frame_byte = (index * 73 + seed) & 255;
        endcase
    endfunction

    // reference fcs values are precomputed independently with python zlib
    task automatic expect_frame(
        input integer length,
        input integer seed,
        input logic [31:0] expected_crc,
        input logic aborted
    );
        integer body_length;

        body_length = (length < 60) ? 60 : length;

        if (aborted)
            expected_lengths.push_back(8 + length);
        else
            expected_lengths.push_back(8 + body_length + 4);

        repeat (7)
            expected_bytes.push_back(8'h55);

        expected_bytes.push_back(8'hD5);

        for (int i = 0; i < length; i = i + 1)
            expected_bytes.push_back(frame_byte(i, seed));

        if (!aborted) begin
            for (int i = length; i < body_length; i = i + 1)
                expected_bytes.push_back(8'h00);

            for (int i = 0; i < 4; i = i + 1)
                expected_bytes.push_back(expected_crc[i * 8 +: 8]);
        end
    endtask

    // sample the dibit interval ending at this edge, before dut updates
    always @(posedge clk or posedge reset) begin
        if (reset) begin
            expected_bytes.delete();
            expected_lengths.delete();

            received_byte = 0;
            pair_index = 0;
            remaining_bytes = 0;
            idle_cycles = 0;
            seen_frame_end = 0;

            previous_en = 0;
            previous_done = 0;
            previous_underflow = 0;
        end else begin
            if ($isunknown({
                ready_out,
                busy_out,
                done_out,
                underflow_out,
                rmii_tx_en
            }))
                $fatal(1, "unknown control output");

            if (valid_in && ready_out)
                accepted = accepted + 1;

            if (rmii_tx_en) begin
                if (!busy_out)
                    $fatal(1, "transmitting while busy_out is low");

                if (!previous_en) begin
                    if (seen_frame_end && idle_cycles < 48)
                        $fatal(1,
                            "interframe gap too short: %0d",
                            idle_cycles);

                    if (expected_lengths.size() == 0)
                        $fatal(1, "unexpected frame");

                    remaining_bytes = expected_lengths.pop_front();
                    starts = starts + 1;
                end

                idle_cycles = 0;

                if (remaining_bytes == 0)
                    $fatal(1, "extra dibit after expected frame end");

                received_byte[pair_index * 2 +: 2] = rmii_txd;

                if (pair_index == 3) begin
                    if (expected_bytes.size() == 0)
                        $fatal(1, "unexpected output byte");

                    expected_byte = expected_bytes.pop_front();

                    if (received_byte !== expected_byte)
                        $fatal(1,
                            "byte mismatch: expected %02h, received %02h",
                            expected_byte, received_byte);

                    remaining_bytes = remaining_bytes - 1;
                    pair_index = 0;
                end else begin
                    pair_index = pair_index + 1;
                end
            end else begin
                idle_cycles = idle_cycles + 1;

                if (previous_en) begin
                    if (pair_index != 0 || remaining_bytes != 0)
                        $fatal(1, "frame ended early or inside a byte");

                    seen_frame_end = 1;
                    ends = ends + 1;
                end
            end

            if (done_out) begin
                if (previous_done || rmii_tx_en ||
                    !busy_out || underflow_out)
                    $fatal(1, "incorrect done pulse timing");

                completions = completions + 1;
            end

            if (underflow_out) begin
                if (previous_underflow || rmii_tx_en || ready_out)
                    $fatal(1, "incorrect underflow behavior");

                underflows = underflows + 1;
            end

            previous_en = rmii_tx_en;
            previous_done = done_out;
            previous_underflow = underflow_out;
        end
    end

    // hold each byte until the wrapper accepts it
    task automatic send_byte(
        input logic [7:0] value,
        input logic is_last
    );
        @(negedge clk);
        data_in = value;
        valid_in = 1;
        last_in = is_last;

        @(posedge clk);
        while (!ready_out)
            @(posedge clk);
    endtask

    task automatic stop_source;
        @(negedge clk);
        valid_in = 0;
        last_in = 0;
        data_in = 8'hA6;
    endtask

    task automatic send_frame(
        input integer length,
        input integer seed
    );
        for (int i = 0; i < length; i = i + 1)
            send_byte(frame_byte(i, seed), i == length - 1);

        stop_source();
    endtask

    task automatic wait_for_idle;
        integer cycles;

        cycles = 0;

        @(negedge clk);
        while (busy_out || rmii_tx_en) begin
            cycles = cycles + 1;

            if (cycles > 10000)
                $fatal(1, "timeout waiting for idle");

            @(negedge clk);
        end

        repeat (2)
            @(negedge clk);

        if (expected_bytes.size() != 0 ||
            expected_lengths.size() != 0)
            $fatal(1, "expected output remains unsent");

        if (done_out || underflow_out || ready_out)
            $fatal(1, "incorrect idle outputs");
    endtask

    task automatic mark_counts;
        starts_before = starts;
        ends_before = ends;
        completions_before = completions;
        underflows_before = underflows;
        accepted_before = accepted;
    endtask

    task automatic check_counts(
        input integer frame_count,
        input integer done_count,
        input integer error_count,
        input integer byte_count
    );
        if (starts - starts_before != frame_count ||
            ends - ends_before != frame_count ||
            completions - completions_before != done_count ||
            underflows - underflows_before != error_count ||
            accepted - accepted_before != byte_count)
            $fatal(1,
                "incorrect frame, completion, underflow, or input count");
    endtask

    task automatic run_good(
        input integer length,
        input integer seed,
        input logic [31:0] expected_crc
    );
        mark_counts();

        expect_frame(length, seed, expected_crc, 0);
        send_frame(length, seed);
        wait_for_idle();

        check_counts(1, 1, 0, length);

        $display("pass: input length %0d, framing and fcs", length);
    endtask

    initial begin
        #3;
        reset = 1;

        repeat (3)
            @(negedge clk);

        reset = 0;

        mark_counts();

        repeat (10)
            @(negedge clk);

        wait_for_idle();
        check_counts(0, 0, 0, 0);
        $display("pass: reset and idle");

        // header only: 46 padding bytes
        run_good(14, 0, 32'h87F71B35);

        // padding boundary: one pad byte, no pad, and one byte above
        run_good(59, 19, 32'h3847F297);
        run_good(60, 37, 32'h386A0B64);
        run_good(61, 53, 32'h8CE3334F);

        // full-sized untagged frame before fcs
        run_good(1514, 71, 32'h861AB214);

        // offer the second frame before the first finishes on rmii
        mark_counts();

        expect_frame(60, 37, 32'h386A0B64, 0);
        expect_frame(14, 0, 32'h87F71B35, 0);

        send_frame(60, 37);
        send_frame(14, 0);

        wait_for_idle();
        check_counts(2, 2, 0, 74);
        $display("pass: queued next frame and interframe gap");

        // stop supplying bytes before asserting last_in
        mark_counts();
        expect_frame(3, 0, 0, 1);

        for (int i = 0; i < 3; i = i + 1)
            send_byte(frame_byte(i, 0), 0);

        stop_source();
        wait (underflow_out);

        // a late byte during the error pulse must not restart transmission
        @(negedge clk);
        data_in = 8'hEE;
        valid_in = 1;
        last_in = 1;

        @(posedge clk);
        if (ready_out)
            $fatal(1, "accepted late byte during underflow");

        stop_source();
        wait_for_idle();

        check_counts(1, 0, 1, 3);
        $display("pass: underflow abort and late-byte suppression");

        // successful transmission after an underflow
        run_good(60, 37, 32'h386A0B64);

        // interrupt an active transmission between clock edges
        expect_frame(61, 53, 32'h8CE3334F, 0);
        send_byte(frame_byte(0, 53), 0);

        @(negedge clk);
        if (!rmii_tx_en)
            $fatal(1, "reset test missed active transmission");

        #3;
        reset = 1;
        valid_in = 0;
        last_in = 0;

        #1;
        if ({
            rmii_tx_en,
            busy_out,
            done_out,
            underflow_out,
            ready_out
        } !== 5'b0)
            $fatal(1, "asynchronous reset outputs incorrect");

        repeat (2)
            @(negedge clk);

        reset = 0;
        wait_for_idle();
        $display("pass: asynchronous reset during transmission");

        // successful transmission after reset
        run_good(61, 53, 32'h8CE3334F);

        $display("all ethernet_TX tests passed");
        $finish;
    end

    // prevent a broken handshake from hanging the simulation
    initial begin
        #1000000;
        $fatal(1, "global simulation timeout");
    end

endmodule