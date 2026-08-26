module UART_packet_RX #(
    parameter integer CLOCK_HZ  = 100_000_000,
    parameter integer BAUD_RATE = 115_200,
    parameter integer PACKET_BYTE_NUMBER = 16
) (
    input  logic       clk,
    input  logic       reset,
    input  logic       rx,

    output logic [7:0] packet [0:PACKET_BYTE_NUMBER - 1],
    output logic       packet_valid
);

    localparam integer CLKS_PER_BIT =
        (CLOCK_HZ + BAUD_RATE / 2) / BAUD_RATE;

    localparam integer HALF_CLKS   = CLKS_PER_BIT / 2;
    localparam integer COUNT_WIDTH = $clog2(CLKS_PER_BIT);


    // The packet structure is:
    // 0x5A (start byte)
    // length byte (number of bytes in the packet, excluding the start byte and length byte)
    // 16 bytes of data (payload)
    // CRC 

    // CRC16-CCITT function
    function automatic logic [15:0] CRC16_CCITT(input logic [7:0] data_in, input logic [15:0] crc_in);
        logic [15:0] crc;
        integer i;
        begin
            crc = crc_in ^ {data_in, 8'h00};
            for (i = 0; i < 8; i++) begin
                if (crc[15]) begin
                    crc = (crc << 1) ^ 16'h1021;
                end else begin
                    crc = crc << 1;
                end
            end
            CRC16_CCITT = crc;
        end
        
    endfunction



    logic [7:0]             data;
    logic                   rx_valid;
    logic                   framing_error;
    logic [7:0]             packet_reg[0:PACKET_BYTE_NUMBER - 1];
    logic [7:0]             packet_next[0:PACKET_BYTE_NUMBER - 1];
    logic [$clog2(PACKET_BYTE_NUMBER) - 1:0] packet_index_reg, packet_index_next;
    logic                   packet_valid_reg, packet_valid_next;
    logic [15:0]            CRC_reg, CRC_next, CRC_rx_reg, CRC_rx_next;

    
    UART_RX #(
        .CLOCK_HZ  (CLOCK_HZ),
        .BAUD_RATE (BAUD_RATE)
    ) uart_rx_unit (
        .clk           (clk),
        .reset         (reset),
        .rx            (rx),
        .data          (data),
        .data_valid    (rx_valid),
        .framing_error (framing_error)
    );

    typedef enum logic [2:0] {
        IDLE,
        LENGTH,
        DATA,
        CRC_HIGH,
        CRC_LOW
    } state_t;

    state_t state_reg, state_next;

    always_ff @(posedge clk or posedge reset) begin : blockName
        if (reset) begin
            packet_index_reg <= 0;
            state_reg <= IDLE;
            packet_valid_reg <= 0;
            CRC_reg <= 16'hFFFF;
            CRC_rx_reg <= 16'h0000;
            for (int i = 0; i < PACKET_BYTE_NUMBER; i++) begin
                packet_reg[i] <= 8'h00;
            end
        end else begin
            packet_index_reg <= packet_index_next;
            packet_reg <= packet_next;
            state_reg <= state_next;
            packet_valid_reg <= packet_valid_next;
            CRC_reg <= CRC_next;
            CRC_rx_reg <= CRC_rx_next;
        end
    end



    always_comb begin
        packet_next = packet_reg;
        packet_index_next = packet_index_reg;
        state_next = state_reg;
        packet_valid_next = 1'b0;
        CRC_next = CRC_reg;
        CRC_rx_next = CRC_rx_reg;

        case (state_reg)
            IDLE: begin
                // We first check if we ever get 0x5A (can be anything else)
                // That will indicate the start of our data packet
                // 0x5A is not part of the packet, so we don't store it
                if (rx_valid) begin
                    packet_valid_next = 1'b0;
                    packet_index_next = 0;
//                  packet_next = PACKET_BYTE_NUMBER{8{1'b0}};
                    packet_next = '{default: 8'h00};
                    CRC_next = 16'hFFFF;

                    if (data == 8'h5A) begin
                        // CRC_next = CRC16_CCITT(data, CRC_reg);
                        // This is because of a edge case where if we receive something right after the previous packet,
                        // CRC_reg will not be reset to 0xFFFF, and the CRC will be wrong. So we reset it here.
                        CRC_next = CRC16_CCITT(data, 16'hFFFF);
                        state_next = LENGTH;
                    end
                end
            end

            LENGTH: begin
                // Length byte is also not part of the packet, so we don't store it
                if (framing_error) begin
                    state_next = IDLE;
                end else if (rx_valid) begin
                    if (data == PACKET_BYTE_NUMBER) begin
                        state_next = DATA;
                        CRC_next = CRC16_CCITT(data, CRC_reg);
                    end else begin
                        state_next = IDLE; // Discard the packet and go back to IDLE
                    end
                end
            end

            DATA: begin
                if (framing_error) begin
                    state_next = IDLE;
                end else if (rx_valid) begin
                    if (packet_index_reg < PACKET_BYTE_NUMBER - 1) begin
                        // this variable will overflow if we receive more than PACKET_BYTE_NUMBER bytes, but we don't care about that
                        packet_index_next = packet_index_reg + 1;
                        packet_next[packet_index_reg] = data;

                        CRC_next = CRC16_CCITT(data, CRC_reg);

                        // We need to discard the packet if any of the middle bytes
                        // are invalid (framing_error is asserted for any byte)
                        
                    end else begin
                        packet_index_next = 0;
                        packet_next[packet_index_reg] = data;

                        CRC_next = CRC16_CCITT(data, CRC_reg);
                        if (framing_error) begin
                            state_next = IDLE;
                        end else begin
                            state_next = CRC_HIGH;
                        end
                    end
                end
            end

            CRC_HIGH: begin
                if (framing_error) begin
                    state_next = IDLE;
                end else if (rx_valid) begin
                    CRC_rx_next[15:8] = data;
                    state_next = CRC_LOW;
                end
            end

            CRC_LOW: begin
                if (framing_error) begin
                    state_next = IDLE;
                end else if (rx_valid) begin
                    if ({CRC_rx_reg[15:8], data} == CRC_reg) begin
                        packet_valid_next = 1'b1;
                    end else begin
                        packet_valid_next = 1'b0;
                    end
                    state_next = IDLE; 
                end
            end

            default: begin
            end
        endcase
    end

    assign packet_valid = packet_valid_reg;
    assign packet = packet_reg;

endmodule