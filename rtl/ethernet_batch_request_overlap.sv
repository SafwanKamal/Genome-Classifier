// Two RB01 batch banks: fill one while streaming records from the other.
module ethernet_batch_request_overlap (
    input logic clk, reset,
    input logic [7:0] data_in,
    input logic valid_in, last_in,
    output logic ready_out, request_valid,
    input logic request_ready,
    output logic [31:0] sequence_out,
    output logic [7:0] features_out [0:15],
    output logic batch_last
);
    logic [7:0] features [0:1023];
    logic [1:0] full;
    logic write_bank, read_bank;
    integer offset, incoming_count, record_index;
    logic [5:0] counts [0:1];
    logic [31:0] incoming_sequence, sequences [0:1];
    logic match, local_match, broadcast_match, byte_matches;
    assign ready_out=!full[write_bank];
    assign request_valid=full[read_bank];
    assign sequence_out=sequences[read_bank]+record_index;
    assign batch_last=record_index==counts[read_bank]-1;
    for (genvar i=0;i<16;i++) assign features_out[i]=features[read_bank*512+record_index*16+i];
    always_comb begin
        byte_matches=1;
        case(offset)
            12:byte_matches=data_in==8'h88;
            13:byte_matches=data_in==8'hb5;
            14:byte_matches=data_in=="R";
            15:byte_matches=data_in=="B";
            16:byte_matches=data_in=="0";
            17:byte_matches=data_in=="1";
            22:byte_matches=data_in>=1 && data_in<=32;
            23:byte_matches=data_in==0;
            default:;
        endcase
    end
    always_ff @(posedge clk) begin
        if(reset) begin
            full<=0;write_bank<=0;read_bank<=0;offset<=0;record_index<=0;
            incoming_count<=0;incoming_sequence<=0;match<=1;local_match<=1;broadcast_match<=1;
        end else begin
            if(request_valid && request_ready) begin
                if(batch_last) begin full[read_bank]<=0;read_bank<=!read_bank;record_index<=0;end
                else record_index<=record_index+1;
            end
            if(valid_in && ready_out) begin
                if(offset<6) begin
                    local_match<=local_match && data_in==(offset==0?8'h02:(offset==5?8'h01:8'h00));
                    broadcast_match<=broadcast_match && data_in==8'hff;
                end
                match<=match && byte_matches;
                if(offset>=18 && offset<=21) incoming_sequence<={incoming_sequence[23:0],data_in};
                if(offset==22) incoming_count<=data_in;
                if(offset>=24 && offset<536) features[write_bank*512+offset-24]<=data_in;
                if(last_in) begin
                    if(match && byte_matches && (local_match || broadcast_match) && incoming_count>=1 &&
                       incoming_count<=32 && offset+1>=24+incoming_count*16) begin
                        full[write_bank]<=1;counts[write_bank]<=6'(incoming_count);
                        sequences[write_bank]<=incoming_sequence;write_bank<=!write_bank;
                    end
                    offset<=0;match<=1;local_match<=1;broadcast_match<=1;
                end else offset<=offset+1;
            end
        end
    end
endmodule
