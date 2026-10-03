`timescale 1ns / 1ps

module ethernet_RX_tb;
    logic clk = 0, reset = 1;
    logic [1:0] rmii_rxd = 0;
    logic rmii_crs_dv = 0, rmii_rx_er = 0;
    logic [7:0] data_out;
    logic valid_out, ready_in = 1, last_out;
    logic good_frame_out, bad_frame_out, overflow_out;
    integer good_count = 0, bad_count = 0, overflow_count = 0;
    integer output_count = 0, output_frames = 0, seed_expected = 0;
    integer length_expected = 0;
    logic check_output = 0;
    logic alternate_stalls = 0, was_stalled = 0;
    logic [7:0] stalled_data;
    logic stalled_last;

    ethernet_RX dut (.*);
    always #10 clk = ~clk;
    always @(negedge clk) if (alternate_stalls) ready_in = !ready_in;
    always @(posedge clk) begin
        if (reset) was_stalled <= 0;
        else begin
            if (was_stalled && valid_out &&
                (data_out !== stalled_data || last_out !== stalled_last))
                $fatal(1, "output changed under backpressure");
            was_stalled <= valid_out && !ready_in;
            stalled_data <= data_out;
            stalled_last <= last_out;
        end
    end

    function automatic logic [7:0] payload(input integer index, seed);
        if (index < 6) payload = 8'hFF;
        else if (index == 6) payload = 8'h02;
        else if (index < 12) payload = 8'h00;
        else if (index == 12) payload = 8'h88;
        else if (index == 13) payload = 8'hB5;
        else payload = (index * 73 + seed) & 255;
    endfunction

    function automatic logic [31:0] crc_byte(
        input logic [31:0] prior, input logic [7:0] value
    );
        logic [31:0] work;
        work = prior ^ value;
        repeat (8)
            work = (work >> 1) ^ ((work[0]) ? 32'hEDB88320 : 0);
        return work;
    endfunction

    task automatic dibit(input logic [1:0] value, input logic dv, input logic er);
        @(negedge clk);
        rmii_rxd = value;
        rmii_crs_dv = dv;
        rmii_rx_er = er;
    endtask

    task automatic send_byte(input logic [7:0] value, input logic er);
        for (integer pair = 0; pair < 4; pair = pair + 1)
            dibit(value[pair*2 +: 2], 1, er && pair == 2);
    endtask

    // With the Ethernet minimum, length is at least 60 before FCS.
    task automatic send_frame(
        input integer length, seed,
        input logic corrupt_fcs, phy_error, bad_preamble, toggle_dv,
        input integer leading_zeros = 0, preamble_bytes = 7,
        input logic carrier_error = 0
    );
        logic [31:0] crc;
        logic [7:0] byte_value;
        crc = 32'hFFFFFFFF;
        for (integer i = 0; i < leading_zeros; i = i + 1)
            dibit(0, 1, carrier_error && i == 0);
        send_byte(bad_preamble ? 8'h54 : 8'h55, 0);
        repeat (preamble_bytes - 1) send_byte(8'h55, 0);
        send_byte(8'hD5, 0);

        for (integer i = 0; i < length; i = i + 1) begin
            byte_value = payload(i, seed);
            crc = crc_byte(crc, byte_value);
            if (toggle_dv && i == 20) begin
                dibit(byte_value[1:0], 1, 0);
                dibit(byte_value[3:2], 0, 0);
                dibit(byte_value[5:4], 1, 0);
                dibit(byte_value[7:6], 1, 0);
            end else begin
                send_byte(byte_value, phy_error && i == 20);
            end
        end

        crc = ~crc;
        if (corrupt_fcs) crc[0] = ~crc[0];
        for (integer i = 0; i < 4; i = i + 1)
            send_byte(crc[i*8 +: 8], 0);

        dibit(0, 0, 0);
        dibit(0, 0, 0);
        repeat (48) dibit(0, 0, 0);
    endtask

    always @(posedge clk) if (!reset) begin
        if (good_frame_out) good_count = good_count + 1;
        if (bad_frame_out) bad_count = bad_count + 1;
        if (overflow_out) overflow_count = overflow_count + 1;
        if (valid_out && ready_in) begin
            if (!check_output) $fatal(1, "unexpected output");
            if (data_out !== payload(output_count, seed_expected))
                $fatal(1, "output byte %0d: got %02h", output_count, data_out);
            if (last_out !== (output_count == length_expected - 1))
                $fatal(1, "last_out at byte %0d", output_count);
            output_count = output_count + 1;
            if (last_out) begin
                output_frames = output_frames + 1;
                check_output = 0;
            end
        end
    end

    initial begin
        repeat (4) @(negedge clk);
        reset = 0;
        length_expected = 60;
        seed_expected = 19;
        check_output = 1;
        send_frame(60, 19, 0, 0, 0, 0);
        wait (output_frames == 1);
        if (good_count != 1 || bad_count != 0 || output_count != 60)
            $fatal(1, "valid minimum frame failed");

        send_frame(60, 19, 1, 0, 0, 0);
        send_frame(59, 19, 0, 0, 0, 0);
        send_frame(60, 19, 0, 1, 0, 0);
        send_frame(60, 19, 0, 0, 1, 0);
        if (good_count != 1 || bad_count != 3 || output_count != 60)
            $fatal(1, "invalid frame accepted or missed");

        output_count = 0;
        length_expected = 1514;
        seed_expected = 37;
        check_output = 1;
        send_frame(1514, 37, 0, 0, 0, 0);
        wait (output_frames == 2);
        if (good_count != 2 || output_count != 1514)
            $fatal(1, "maximum frame failed");

        output_count = 0;
        length_expected = 60;
        seed_expected = 42;
        check_output = 1;
        send_frame(60, 42, 0, 0, 0, 1);
        wait (output_frames == 3);
        if (good_count != 3 || output_count != 60)
            $fatal(1, "CRS_DV toggle failed");

        // Keep one checked frame in the output RAM while another arrives.
        output_count = 0;
        seed_expected = 51;
        ready_in = 0;
        check_output = 1;
        send_frame(60, 51, 0, 0, 0, 0);
        if (good_count != 4 || !valid_out)
            $fatal(1, "backpressure failed");
        send_frame(60, 99, 0, 0, 0, 0);
        if (overflow_count != 1 || good_count != 4)
            $fatal(1, "overflow failed");
        ready_in = 1;
        wait (output_frames == 4);
        if (output_count != 60)
            $fatal(1, "held output corrupted");

        // Reproduce the board's six leading 00 dibits, then exercise every
        // dibit alignment and several shortened whole-byte preambles.
        for (integer lead = 0; lead <= 9; lead = lead + 1) begin
            output_count = 0;
            seed_expected = 60 + lead;
            check_output = 1;
            send_frame(60, seed_expected, 0, 0, 0, 0, lead, (lead % 3) + 1);
            wait (output_frames == 5 + lead);
            if (good_count != 5 + lead || output_count != 60)
                $fatal(1, "leading-zero frame failed: %0d", lead);
        end
        // An error during carrier acquisition must not disappear when data starts.
        send_frame(60, 90, 0, 0, 0, 0, 6, 7, 1);
        if (good_count != 14 || bad_count != 3)
            $fatal(1, "carrier error was ignored");
        // False carrier without preamble must reset cleanly for the next frame.
        repeat (6) dibit(0, 1, 0);
        repeat (4) dibit(0, 0, 0);
        output_count = 0;
        seed_expected = 92;
        check_output = 1;
        send_frame(60, 92, 0, 0, 0, 0, 6);
        wait (output_frames == 15);
        if (good_count != 15 || output_count != 60)
            $fatal(1, "false-carrier recovery failed");
        output_count = 0;
        seed_expected = 100;
        check_output = 1;
        alternate_stalls = 1;
        send_frame(60, 100, 0, 0, 0, 0, 6);
        wait (output_frames == 16);
        @(negedge clk);
        alternate_stalls = 0;
        ready_in = 1;
        if (good_count != 16 || output_count != 60)
            $fatal(1, "intermittent backpressure failed");
        send_frame(1515, 101, 0, 0, 0, 0, 6);
        if (good_count != 16 || bad_count != 4)
            $fatal(1, "oversized frame accepted");
        // RAM is not reset; control must invalidate the held frame on reset.
        ready_in = 0;
        send_frame(60, 102, 0, 0, 0, 0, 6);
        if (!valid_out || good_count != 17) $fatal(1, "reset test setup failed");
        reset = 1;
        repeat (4) @(negedge clk);
        reset = 0;
        ready_in = 1;
        output_count = 0;
        seed_expected = 103;
        check_output = 1;
        send_frame(60, 103, 0, 0, 0, 0, 6);
        wait (output_frames == 17);
        if (good_count != 18 || output_count != 60)
            $fatal(1, "reset recovery failed");
        $display("all ethernet_RX tests passed (leading zeros, synchronous RAM, stalls, reset)");
        $finish;
    end

    initial begin
        #1000000;
        $fatal(1, "ethernet_RX timeout");
    end
endmodule
