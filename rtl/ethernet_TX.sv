// 100 mbps full-duplex transmit framing, using a 50 mhz rmii clock
// input bytes: destination mac, source mac, type/length, payload
// the source must have the complete frame available before asserting valid
module ethernet_TX (
    input  logic clk,
    input  logic reset,

    input  logic [7:0] data_in,
    input  logic valid_in,
    output logic ready_out,
    input  logic last_in,

    output logic [1:0] rmii_txd,
    output logic rmii_tx_en,
    output logic busy_out,
    output logic done_out,
    output logic underflow_out
);

    typedef enum logic [2:0] {
        idle, preamble, delimiter, frame_data,
        padding, fcs, drain, gap
    } state_t;

    state_t state_reg, state_next;
    logic [2:0] preamble_count_reg, preamble_count_next;
    logic [5:0] byte_count_reg, byte_count_next;
    logic [1:0] fcs_count_reg, fcs_count_next;
    logic [5:0] gap_count_reg, gap_count_next;
    logic done_reg, done_next;

    logic [7:0] tx_data;
    logic tx_valid, tx_ready, tx_last, tx_underflow;
    logic crc_clear, crc_enable, crc_covered;
    logic [31:0] crc_value;

    rmii_TX serializer (
        .clk(clk),
        .reset(reset),
        .data_in(tx_data),
        .valid_in(tx_valid),
        .ready_out(tx_ready),
        .last_in(tx_last),
        .rmii_txd(rmii_txd),
        .rmii_tx_en(rmii_tx_en),
        .underflow_out(tx_underflow)
    );

    ethernet_CRC32 crc_calculator (
        .clk(clk),
        .reset(reset),
        .clear_in(crc_clear),
        .data_in(tx_data),
        .enable_in(crc_enable),
        .crc_out(crc_value)
    );

    always_ff @(posedge clk or posedge reset) begin
        if (reset) begin
            state_reg <= idle;
            preamble_count_reg <= 0;
            byte_count_reg <= 0;
            fcs_count_reg <= 0;
            gap_count_reg <= 0;
            done_reg <= 1'b0;
        end else begin
            state_reg <= state_next;
            preamble_count_reg <= preamble_count_next;
            byte_count_reg <= byte_count_next;
            fcs_count_reg <= fcs_count_next;
            gap_count_reg <= gap_count_next;
            done_reg <= done_next;
        end
    end

    always_comb begin
        state_next = state_reg;
        preamble_count_next = preamble_count_reg;
        byte_count_next = byte_count_reg;
        fcs_count_next = fcs_count_reg;
        gap_count_next = gap_count_reg;
        done_next = 1'b0;

        tx_data = 8'h00;
        tx_valid = 1'b0;
        tx_last = 1'b0;
        ready_out = 1'b0;
        crc_clear = 1'b0;
        crc_covered = 1'b0;

        case (state_reg)
            idle: begin
                crc_clear = 1'b1;
                preamble_count_next = 0;
                byte_count_next = 0;
                fcs_count_next = 0;
                gap_count_next = 0;

                // the source holds its first byte until ready_out is high
                if (valid_in)
                    state_next = preamble;
            end

            preamble: begin
                tx_data = 8'h55;
                tx_valid = 1'b1;

                if (tx_ready) begin
                    if (preamble_count_reg == 3'd6) begin
                        state_next = delimiter;
                    end else begin
                        preamble_count_next = preamble_count_reg + 1'b1;
                    end
                end
            end

            delimiter: begin
                tx_data = 8'hD5;
                tx_valid = 1'b1;

                if (tx_ready)
                    state_next = frame_data;
            end

            frame_data: begin
                tx_data = data_in;
                tx_valid = valid_in;
                ready_out = tx_ready;
                crc_covered = 1'b1;

                if (valid_in && tx_ready) begin
                    // saturate at 60; only the minimum length matters here
                    if (byte_count_reg < 6'd60)
                        byte_count_next = byte_count_reg + 1'b1;

                    if (last_in) begin
                        // the current accepted byte is included in the total
                        if (byte_count_reg >= 6'd59) begin
                            state_next = fcs;
                        end else begin
                            state_next = padding;
                        end
                    end
                end
            end

            padding: begin
                tx_data = 8'h00;
                tx_valid = 1'b1;
                crc_covered = 1'b1;

                if (tx_ready) begin
                    byte_count_next = byte_count_reg + 1'b1;

                    if (byte_count_reg == 6'd59)
                        state_next = fcs;
                end
            end

            fcs: begin
                // crc_value stays fixed because crc_enable is low here
                tx_data = crc_value[fcs_count_reg * 8 +: 8];
                tx_valid = 1'b1;
                tx_last = (fcs_count_reg == 2'd3);

                if (tx_ready) begin
                    if (fcs_count_reg == 2'd3) begin
                        state_next = drain;
                    end else begin
                        fcs_count_next = fcs_count_reg + 1'b1;
                    end
                end
            end

            drain: begin
                // acceptance of the last byte precedes its final wire dibit
                if (!rmii_tx_en) begin
                    done_next = 1'b1;
                    gap_count_next = 0;
                    state_next = gap;
                end
            end

            gap: begin
                // at least 48 idle rmii clocks = 96 bit times
                if (gap_count_reg == 6'd47) begin
                    state_next = idle;
                end else begin
                    gap_count_next = gap_count_reg + 1'b1;
                end
            end

            default: begin
                state_next = idle;
            end
        endcase

        // suppress a restart while the serializer reports an aborted frame
        if (tx_underflow) begin
            tx_valid = 1'b0;
            tx_last = 1'b0;
            ready_out = 1'b0;
            crc_covered = 1'b0;
            done_next = 1'b0;
            gap_count_next = 0;
            state_next = gap;
        end
    end

    // only accepted header, payload, and padding bytes update the crc
    assign crc_enable = crc_covered && tx_valid && tx_ready;
    assign busy_out = (state_reg != idle);
    assign done_out = done_reg;
    assign underflow_out = tx_underflow;

endmodule