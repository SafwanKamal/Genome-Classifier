// Receive 100 Mb/s RMII on the 50 MHz PHY reference clock.
// Data dibits arrive least-significant first. The preamble and SFD are removed.
module rmii_RX (
    input logic clk, reset,
    input logic [1:0] rmii_rxd,
    input logic rmii_crs_dv, rmii_rx_er,
    output logic [7:0] data_out,
    output logic valid_out, start_out, end_out, error_out
);
    // Capture the entire RMII bundle together, before any decode logic.
    // No reset on these input flops: reset below flushes the decoder state.
    (* IOB = "TRUE", mark_debug = "true" *) logic [1:0] rxd_sample;
    (* IOB = "TRUE", mark_debug = "true" *) logic dv_sample, er_sample;
    always_ff @(posedge clk) begin
        rxd_sample <= rmii_rxd;
        dv_sample <= rmii_crs_dv;
        er_sample <= rmii_rx_er;
    end

    typedef enum logic [2:0] {idle, carrier, preamble, frame_data, reject} state_t;
    state_t state_reg;
    logic [1:0] pair_reg;
    logic [7:0] byte_reg;
    logic [4:0] preamble_reg;
    logic low_reg, error_reg;

    always_ff @(posedge clk or posedge reset) begin
        if (reset) begin
            state_reg <= idle;
            pair_reg <= '0;
            byte_reg <= '0;
            preamble_reg <= '0;
            low_reg <= 0;
            error_reg <= 0;
            data_out <= '0;
            valid_out <= 0;
            start_out <= 0;
            end_out <= 0;
            error_out <= 0;
        end else begin
            valid_out <= 0;
            start_out <= 0;
            end_out <= 0;
            error_out <= 0;
            if (!dv_sample && low_reg) begin
                // One low CRS_DV cycle can be valid data at the frame tail.
                // Two consecutive lows end the frame; the first is speculative.
                if (state_reg == frame_data) begin
                    end_out <= 1;
                    error_out <= error_reg || (pair_reg != 2'd1);
                end
                state_reg <= idle;
                pair_reg <= '0;
                preamble_reg <= '0;
                low_reg <= 0;
            end else if (dv_sample || state_reg != idle) begin
                low_reg <= !dv_sample;
                if (er_sample) error_reg <= 1;
                case (state_reg)
                    idle, carrier: begin
                        pair_reg <= '0;
                        if (state_reg == idle) error_reg <= er_sample;
                        // LAN8720A asserts carrier before decoded data is ready.
                        // Wait through 00; the first 01 starts the preamble run.
                        case (rxd_sample)
                            2'b00: state_reg <= carrier;
                            2'b01: begin
                                state_reg <= preamble;
                                preamble_reg <= 1;
                                pair_reg <= 1;
                            end
                            default: state_reg <= reject;
                        endcase
                    end
                    preamble: begin
                        if (rxd_sample == 2'b01) begin
                            pair_reg <= pair_reg + 1'b1;
                            if (preamble_reg != 31) preamble_reg <= preamble_reg + 1'b1;
                        end else if (rxd_sample == 2'b11 && preamble_reg >= 7 && pair_reg == 3 &&
                                     !error_reg && !er_sample) begin
                            // D5 ends in 11 after repeated 01 dibits. Align the
                            // first destination-MAC byte to the following cycle.
                            state_reg <= frame_data;
                            pair_reg <= '0;
                            start_out <= 1;
                        end else state_reg <= reject;
                    end
                    frame_data: begin
                        byte_reg <= {rxd_sample, byte_reg[7:2]};
                        pair_reg <= pair_reg + 1'b1;
                        if (pair_reg == 3) begin
                            data_out <= {rxd_sample, byte_reg[7:2]};
                            valid_out <= 1;
                        end
                    end
                    default: begin end
                endcase
            end else low_reg <= 0;
        end
    end
endmodule
