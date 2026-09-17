module rmii_TX(
    input logic clk,
    input  logic reset,

    input logic [7:0] data_in,
    input logic valid_in, // data_in is ready to be accepted
    output logic ready_out, // indicate when the module is ready to accept new data
    input logic last_in, // indicate the last data of a packet
    output logic [1:0] rmii_txd,
    output logic rmii_tx_en,
    output logic underflow_out // When valid_in is low, but ready_out is high in the middle of a transmission
);

    logic [1:0] two_bit_counter_reg, two_bit_counter_next;
    logic [1:0] two_bit_reg, two_bit_next;
    logic [7:0] data_reg, data_next;
    logic last_in_reg, last_in_next;
    logic ready_out_reg, ready_out_next;
    logic underflow_out_reg, underflow_out_next;
    logic rmii_tx_en_reg, rmii_tx_en_next;

    typedef enum logic [1:0] {
        IDLE = 2'b00,
        TRANSMIT = 2'b01,
        LAST = 2'b10
    } state_t;

    state_t state_reg, state_next;

    always_ff @(posedge clk or posedge reset) begin
        if (reset) begin
            two_bit_counter_reg <= 2'b00;
            two_bit_reg <= 2'b00;
            data_reg <= 8'b00000000;
            state_reg <= IDLE;
            last_in_reg <= 1'b0;
            ready_out_reg <= 1'b1; // Ready to accept new data after reset
            underflow_out_reg <= 1'b0;
            rmii_tx_en_reg <= 1'b0;
        end else begin
            two_bit_counter_reg <= two_bit_counter_next;
            two_bit_reg <= two_bit_next;
            data_reg <= data_next;
            state_reg <= state_next;
            last_in_reg <= last_in_next;
            ready_out_reg <= ready_out_next;
            underflow_out_reg <= underflow_out_next;
            rmii_tx_en_reg <= rmii_tx_en_next;
        end
    end

    always_comb begin
        // Default assignments
        two_bit_counter_next = two_bit_counter_reg;
        two_bit_next = two_bit_reg;
        data_next = data_reg;
        state_next = state_reg;
        last_in_next = last_in_reg;
        ready_out_next = ready_out_reg;

        underflow_out_next = 1'b0; // Clear underflow by default
        rmii_tx_en_next = 1'b0; // Default to not transmitting

        case (state_reg)
            IDLE: begin
                ready_out_next = 1'b1;
                if (valid_in) begin
                    two_bit_counter_next = 2'b00;
                    state_next = TRANSMIT;
                    ready_out_next = 1'b0; 
                    two_bit_next = data_in[1:0]; 
                    data_next = {2'b00, data_in[7:2]}; 
                    rmii_tx_en_next = 1'b1;
                    last_in_next = last_in;
                end
            end

            TRANSMIT: begin
                rmii_tx_en_next = 1'b1;
                two_bit_next = data_reg[1:0];
                data_next = {2'b00, data_reg[7:2]}; // Shift left to prepare next two bits for transmission

                if(two_bit_counter_reg == 2'b10) begin
                    // We have to do this on the second to last 2-bit transmission
                    // because we are using _reg and _next method here.
                    if (last_in_reg) begin
                        ready_out_next = 1'b0;
                    end else begin
                        ready_out_next = 1'b1; // Ready to accept new data
                    end
                end else if (two_bit_counter_reg == 2'b11) begin
                    if (last_in_reg) begin
                        state_next = IDLE; // We are done transmitting the last byte
                        last_in_next = 1'b0; 
                        rmii_tx_en_next = 1'b0;
                        ready_out_next = 1'b1; 
                    end else if (valid_in) begin
                        two_bit_next = data_in[1:0]; 
                        data_next = {2'b00, data_in[7:2]}; 
                        last_in_next = last_in;
                        ready_out_next = 1'b0;
                    end else begin
                        underflow_out_next = 1'b1; // Underflow condition, no new data available
                        state_next = IDLE; 
                        rmii_tx_en_next = 1'b0;
                        ready_out_next = 1'b1;
                    end
                end
                // Will use the overflow of the 2-bit counter to determine when we have transmitted all 4 pairs of bits from the byte.
                two_bit_counter_next = two_bit_counter_reg + 1;
            end

        endcase
    end

    assign rmii_txd = two_bit_reg;
    assign ready_out = ready_out_reg;
    assign underflow_out = underflow_out_reg;
    assign rmii_tx_en = rmii_tx_en_reg;

endmodule