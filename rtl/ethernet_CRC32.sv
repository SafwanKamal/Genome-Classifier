module ethernet_CRC32 (
    input  logic clk,
    input  logic reset,
    input  logic clear_in,
    input  logic [7:0] data_in,
    input  logic enable_in,
    output logic [31:0] crc_out
);

    localparam logic [31:0] crc_initial = 32'hFFFFFFFF;
    localparam logic [31:0] crc_polynomial = 32'hEDB88320;

    logic [31:0] crc_reg, crc_next;

    function automatic logic [31:0] calculate_crc_byte(
        input logic [31:0] current_crc,
        input logic [7:0] data
    );
        logic [31:0] crc_work;

        begin
            crc_work = current_crc;

            // process one byte, starting with its least significant bit
            for (int i = 0; i < 8; i = i + 1) begin
                if (crc_work[0] ^ data[i]) begin
                    crc_work = (crc_work >> 1) ^ crc_polynomial;
                end else begin
                    crc_work = crc_work >> 1;
                end
            end

            calculate_crc_byte = crc_work;
        end
    endfunction

    always_ff @(posedge clk or posedge reset) begin
        if (reset) begin
            crc_reg <= crc_initial;
        end else begin
            crc_reg <= crc_next;
        end
    end

    always_comb begin
        crc_next = crc_reg;

        // clear takes priority; present the first byte on a later cycle
        if (clear_in) begin
            crc_next = crc_initial;
        end else if (enable_in) begin
            crc_next = calculate_crc_byte(crc_reg, data_in);
        end
    end

    // complement the running state to obtain the current fcs value
    assign crc_out = ~crc_reg;

endmodule