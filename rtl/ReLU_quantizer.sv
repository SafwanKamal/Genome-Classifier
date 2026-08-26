module ReLU_quantizer #(
    parameter integer DATA_NUMBER = 4,
    parameter integer QSHIFT      = 4
) (
    input  logic clk,
    input  logic reset,
    input  logic start,

    input  logic signed [31:0] data32 [0:DATA_NUMBER - 1],

    output logic signed [7:0] data8 [0:DATA_NUMBER - 1],
    output logic done
);

    logic signed [7:0] data8_reg  [0:DATA_NUMBER - 1];
    logic signed [7:0] data8_next [0:DATA_NUMBER - 1];

    logic done_reg, done_next;


    function automatic logic signed [7:0] quantize (
        input logic signed [31:0] value
    );

        logic signed [32:0] rounded_value;
        logic signed [32:0] scaled_value;

        begin
            // ReLU
            if (value <= 0) begin
                quantize = 8'sd0;
            end
            else begin
                // Round before dividing by 2^QSHIFT
                rounded_value =
                    {1'b0, value}
                    + (33'sd1 <<< (QSHIFT - 1));

                scaled_value = rounded_value >>> QSHIFT;

                // Saturate to the positive signed INT8 range
                if (scaled_value > 33'sd127) begin
                    quantize = 8'sd127;
                end
                else begin
                    quantize = scaled_value[7:0];
                end
            end
        end

    endfunction


    always_comb begin
        data8_next = data8_reg;
        done_next  = 1'b0;

        if (start) begin
            for (int i = 0; i < DATA_NUMBER; i++) begin
                data8_next[i] = quantize(data32[i]);
            end

            done_next = 1'b1;
        end
    end


    always_ff @(posedge clk or posedge reset) begin
        if (reset) begin
            for (int i = 0; i < DATA_NUMBER; i++) begin
                data8_reg[i] <= '0;
            end
            done_reg  <= 1'b0;
        end
        else begin
            data8_reg <= data8_next;
            done_reg  <= done_next;
        end
    end


    assign data8 = data8_reg;
    assign done  = done_reg;

endmodule