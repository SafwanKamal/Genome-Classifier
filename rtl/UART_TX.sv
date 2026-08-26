module UART_TX #(
    parameter integer CLOCK_HZ  = 100_000_000,
    parameter integer BAUD_RATE = 115_200
) (
    input  logic       clk,
    input  logic       reset,
    output  logic      tx,

    input logic [7:0] data,
    input logic       send,
    output logic      busy
);

    localparam integer BIT_COUNT = 8;
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
    logic                   busy_reg, busy_next;
    logic [7:0]             data_reg, data_next;

    logic tx_reg, tx_next;


    always_ff @(posedge clk or posedge reset) begin
        if (reset) begin
            state_reg     <= IDLE;
            clk_count_reg <= 0;
            bit_count_reg <= 0;
            tx_reg        <= 1'b1;
            busy_reg      <= 0;
            data_reg      <= '0;
        end else begin
            state_reg     <= state_next;
            clk_count_reg <= clk_count_next;
            bit_count_reg <= bit_count_next;
            data_reg      <= data_next;
            tx_reg        <= tx_next;
            busy_reg      <= busy_next;
        end
    end

    always_comb begin
        clk_count_next = clk_count_reg;
        bit_count_next = bit_count_reg;
        tx_next = tx_reg;
        state_next = state_reg;
        data_next = data_reg;
        busy_next = busy_reg;

        case (state_reg)
            IDLE: begin
                clk_count_next = '0;
                bit_count_next = '0;
                tx_next = 1'b1;
                busy_next = 1'b0;
                if (send) begin
                    state_next = START;
                    busy_next = 1'b1;
                    data_next = data;
                end
            end

            START: begin
                tx_next = 1'b0;

                if (clk_count_reg < CLKS_PER_BIT - 1) begin
                    clk_count_next = clk_count_reg + 1;
                end else begin
                    clk_count_next = '0;
                    state_next     = DATA;
                end
            end

            DATA: begin
                tx_next = data_reg[bit_count_reg];

                if(clk_count_reg < CLKS_PER_BIT - 1) begin
                    clk_count_next = clk_count_reg + 1;
                end else begin
                    clk_count_next = '0;
                    if (bit_count_reg < BIT_COUNT - 1) begin
                        bit_count_next = bit_count_reg + 1;
                    end else begin
                        bit_count_next = '0;
                        state_next = STOP;
                    end
                end
            end

            STOP: begin
                tx_next = 1'b1;

                if (clk_count_reg < CLKS_PER_BIT - 1) begin
                    clk_count_next = clk_count_reg + 1;
                end else begin
                    clk_count_next = '0;
                    busy_next = 1'b0;
                    tx_next = 1'b1;
                    state_next = IDLE;
                end
            end
        endcase
    end

    assign busy = busy_reg;
    assign tx   = tx_reg;

endmodule