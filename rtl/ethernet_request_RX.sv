// Parse the RX01 bring-up request from ethernet_RX's CRC-checked byte stream.
// All ports share the receiver clock; classifier clock crossing comes later.
module ethernet_request_RX #(
    parameter logic [47:0] LOCAL_MAC = 48'h020000000001
) (
    input  logic clk, reset,
    input  logic [7:0] data_in,
    input  logic valid_in, last_in,
    output logic ready_out,
    output logic request_valid,
    input  logic request_ready,
    output logic [31:0] sequence_out,
    output logic [7:0] features_out [0:15]
);
    logic [10:0] offset;
    logic local_match, broadcast_match, format_match;
    logic byte_matches;

    assign ready_out = !request_valid;
    always_comb begin
        byte_matches = 1'b1;
        case (offset)
            12: byte_matches = data_in == 8'h88;
            13: byte_matches = data_in == 8'hb5;
            14: byte_matches = data_in == 8'h52; // R
            15: byte_matches = data_in == 8'h58; // X
            16: byte_matches = data_in == 8'h30; // 0
            17: byte_matches = data_in == 8'h31; // 1
            default: ;
        endcase
    end

    always_ff @(posedge clk) begin
        if (reset) begin
            offset <= 0;
            local_match <= 1;
            broadcast_match <= 1;
            format_match <= 1;
            request_valid <= 0;
            sequence_out <= 0;
        end else begin
            if (request_valid && request_ready)
                request_valid <= 0;
            if (valid_in && ready_out) begin
                if (offset < 6) begin
                    local_match <= local_match &&
                        (data_in == LOCAL_MAC[47 - offset*8 -: 8]);
                    broadcast_match <= broadcast_match && (data_in == 8'hff);
                end
                format_match <= format_match && byte_matches;
                if (offset >= 18 && offset <= 21)
                    sequence_out <= {sequence_out[23:0], data_in};
                if (offset >= 22 && offset <= 37)
                    features_out[offset - 22] <= data_in;
                if (last_in) begin
                    // Padding is ignored. Publish only a complete request.
                    if (offset >= 37 && (local_match || broadcast_match) &&
                        format_match && byte_matches)
                        request_valid <= 1;
                    offset <= 0;
                    local_match <= 1;
                    broadcast_match <= 1;
                    format_match <= 1;
                end else
                    offset <= offset + 1'b1;
            end
        end
    end
endmodule
