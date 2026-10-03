// Store-and-forward raw Ethernet receiver. Output contains destination MAC,
// source MAC, EtherType/length and payload/padding; preamble and FCS are removed.
// All ports use the 50 MHz PHY reference clock. No backpressure exists on RMII.
module ethernet_RX #(
    parameter integer MAX_FRAME_BYTES = 1518
) (
    input  logic       clk,
    input  logic       reset,
    input  logic [1:0] rmii_rxd,
    input  logic       rmii_crs_dv,
    input  logic       rmii_rx_er,
    output logic [7:0] data_out,
    output logic       valid_out,
    input  logic       ready_in,
    output logic       last_out,
    output logic       good_frame_out,
    output logic       bad_frame_out,
    output logic       overflow_out
);
    localparam integer COUNT_WIDTH = $clog2(MAX_FRAME_BYTES + 1);
    localparam logic [31:0] GOOD_RESIDUE = 32'h2144DF1C;
    typedef enum logic [1:0] {idle, capture, deliver, discard} state_t;

    state_t state_reg;
    logic [COUNT_WIDTH-1:0] count_reg, read_reg, length_reg;
    (* ram_style = "block" *) logic [7:0] frame_mem [0:MAX_FRAME_BYTES-1];
    logic [COUNT_WIDTH-1:0] next_read;
    (* mark_debug = "true" *) logic [7:0] rx_data;
    (* mark_debug = "true" *) logic rx_valid, rx_start, rx_end, rx_error;
    logic [31:0] crc_value;
    logic frame_error_reg;
    logic discard_to_idle_reg;

    rmii_RX decoder (
        .clk(clk), .reset(reset),
        .rmii_rxd(rmii_rxd), .rmii_crs_dv(rmii_crs_dv),
        .rmii_rx_er(rmii_rx_er),
        .data_out(rx_data), .valid_out(rx_valid),
        .start_out(rx_start), .end_out(rx_end), .error_out(rx_error)
    );

    ethernet_CRC32 crc (
        .clk(clk), .reset(reset), .clear_in(rx_start),
        .data_in(rx_data), .enable_in(state_reg == capture && rx_valid),
        .crc_out(crc_value)
    );

    // Synchronous simple dual-port RAM. Prefetch the following byte on a
    // transfer, keeping the output stable during backpressure without bubbles.
    assign next_read = (state_reg == capture) ? '0 :
        read_reg + ((valid_out && ready_in && !last_out) ? 1'b1 : 1'b0);
    always_ff @(posedge clk) begin
        if (!reset && state_reg == capture && rx_valid && count_reg < MAX_FRAME_BYTES)
            frame_mem[count_reg] <= rx_data;
        data_out <= frame_mem[next_read];
    end
    assign valid_out = (state_reg == deliver);
    assign last_out = valid_out && (read_reg == length_reg - 1'b1);

    always_ff @(posedge clk or posedge reset) begin
        if (reset) begin
            state_reg <= idle;
            count_reg <= '0;
            read_reg <= '0;
            length_reg <= '0;
            frame_error_reg <= 1'b0;
            discard_to_idle_reg <= 1'b0;
            good_frame_out <= 1'b0;
            bad_frame_out <= 1'b0;
            overflow_out <= 1'b0;
        end else begin
            good_frame_out <= 1'b0;
            bad_frame_out <= 1'b0;
            overflow_out <= 1'b0;

            case (state_reg)
                idle: if (rx_start) begin
                    count_reg <= '0;
                    frame_error_reg <= 1'b0;
                    state_reg <= capture;
                end

                capture: begin
                    if (rx_valid) begin
                        if (count_reg < MAX_FRAME_BYTES) begin
                            count_reg <= count_reg + 1'b1;
                        end else begin
                            frame_error_reg <= 1'b1;
                        end
                    end

                    if (rx_end) begin
                        if (rx_error || frame_error_reg ||
                            count_reg < 64 || count_reg > MAX_FRAME_BYTES ||
                            crc_value != GOOD_RESIDUE) begin
                            bad_frame_out <= 1'b1;
                            state_reg <= idle;
                        end else begin
                            length_reg <= count_reg - 4;
                            read_reg <= '0;
                            good_frame_out <= 1'b1;
                            state_reg <= deliver;
                        end
                    end
                end

                deliver: begin
                    if (valid_out && ready_in) begin
                        if (last_out) begin
                            state_reg <= idle;
                        end else begin
                            read_reg <= read_reg + 1'b1;
                        end
                    end
                    if (rx_start) begin
                        overflow_out <= 1'b1;
                        discard_to_idle_reg <= valid_out && ready_in && last_out;
                        state_reg <= discard;
                    end
                end

                discard: if (rx_end)
                    state_reg <= discard_to_idle_reg ? idle : deliver;

                default: state_reg <= idle;
            endcase
        end
    end
endmodule
