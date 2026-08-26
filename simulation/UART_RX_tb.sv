`timescale 1ns / 1ps

module UART_RX_tb;

    localparam integer CLOCK_HZ     = 1_000_000;
    localparam integer BAUD_RATE    = 100_000;
    localparam integer CLKS_PER_BIT = CLOCK_HZ / BAUD_RATE;

    logic clk;
    logic reset;
    logic rx;

    logic [7:0] data;
    logic       data_valid;
    logic       framing_error;


    UART_RX #(
        .CLOCK_HZ  (CLOCK_HZ),
        .BAUD_RATE (BAUD_RATE)
    ) DUT (
        .clk           (clk),
        .reset         (reset),
        .rx            (rx),
        .data          (data),
        .data_valid    (data_valid),
        .framing_error (framing_error)
    );


    always begin
        #5 clk = ~clk;
    end


    task automatic send_UART_byte(
        input logic [7:0] value
    );
        integer i;

        begin
            // Start bit
            rx = 1'b0;
            repeat (CLKS_PER_BIT)
                @(posedge clk);

            // Data bits
            for (i = 0; i < 8; i = i + 1) begin
                rx = value[i];

                repeat (CLKS_PER_BIT)
                    @(posedge clk);
            end

            // Stop bit
            rx = 1'b1;

            repeat (CLKS_PER_BIT)
                @(posedge clk);
        end
    endtask


    task automatic send_and_check(
        input logic [7:0] expected
    );
        fork
            send_UART_byte(expected);

            begin
                @(posedge data_valid);
                #1;

                if (data !== expected) begin
                    $display(
                        "FAIL: expected %h, received %h",
                        expected,
                        data
                    );

                    $fatal;
                end
                else begin
                    $display(
                        "PASS: received %h",
                        data
                    );
                end
            end
        join

        repeat (2)
            @(posedge clk);
    endtask


    initial begin
        clk   = 1'b0;
        reset = 1'b1;
        rx    = 1'b1;

        $dumpfile("UART_RX_tb.vcd");
        $dumpvars(0, UART_RX_tb);

        repeat (5)
            @(posedge clk);

        reset = 1'b0;

        repeat (5)
            @(posedge clk);

        send_and_check(8'h00);
        send_and_check(8'h01);
        send_and_check(8'h55);
        send_and_check(8'hA5);
        send_and_check(8'hFF);

        $display("All UART_RX tests passed.");

        repeat (5)
            @(posedge clk);

        $finish;
    end

endmodule