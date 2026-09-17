`timescale 1ns / 1ps

module ethernet_TX_buffer_tb;

    localparam int MAX_FRAME_BYTES = 1514;

    logic clk = 0, reset = 0;
    logic [7:0] data_in = 0;
    logic valid_in = 0, last_in = 0;
    logic ready_out, buffer_busy, overflow_out;

    logic [7:0] buffered_data;
    logic buffered_valid, buffered_last, buffered_ready;
    logic tx_ready, tx_busy, tx_done, tx_underflow;
    logic [1:0] rmii_txd;
    logic rmii_tx_en;
    logic starve_tx = 0;

    logic [7:0] expected_wire[$], expected_payload[$];
    logic expected_last[$];
    integer expected_lengths[$];

    logic [7:0] received_byte, expected_byte, held_data;
    logic expected_last_bit, held_last;
    logic previous_en = 0, previous_done = 0;
    logic previous_error = 0, previous_overflow = 0;
    logic stalled = 0, waiting_result = 0, seen_end = 0;

    integer pair_index = 0, remaining_bytes = 0, idle_cycles = 0;
    integer input_length = 0, committed = 0;
    integer starts = 0, ends = 0, completions = 0;
    integer errors = 0, overflows = 0, accepted = 0, forwarded = 0;
    integer input_stalls = 0;

    integer starts_before, ends_before, completions_before;
    integer errors_before, overflows_before, accepted_before;
    integer forwarded_before, stalls_before;

    ethernet_TX_buffer #(
        .ADDR_WIDTH(11),
        .MAX_FRAME_BYTES(MAX_FRAME_BYTES)
    ) buffer_dut (
        .clk(clk),
        .reset(reset),
        .data_in(data_in),
        .valid_in(valid_in),
        .last_in(last_in),
        .ready_out(ready_out),
        .data_out(buffered_data),
        .valid_out(buffered_valid),
        .last_out(buffered_last),
        .ready_in(buffered_ready),
        .tx_done_in(tx_done),
        .tx_underflow_in(tx_underflow),
        .busy_out(buffer_busy),
        .overflow_out(overflow_out)
    );

    // test-only fault injection: block both sides of the byte handshake
    assign buffered_ready = tx_ready && !starve_tx;

    ethernet_TX tx_dut (
        .clk(clk),
        .reset(reset),
        .data_in(buffered_data),
        .valid_in(buffered_valid && !starve_tx),
        .last_in(buffered_last),
        .ready_out(tx_ready),
        .rmii_txd(rmii_txd),
        .rmii_tx_en(rmii_tx_en),
        .busy_out(tx_busy),
        .done_out(tx_done),
        .underflow_out(tx_underflow)
    );

    // 50 mhz clock
    always #10 clk = ~clk;

    function automatic logic [7:0] frame_byte(
        input integer index,
        input integer seed
    );
        case (index)
            0, 1, 2, 3, 4, 5: frame_byte = 8'hFF;
            6: frame_byte = 8'h02;
            7, 8, 9, 10: frame_byte = 8'h00;
            11: frame_byte = 8'h01;
            12: frame_byte = 8'h88;
            13: frame_byte = 8'hB5;
            default: frame_byte = (index * 73 + seed) & 255;
        endcase
    endfunction

    // fcs constants below were generated independently with python zlib
    // for an aborted frame, length is the expected transmitted prefix length
    task automatic expect_frame(
        input integer length,
        input integer seed,
        input logic [31:0] crc,
        input logic aborted
    );
        integer padded_length;

        padded_length = (length < 60) ? 60 : length;

        expected_lengths.push_back(
            aborted ? 8 + length : 12 + padded_length
        );

        repeat (7)
            expected_wire.push_back(8'h55);

        expected_wire.push_back(8'hD5);

        for (int i = 0; i < length; i = i + 1) begin
            expected_wire.push_back(frame_byte(i, seed));
            expected_payload.push_back(frame_byte(i, seed));
            expected_last.push_back(!aborted && i == length - 1);
        end

        if (!aborted) begin
            for (int i = length; i < padded_length; i = i + 1)
                expected_wire.push_back(8'h00);

            for (int i = 0; i < 4; i = i + 1)
                expected_wire.push_back(crc[i * 8 +: 8]);
        end
    endtask

    // sample before nonblocking updates, as the receiver does at the edge
    always @(posedge clk or posedge reset) begin
        if (reset) begin
            expected_wire.delete();
            expected_payload.delete();
            expected_last.delete();
            expected_lengths.delete();

            previous_en = 0;
            previous_done = 0;
            previous_error = 0;
            previous_overflow = 0;

            stalled = 0;
            waiting_result = 0;
            seen_end = 0;

            pair_index = 0;
            remaining_bytes = 0;
            idle_cycles = 0;
            input_length = 0;
            committed = 0;

            starts = 0;
            ends = 0;
            completions = 0;
            errors = 0;
            overflows = 0;
            accepted = 0;
            forwarded = 0;
            input_stalls = 0;

            received_byte = 0;
            held_data = 0;
            held_last = 0;
        end else begin
            if ($isunknown({
                ready_out, buffer_busy, overflow_out,
                buffered_valid, buffered_last, tx_ready,
                tx_busy, tx_done, tx_underflow, rmii_tx_en
            }))
                $fatal(1, "unknown control output");

            if (valid_in && !ready_out)
                input_stalls = input_stalls + 1;

            if (valid_in && ready_out) begin
                accepted = accepted + 1;
                input_length = input_length + 1;

                if (last_in) begin
                    if (input_length <= MAX_FRAME_BYTES)
                        committed = committed + 1;

                    input_length = 0;
                end
            end

            // reset and underflow are allowed to cancel a stalled byte
            if (stalled && !tx_underflow) begin
                if (!buffered_valid ||
                    buffered_data !== held_data ||
                    buffered_last !== held_last)
                    $fatal(1, "buffer output changed while stalled");
            end

            stalled = buffered_valid &&
                      !buffered_ready &&
                      !tx_underflow;

            held_data = buffered_data;
            held_last = buffered_last;

            if (waiting_result && !tx_done && !tx_underflow) begin
                if (ready_out || !buffer_busy || buffered_valid)
                    $fatal(1, "buffer released before transmission result");
            end

            if (tx_done || tx_underflow)
                waiting_result = 0;

            if (buffered_valid && buffered_ready) begin
                if (!buffer_busy || ready_out)
                    $fatal(1,
                        "incorrect buffer status during output transfer");

                if (expected_payload.size() == 0 ||
                    expected_last.size() == 0)
                    $fatal(1, "unexpected buffered byte");

                expected_byte = expected_payload.pop_front();
                expected_last_bit = expected_last.pop_front();

                if (buffered_data !== expected_byte ||
                    buffered_last !== expected_last_bit)
                    $fatal(1, "buffer data or last flag mismatch");

                forwarded = forwarded + 1;

                if (buffered_last)
                    waiting_result = 1;
            end

            if (rmii_tx_en) begin
                if (!tx_busy)
                    $fatal(1, "transmitter not busy during frame");

                if (!previous_en) begin
                    if (seen_end && idle_cycles < 48)
                        $fatal(1, "interframe gap too short");

                    if (expected_lengths.size() == 0)
                        $fatal(1, "unexpected frame on rmii");

                    starts = starts + 1;

                    if (starts > committed)
                        $fatal(1,
                            "transmission began before frame was complete");

                    remaining_bytes = expected_lengths.pop_front();
                end

                idle_cycles = 0;

                if (remaining_bytes == 0)
                    $fatal(1, "extra dibit after expected frame end");

                received_byte[pair_index * 2 +: 2] = rmii_txd;

                if (pair_index == 3) begin
                    if (expected_wire.size() == 0)
                        $fatal(1, "unexpected rmii byte");

                    expected_byte = expected_wire.pop_front();

                    if (received_byte !== expected_byte)
                        $fatal(1,
                            "rmii mismatch: expected %02h, received %02h",
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
                        $fatal(1,
                            "frame ended at an unexpected position");

                    ends = ends + 1;
                    seen_end = 1;
                end
            end

            if (tx_done) begin
                if (previous_done || rmii_tx_en ||
                    tx_underflow || !tx_busy)
                    $fatal(1, "incorrect done pulse");

                completions = completions + 1;
            end

            if (tx_underflow) begin
                if (previous_error || rmii_tx_en || buffered_valid)
                    $fatal(1, "incorrect underflow handling");

                errors = errors + 1;
            end

            if (overflow_out) begin
                if (previous_overflow)
                    $fatal(1, "overflow lasted longer than one clock");

                overflows = overflows + 1;
            end

            previous_en = rmii_tx_en;
            previous_done = tx_done;
            previous_error = tx_underflow;
            previous_overflow = overflow_out;
        end
    end

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
        input integer seed,
        input logic gaps
    );
        for (int i = 0; i < length; i = i + 1) begin
            send_byte(frame_byte(i, seed), i == length - 1);

            if (gaps && i < length - 1) begin
                stop_source();
                repeat (1 + i % 13)
                    @(negedge clk);
            end
        end

        stop_source();
    endtask

    task automatic wait_for_idle;
        integer cycles;

        cycles = 0;
        @(negedge clk);

        while (buffer_busy || tx_busy || rmii_tx_en) begin
            if (cycles >= 10000)
                $fatal(1, "idle timeout");

            cycles = cycles + 1;
            @(negedge clk);
        end

        repeat (2)
            @(negedge clk);

        if (expected_wire.size() ||
            expected_lengths.size() ||
            expected_payload.size() ||
            expected_last.size())
            $fatal(1, "expected data remains unsent");

        if (!ready_out || buffered_valid ||
            tx_done || tx_underflow || overflow_out)
            $fatal(1, "incorrect idle outputs");
    endtask

    task automatic apply_reset;
        @(negedge clk);
        #3;

        reset = 1;
        valid_in = 0;
        last_in = 0;
        starve_tx = 0;

        #1;
        if ({
            ready_out, buffered_valid, buffered_last, buffer_busy,
            overflow_out, tx_busy, tx_done, tx_underflow, rmii_tx_en
        } !== 9'b0)
            $fatal(1, "asynchronous reset outputs incorrect");

        repeat (3)
            @(negedge clk);

        reset = 0;

        repeat (2)
            @(negedge clk);
    endtask

    task automatic mark_counts;
        starts_before = starts;
        ends_before = ends;
        completions_before = completions;
        errors_before = errors;
        overflows_before = overflows;
        accepted_before = accepted;
        forwarded_before = forwarded;
        stalls_before = input_stalls;
    endtask

    task automatic check_counts(
        input integer frames,
        input integer done_count,
        input integer error_count,
        input integer overflow_count,
        input integer input_bytes,
        input integer output_bytes
    );
        if (starts - starts_before != frames ||
            ends - ends_before != frames ||
            completions - completions_before != done_count ||
            errors - errors_before != error_count ||
            overflows - overflows_before != overflow_count ||
            accepted - accepted_before != input_bytes ||
            forwarded - forwarded_before != output_bytes)
            $fatal(1, "incorrect frame, status, or byte count");
    endtask

    task automatic run_good(
        input integer length,
        input integer seed,
        input logic [31:0] crc,
        input logic gaps
    );
        mark_counts();
        expect_frame(length, seed, crc, 0);
        send_frame(length, seed, gaps);
        wait_for_idle();

        check_counts(1, 1, 0, 0, length, length);

        $display("pass: length=%0d, producer gaps=%0d", length, gaps);
    endtask

    task automatic run_oversized(input integer length);
        mark_counts();

        send_frame(length, 9, 0);
        wait_for_idle();

        check_counts(0, 0, 0, 1, length, 0);

        $display("pass: oversized frame discarded, length=%0d", length);
    endtask

    initial begin
        apply_reset();
        mark_counts();

        repeat (10)
            @(negedge clk);

        wait_for_idle();
        check_counts(0, 0, 0, 0, 0, 0);
        $display("pass: reset and idle");

        run_good(14, 0, 32'h87F71B35, 0);
        run_good(59, 19, 32'h3847F297, 0);
        run_good(60, 37, 32'h386A0B64, 0);
        run_good(61, 53, 32'h8CE3334F, 1);
        run_good(1514, 71, 32'h861AB214, 0);

        // overflow on the last byte, followed by recovery
        run_oversized(1515);
        run_good(14, 0, 32'h87F71B35, 0);

        // discard enough bytes to exceed even the physical memory depth
        run_oversized(2100);
        run_good(60, 37, 32'h386A0B64, 0);

        // offer another frame while the buffer is still occupied
        mark_counts();

        expect_frame(60, 37, 32'h386A0B64, 0);
        expect_frame(14, 0, 32'h87F71B35, 0);

        send_frame(60, 37, 0);
        send_frame(14, 0, 0);

        wait_for_idle();
        check_counts(2, 2, 0, 0, 74, 74);

        if (input_stalls == stalls_before)
            $fatal(1,
                "next-frame test did not exercise input backpressure");

        $display("pass: next frame waits for buffer release");

        // create a real transmitter underflow after three accepted bytes
        mark_counts();

        expect_frame(3, 37, 0, 1);
        send_frame(60, 37, 0);

        while (forwarded - forwarded_before < 3)
            @(negedge clk);

        starve_tx = 1;

        wait (tx_underflow);
        wait_for_idle();

        check_counts(1, 0, 1, 0, 60, 3);
        starve_tx = 0;

        $display("pass: underflow abort and buffer release");
        run_good(61, 53, 32'h8CE3334F, 0);

        // reset an incomplete input frame
        for (int i = 0; i < 10; i = i + 1)
            send_byte(frame_byte(i, 0), 0);

        stop_source();

        if (!buffer_busy || rmii_tx_en)
            $fatal(1, "collection reset test in wrong phase");

        apply_reset();
        wait_for_idle();

        $display("pass: reset during collection");
        run_good(14, 0, 32'h87F71B35, 0);

        // reset both modules during active transmission
        mark_counts();

        expect_frame(60, 37, 32'h386A0B64, 0);
        send_frame(60, 37, 0);

        while (forwarded - forwarded_before < 3)
            @(negedge clk);

        if (!rmii_tx_en)
            $fatal(1, "transmission reset test in wrong phase");

        apply_reset();
        wait_for_idle();

        $display("pass: reset during transmission");
        run_good(60, 37, 32'h386A0B64, 0);

        $display("all ethernet_TX_buffer tests passed");
        $finish;
    end

    initial begin
        #2000000;
        $fatal(1, "global simulation timeout");
    end

endmodule