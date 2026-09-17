`timescale 1ns / 1ps

module ethernet_crc32_tb;

    logic clk = 1'b0;
    logic reset = 1'b0;
    logic clear_in = 1'b0;
    logic [7:0] data_in = 8'h00;
    logic enable_in = 1'b0;
    logic [31:0] crc_out;

    // independent reference values from python zlib.crc32
    logic [31:0] digit_crc [0:8] = '{
        32'h83DCEFB7, 32'h4F5344CD, 32'h884863D2,
        32'h9BE3E0A3, 32'hCBF53A1C, 32'h0972D361,
        32'h5003699F, 32'h9AE0DAAF, 32'hCBF43926
    };

    ethernet_CRC32 dut (
        .clk(clk),
        .reset(reset),
        .clear_in(clear_in),
        .data_in(data_in),
        .enable_in(enable_in),
        .crc_out(crc_out)
    );

    // 50 mhz clock
    always #10 clk = ~clk;

    // drive before the rising edge and sample after register updates
    task automatic step(
        input logic clear_value,
        input logic enable_value,
        input logic [7:0] byte_value
    );
        @(negedge clk);
        clear_in = clear_value;
        enable_in = enable_value;
        data_in = byte_value;
        @(posedge clk);
        #1;
    endtask

    task automatic check_crc(
        input logic [31:0] expected,
        input string test_name
    );
        if (crc_out !== expected)
            $fatal(1, "%s: expected %08h, received %08h",
                test_name, expected, crc_out);
    endtask

    task automatic run_digits(input logic insert_gaps);
        step(1'b1, 1'b0, 8'h00);
        check_crc(32'h00000000, "clear before digits");

        for (int i = 0; i < 9; i = i + 1) begin
            step(1'b0, 1'b1, 8'h31 + i);
            check_crc(digit_crc[i], $sformatf("digit prefix %0d", i + 1));

            if (insert_gaps) begin
                repeat (3) begin
                    step(1'b0, 1'b0, 8'hA5 ^ i);
                    check_crc(digit_crc[i], "hold between bytes");
                end
            end
        end

        $display("pass: 123456789, gaps=%0d", insert_gaps);
    endtask

    task automatic run_pattern(
        input integer length,
        input integer seed,
        input logic [31:0] expected
    );
        step(1'b1, 1'b0, 8'h00);
        check_crc(32'h00000000, "clear before pattern");

        // the byte input truncates this expression to its low eight bits
        for (int i = 0; i < length; i = i + 1)
            step(1'b0, 1'b1, i * 73 + seed);

        check_crc(expected, $sformatf("pattern length %0d", length));
        step(1'b0, 1'b0, 8'hFF);
        check_crc(expected, "hold after pattern");
        $display("pass: pattern length %0d", length);
    endtask

    initial begin
        // assert reset between clock edges
        #3;
        reset = 1'b1;
        #1;
        check_crc(32'h00000000, "initial asynchronous reset");
        @(negedge clk);
        reset = 1'b0;

        repeat (3) begin
            step(1'b0, 1'b0, 8'hFF);
            check_crc(32'h00000000, "idle after reset");
        end
        $display("pass: reset and idle");

        run_digits(1'b0);
        run_digits(1'b1);

        // clear must discard both the old state and this enabled byte
        step(1'b1, 1'b1, 8'hFF);
        check_crc(32'h00000000, "clear priority over enable");
        step(1'b0, 1'b1, 8'h31);
        check_crc(digit_crc[0], "first byte after clear");
        $display("pass: clear priority and restart");

        // reset a nonzero running crc without waiting for a rising edge
        @(negedge clk);
        #3;
        reset = 1'b1;
        #1;
        check_crc(32'h00000000, "asynchronous reset during calculation");
        enable_in = 1'b0;
        clear_in = 1'b0;
        @(negedge clk);
        reset = 1'b0;
        step(1'b0, 1'b1, 8'h31);
        check_crc(digit_crc[0], "first byte after reset");
        $display("pass: asynchronous reset and recovery");

        run_pattern(1, 0, 32'hD202EF8D);
        run_pattern(60, 19, 32'h4C066E4B);
        run_pattern(256, 0, 32'h3BD05201);
        run_pattern(1514, 37, 32'hEA1E3CD2);

        $display("all ethernet_crc32 tests passed");
        $finish;
    end

    initial begin
        #100000;
        $fatal(1, "global simulation timeout");
    end

endmodule