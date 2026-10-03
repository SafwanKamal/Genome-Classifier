// Aggregate <=32 sequential scores into one reply: version=1/type=2,
// base sequence BE32, count, reserved=0, then score BE32 + two flags per record.
module ethernet_batch_result #(parameter integer ROUTING_THRESHOLD=-1914) (
    input logic clk, reset, enabled,
    input logic [64:0] result_in,
    input logic result_valid,
    output logic result_ready,
    output logic [7:0] data_out,
    output logic valid_out, last_out,
    input logic ready_in, tx_done, tx_underflow,
    output logic sent, error
);
    typedef enum logic [1:0] {COLLECT, SEND, WAIT_TX} state_t;
    state_t state;
    logic signed [31:0] scores [0:31];
    logic [31:0] base_sequence;
    integer count, index, record_index, field_index, frame_length;
    logic signed [31:0] current_score;
    assign result_ready = enabled && state == COLLECT;
    assign valid_out = state == SEND;
    assign frame_length = 22 + count*6;
    assign last_out = valid_out && index == frame_length-1;
    always_comb begin
        record_index = index>=22 ? (index-22)/6 : 0;
        field_index = index>=22 ? (index-22)%6 : 0;
        current_score = scores[record_index];
        data_out = 0;
        case (index)
            0,1,2,3,4,5: data_out=8'hff;
            6: data_out=2;
            11: data_out=1;
            12: data_out=8'h88;
            13: data_out=8'hb5;
            14: data_out=1;
            15: data_out=2;
            16,17,18,19: data_out=base_sequence[31-(index-16)*8 -: 8];
            20: data_out=8'(count);
            default: if (index>=22) begin
                if (field_index<4) data_out=current_score[31-field_index*8 -: 8];
                else if (field_index==4) data_out=8'(current_score>=0);
                else data_out=8'(current_score>=ROUTING_THRESHOLD);
            end
        endcase
    end
    always_ff @(posedge clk) begin
        if (reset) begin state<=COLLECT; count<=0; index<=0; base_sequence<=0; sent<=0; error<=0; end
        else case (state)
            COLLECT: if (result_valid && result_ready) begin
                scores[count]<=result_in[31:0];
                if (count==0) base_sequence<=result_in[63:32];
                count<=count+1;
                if (result_in[64]) begin state<=SEND; index<=0; end
            end
            SEND: if (ready_in) begin
                if (last_out) state<=WAIT_TX;
                else index<=index+1;
            end
            WAIT_TX: if (tx_done || tx_underflow) begin
                state<=COLLECT; count<=0;
                if (tx_done) sent<=!sent;
                if (tx_underflow) error<=1;
            end
            default: state<=COLLECT;
        endcase
    end
endmodule
