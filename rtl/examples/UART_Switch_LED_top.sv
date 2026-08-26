module UART_switch_LED_top #(
    parameter integer CLOCK_HZ = 100_000_000
) (
    input  logic       CLK100MHZ,
    input  logic       BTNC,
    input  logic       BTNU,
    input  logic       UART_RX,
    input  logic [7:0] SW,

    output logic       UART_TX,
    output logic [7:0] LED
);

    logic [7:0] rx_data;
    logic       rx_valid;
    logic       rx_error;

    logic [7:0] tx_data_reg, tx_data_next;
    logic       tx_send_reg, tx_send_next;
    logic       tx_busy;

    logic [7:0] LED_reg, LED_next;

    logic BTNU_debounced;
    logic BTNU_prev_reg, BTNU_prev_next;


    button_debouncer button_debouncer_unit (
        .clk        (CLK100MHZ),
        .reset      (BTNC),
        .button_in  (BTNU),
        .button_out (BTNU_debounced)
    );


    UART_RX #(
        .CLOCK_HZ  (CLOCK_HZ),
        .BAUD_RATE (115_200)
    ) UART_RX_unit (
        .clk           (CLK100MHZ),
        .reset         (BTNC),
        .rx            (UART_RX),
        .data          (rx_data),
        .data_valid    (rx_valid),
        .framing_error (rx_error)
    );


    UART_TX #(
        .CLOCK_HZ  (CLOCK_HZ),
        .BAUD_RATE (115_200)
    ) UART_TX_unit (
        .clk   (CLK100MHZ),
        .reset (BTNC),
        .tx    (UART_TX),
        .data  (tx_data_reg),
        .send  (tx_send_reg),
        .busy  (tx_busy)
    );


    always_comb begin
        tx_data_next  = tx_data_reg;
        tx_send_next  = 1'b0;
        LED_next      = LED_reg;
        BTNU_prev_next = BTNU_debounced;

        // Send once on each button press
        if (BTNU_debounced &&
            !BTNU_prev_reg &&
            !tx_busy) begin

            tx_data_next = SW;
            tx_send_next = 1'b1;
        end

        if (rx_valid)
            LED_next = rx_data;
    end


    always_ff @(posedge CLK100MHZ or posedge BTNC) begin
        if (BTNC) begin
            tx_data_reg  <= '0;
            tx_send_reg  <= 1'b0;
            LED_reg      <= '0;
            BTNU_prev_reg <= 1'b0;
        end
        else begin
            tx_data_reg  <= tx_data_next;
            tx_send_reg  <= tx_send_next;
            LED_reg      <= LED_next;
            BTNU_prev_reg <= BTNU_prev_next;
        end
    end


    assign LED = LED_reg;

endmodule