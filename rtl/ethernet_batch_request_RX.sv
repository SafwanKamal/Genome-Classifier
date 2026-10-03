// CRC-checked RB01 frame: base sequence (BE32), count (1..32), reserved=0,
// then count*16 feature bytes. Hold one batch and stream requests with backpressure.
module ethernet_batch_request_RX (
    input logic clk, reset,
    input logic [7:0] data_in,
    input logic valid_in, last_in,
    output logic ready_out,
    output logic request_valid,
    input logic request_ready,
    output logic [31:0] sequence_out,
    output logic [7:0] features_out [0:15],
    output logic batch_last
);
    logic [7:0] features [0:511];
    integer offset, count, record_index;
    logic [31:0] base_sequence;
    logic match, local_match, broadcast_match, byte_matches, publishing;
    assign ready_out = !publishing;
    assign request_valid = publishing;
    assign sequence_out = base_sequence + record_index;
    assign batch_last = record_index == count-1;
    for (genvar i=0; i<16; i++) assign features_out[i] = features[record_index*16+i];
    always_comb begin
        byte_matches = 1;
        case (offset)
            12: byte_matches = data_in == 8'h88;
            13: byte_matches = data_in == 8'hb5;
            14: byte_matches = data_in == "R";
            15: byte_matches = data_in == "B";
            16: byte_matches = data_in == "0";
            17: byte_matches = data_in == "1";
            22: byte_matches = data_in >= 1 && data_in <= 32;
            23: byte_matches = data_in == 0;
            default: ;
        endcase
    end
    always_ff @(posedge clk) begin
        if (reset) begin
            offset <= 0; count <= 0; record_index <= 0; base_sequence <= 0;
            match <= 1; local_match <= 1; broadcast_match <= 1; publishing <= 0;
        end else begin
            if (publishing && request_ready) begin
                if (batch_last) begin publishing <= 0; record_index <= 0; end
                else record_index <= record_index+1;
            end
            if (valid_in && ready_out) begin
                if (offset < 6) begin
                    local_match <= local_match && data_in == (offset==0 ? 8'h02 : (offset==5 ? 8'h01 : 8'h00));
                    broadcast_match <= broadcast_match && data_in == 8'hff;
                end
                match <= match && byte_matches;
                if (offset>=18 && offset<=21) base_sequence <= {base_sequence[23:0], data_in};
                if (offset==22) count <= data_in;
                if (offset>=24 && offset<536) features[offset-24] <= data_in;
                if (last_in) begin
                    // Require all advertised records; Ethernet padding is ignored.
                    if (match && byte_matches && (local_match || broadcast_match) &&
                        count>=1 && count<=32 && offset+1 >= 24+count*16)
                        publishing <= 1;
                    offset <= 0; match <= 1; local_match <= 1; broadcast_match <= 1;
                end else offset <= offset+1;
            end
        end
    end
endmodule
