// Receive 100 Mb/s RMII on the 50 MHz PHY reference clock.
// Data dibits arrive least-significant first. The preamble and SFD are removed.
module rmii_RX (
    input  logic       clk,
    input  logic       reset,
    input  logic [1:0] rmii_rxd,
    input  logic       rmii_crs_dv,
    input  logic       rmii_rx_er,
    output logic [7:0] data_out,
    output logic       valid_out,
    output logic       start_out,
    output logic       end_out,
    output logic       error_out
);
    typedef enum logic [1:0] {idle, preamble, frame_data, reject} state_t;
    state_t state_reg;
    logic [1:0] pair_reg;
    logic [7:0] byte_reg;
    logic [2:0] preamble_reg;
    logic       low_reg;
    logic       error_reg;

    always_ff @(posedge clk or posedge reset) begin
        if (reset) begin
            state_reg <= idle;
            pair_reg <= '0;
            byte_reg <= '0;
            preamble_reg <= '0;
            low_reg <= 1'b0;
            error_reg <= 1'b0;
            data_out <= '0;
            valid_out <= 1'b0;
            start_out <= 1'b0;
            end_out <= 1'b0;
            error_out <= 1'b0;
        end else begin
            valid_out <= 1'b0;
            start_out <= 1'b0;
            end_out <= 1'b0;
            error_out <= 1'b0;

            if (!rmii_crs_dv && low_reg) begin
                // CRS_DV may toggle at the end of a frame while RX_DV is
                // still asserted. One low cycle is therefore not EOF.
                if (state_reg == frame_data) begin
                    end_out <= 1'b1;
                    error_out <= error_reg || (pair_reg != 2'd1);
                end
                state_reg <= idle;
                pair_reg <= '0;
                preamble_reg <= '0;
                low_reg <= 1'b0;
            end else if (rmii_crs_dv || state_reg != idle) begin
                low_reg <= !rmii_crs_dv;
                if (rmii_rx_er)
                    error_reg <= 1'b1;

                // The first low CRS_DV dibit is speculative. A normal
                // frame ended on a byte boundary, so it cannot complete
                // another byte. If CRS_DV rises again it was valid data.
                byte_reg[pair_reg * 2 +: 2] <= rmii_rxd;
                pair_reg <= pair_reg + 1'b1;

                if (pair_reg == 2'd3) begin
                    pair_reg <= '0;
                    case (state_reg)
                        preamble: begin
                            if ({rmii_rxd, byte_reg[5:0]} == 8'h55) begin
                                if (preamble_reg != 3'd7)
                                    preamble_reg <= preamble_reg + 1'b1;
                            end else if ({rmii_rxd, byte_reg[5:0]} == 8'hD5 &&
                                         preamble_reg != 3'd0 &&
                                         !error_reg && !rmii_rx_er) begin
                                state_reg <= frame_data;
                                start_out <= 1'b1;
                            end else begin
                                state_reg <= reject;
                            end
                        end
                        frame_data: begin
                            data_out <= {rmii_rxd, byte_reg[5:0]};
                            valid_out <= 1'b1;
                        end
                        default: begin end
                    endcase
                end

                if (state_reg == idle) begin
                    state_reg <= preamble;
                    pair_reg <= 2'd1;
                    byte_reg[1:0] <= rmii_rxd;
                    preamble_reg <= '0;
                    error_reg <= rmii_rx_er;
                end
            end else begin
                low_reg <= 1'b0;
            end
        end
    end
endmodule
