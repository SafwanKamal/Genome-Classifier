module button_debouncer(
    input  logic clk,
    input  logic reset,
    input  logic button_in,
    output logic button_out
);

    // Debouncing the button signal to avoid multiple triggers on a single press
    logic [17:0] debounce_counter;
    logic button_state;

    always_ff @(posedge clk or posedge reset) begin
        if (reset) begin
            debounce_counter <= 18'b0;
            button_state <= 1'b0;
            button_out <= 1'b0;
        end else begin
            if (button_in != button_state) begin
                debounce_counter <= debounce_counter + 1;
                if (debounce_counter == 18'h3FFFF) begin
                    button_state <= button_in;
                    button_out <= button_in;
                    debounce_counter <= 18'b0;
                end
            end else begin
                debounce_counter <= 18'b0;
            end
        end
    end


endmodule