module UART_RX #(
    parameter integer CLOCK_HZ  = 100_000_000,
    parameter integer BAUD_RATE = 115_200
) (
    input  logic       clk,
    input  logic       reset,
    input  logic       rx,

    output logic [7:0] data,
    output logic       data_valid,
    output logic       framing_error
);

    localparam integer CLKS_PER_BIT =
        (CLOCK_HZ + BAUD_RATE / 2) / BAUD_RATE;

    localparam integer HALF_CLKS   = CLKS_PER_BIT / 2;
    localparam integer COUNT_WIDTH = $clog2(CLKS_PER_BIT);


    typedef enum logic [1:0] {
        IDLE,
        START,
        DATA,
        STOP
    } state_t;


    state_t state_reg, state_next;

    logic [COUNT_WIDTH-1:0] clk_count_reg, clk_count_next;
    logic [2:0]             bit_count_reg, bit_count_next;
    logic [7:0]             shift_reg, shift_next;
    logic [7:0]             data_reg, data_next;

    logic data_valid_reg, data_valid_next;
    logic framing_error_reg, framing_error_next;

    // UART input synchronizer
    (* ASYNC_REG = "TRUE" *)
    logic rx_meta_reg, rx_meta_next;

    (* ASYNC_REG = "TRUE" *)
    logic rx_sync_reg, rx_sync_next;


    // Next-state logic
    always_comb begin
        state_next         = state_reg;
        clk_count_next     = clk_count_reg;
        bit_count_next     = bit_count_reg;
        shift_next         = shift_reg;
        data_next          = data_reg;
        data_valid_next    = 1'b0;
        framing_error_next = 1'b0;

        rx_meta_next = rx;
        rx_sync_next = rx_meta_reg;


        case (state_reg)

            IDLE: begin
                clk_count_next = '0;
                bit_count_next = '0;

                if (!rx_sync_reg)
                    state_next = START;
            end


            START: begin
                // Sample the middle of the start bit
                if (clk_count_reg == HALF_CLKS - 1) begin
                    clk_count_next = '0;

                    if (!rx_sync_reg)
                        state_next = DATA;
                    else
                        state_next = IDLE;
                end
                else begin
                    clk_count_next = clk_count_reg + 1'b1;
                end
            end


            DATA: begin
                if (clk_count_reg == CLKS_PER_BIT - 1) begin
                    clk_count_next = '0;

                    // UART sends LSB first
                    shift_next[bit_count_reg] = rx_sync_reg;

                    if (bit_count_reg == 3'd7) begin
                        bit_count_next = '0;
                        state_next     = STOP;
                    end
                    else begin
                        bit_count_next = bit_count_reg + 1'b1;
                    end
                end
                else begin
                    clk_count_next = clk_count_reg + 1'b1;
                end
            end


            STOP: begin
                if (clk_count_reg == CLKS_PER_BIT - 1) begin
                    clk_count_next = '0;
                    state_next     = IDLE;

                    if (rx_sync_reg) begin
                        data_next       = shift_reg;
                        data_valid_next = 1'b1;
                    end
                    else begin
                        framing_error_next = 1'b1;
                    end
                end
                else begin
                    clk_count_next = clk_count_reg + 1'b1;
                end
            end


            default: begin
                state_next     = IDLE;
                clk_count_next = '0;
                bit_count_next = '0;
            end

        endcase
    end


    // Register update
    always_ff @(posedge clk) begin
        if (reset) begin
            state_reg         <= IDLE;
            clk_count_reg     <= '0;
            bit_count_reg     <= '0;
            shift_reg         <= '0;
            data_reg          <= '0;
            data_valid_reg    <= 1'b0;
            framing_error_reg <= 1'b0;
            rx_meta_reg       <= 1'b1;
            rx_sync_reg       <= 1'b1;
        end
        else begin
            state_reg         <= state_next;
            clk_count_reg     <= clk_count_next;
            bit_count_reg     <= bit_count_next;
            shift_reg         <= shift_next;
            data_reg          <= data_next;
            data_valid_reg    <= data_valid_next;
            framing_error_reg <= framing_error_next;
            rx_meta_reg       <= rx_meta_next;
            rx_sync_reg       <= rx_sync_next;
        end
    end


    assign data          = data_reg;
    assign data_valid    = data_valid_reg;
    assign framing_error = framing_error_reg;

endmodule