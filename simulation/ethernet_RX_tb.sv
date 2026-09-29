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

    ethernet_RX dut (.*);
    always #10 clk = ~clk;

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
        input logic corrupt_fcs, phy_error, bad_preamble, toggle_dv
    );
        logic [31:0] crc;
        logic [7:0] byte_value;
        crc = 32'hFFFFFFFF;
        send_byte(bad_preamble ? 8'h54 : 8'h55, 0);
        repeat (6) send_byte(8'h55, 0);
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

        $display("all ethernet_RX tests passed");
        $finish;
    end

    initial begin
        #1000000;
        $fatal(1, "ethernet_RX timeout");
    end
endmodule
