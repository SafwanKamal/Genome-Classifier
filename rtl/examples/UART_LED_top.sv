module UART_LED_top (
    input  logic       CLK100MHZ,
    input  logic       BTNC,
    input  logic       UART_RX,
    output logic [7:0] LED
);

    logic [7:0] rx_data;
    logic       rx_valid;
    logic       rx_error;

    logic [7:0] led_reg, led_next;


    UART_RX #(
        .CLOCK_HZ  (100_000_000),
        .BAUD_RATE (115_200)
    ) uart_rx_unit (
        .clk           (CLK100MHZ),
        .reset         (BTNC),
        .rx            (UART_RX),
        .data          (rx_data),
        .data_valid    (rx_valid),
        .framing_error (rx_error)
    );


    // Hold the most recently received byte
    always_comb begin
        led_next = led_reg;

        if (rx_valid)
            led_next = rx_data;
    end


    // LED register
    always_ff @(posedge CLK100MHZ) begin
        if (BTNC)
            led_reg <= 8'h00;
        else
            led_reg <= led_next;
    end


    assign LED = led_reg;

endmodule