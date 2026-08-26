module UART_response_TX #(
    parameter integer CLOCK_HZ  = 100_000_000,
    parameter integer BAUD_RATE = 115_200,
    parameter integer BYTES_NUMBER = 4,
    parameter integer DATA_NUMBER = 4
) (
    input  logic       clk,
    input  logic       reset,
    output logic       tx,

    input  logic signed [31:0] data32 [0:DATA_NUMBER - 1],
    input  logic       send,
    output logic       busy
);

    typedef enum logic [1:0] {
        IDLE,
        START,
        DATA,
        STOP
    } state_t;

    state_t     state_reg, state_next; 
    logic [7:0] data_reg, data_next;
    logic [1:0] byte_count_reg, byte_count_next;
    logic       send_reg, send_next;
    logic       busy_reg, busy_next; 
    logic signed [31:0] data32_reg [0:DATA_NUMBER - 1];
    logic signed [31:0] data32_next [0:DATA_NUMBER - 1];

    localparam  integer DATA_INDEX_WIDTH = (DATA_NUMBER > 1) ? $clog2(DATA_NUMBER) : 1;
    logic [DATA_INDEX_WIDTH - 1:0] data_index_reg, data_index_next; 
    // I think _reg, _next is not necessary because TX_busy is not an input. 
    logic       TX_busy;

    UART_TX #(
        .CLOCK_HZ(CLOCK_HZ),
        .BAUD_RATE(BAUD_RATE)
    ) uart_tx_inst (
        .clk(clk),
        .reset(reset),
        .tx(tx),
        .data(data_reg),
        .send(send_reg),
        .busy(TX_busy)
    );


    always_ff @(posedge clk or posedge reset) begin
        if (reset) begin
            data_reg <= '0;
            byte_count_reg <= 2'b00;
            send_reg <= 0;
            state_reg <= IDLE;
            busy_reg <= 0;
            for (int i = 0; i < DATA_NUMBER; i++) begin
                data32_reg[i] <= '0;
            end
            data_index_reg <= '0;
        end else begin
            data_reg <= data_next;
            byte_count_reg <= byte_count_next;
            send_reg <= send_next;
            state_reg <= state_next; 
            busy_reg <= busy_next;
            data32_reg <= data32_next;
            data_index_reg <= data_index_next;
        end
    end

    always_comb begin
        state_next = state_reg;
        data_next = data_reg;
        send_next = 0; // send is normally low, is low even when we are sending something. So, we will only have 1 high pulse of send
        byte_count_next = byte_count_reg; 
        busy_next = busy_reg;
        data32_next = data32_reg;
        data_index_next = data_index_reg;

        case (state_reg)
            IDLE: begin
                data_next = '0;
                send_next = 0;
                byte_count_next = '0;
                data_index_next = '0;

                if (send) begin
                    // state_next = START;
                    state_next = DATA;
                    busy_next = 1;
                    data_next = 8'hA5;
                    send_next = 1;
                    data32_next = data32;
                end
            end 

            // START: begin
            //     // If we do not use send_reg in the condition
            //     // then, TX_busy will be 0 twice consecutively,
            //     // which would trigger the conditional block twice,
            //     // which we do not want. 
            //     if (!TX_busy && !send_reg) begin
            //         state_next = DATA;
            //         data_next = data32_reg[31:24];
            //         send_next = 1;
            //     end
            // end

            DATA: begin
                if (!TX_busy && !send_reg) begin
                    // Can be done with a for loop too
                    // integer i;
                    // for (i = 0; i < DATA_NUMBER; i++) begin

                    // end
                    case (byte_count_reg)
                        2'b00: begin
                            data_next = data32_reg[data_index_reg][31:24];
                            send_next = 1;
                            byte_count_next = byte_count_reg + 1;
                        end 
                        2'b01: begin
                            data_next = data32_reg[data_index_reg][23:16];
                            send_next = 1;
                            byte_count_next = byte_count_reg + 1;
                        end
                        2'b10: begin
                            data_next = data32_reg[data_index_reg][15:8];
                            send_next = 1;
                            byte_count_next = byte_count_reg + 1; 
                        end
                        2'b11: begin
                            data_next = data32_reg[data_index_reg][7:0];
                            byte_count_next = '0;
                            send_next = 1;
                            // This will not work actually
                            // if (i == 3) begin
                            //     state_next = IDLE;
                            // end
                            if (data_index_reg == DATA_NUMBER - 1) begin
                                data_index_next = '0;
                                state_next = STOP;
                            end else begin
                                data_index_next = data_index_reg + 1;
                            end
                        end

                        default: begin
                        end
                    endcase
                end
            end

            STOP: begin
                // We will do additional check and safety like CRC later
                if (!TX_busy && !send_reg) begin
                    busy_next       = 1'b0;
                    state_next      = IDLE;
                end
            end
            default: begin
            end
        endcase
    end

    assign busy = busy_reg;

endmodule