// Two score banks collect the next batch while the preceding reply is sent.
module ethernet_batch_result_overlap #(parameter integer ROUTING_THRESHOLD=-1833) (
    input logic clk, reset, enabled,
    input logic [64:0] result_in,
    input logic result_valid,
    output logic result_ready,
    output logic [7:0] data_out,
    output logic valid_out, last_out,
    input logic ready_in, tx_done, tx_underflow, tx_busy, buffer_busy,
    output logic sent, error
);
    typedef enum logic [1:0] {IDLE, SEND, WAIT_TX} state_t;
    state_t state;
    logic [1:0] full;
    logic write_bank, read_bank;
    logic signed [31:0] scores [0:63];
    logic [31:0] sequences [0:1];
    logic [5:0] counts [0:1];
    logic [4:0] write_index, record_index;
    logic [7:0] index, frame_length;
    logic [2:0] field_index;
    logic signed [31:0] current_score;
    assign result_ready=enabled && !full[write_bank];
    assign valid_out=state==SEND;
    assign frame_length=8'(22+counts[read_bank]*6);
    assign last_out=valid_out && index==frame_length-1;
    always_comb begin
        current_score=scores[{read_bank,record_index}];
        data_out=0;
        case(index)
            0,1,2,3,4,5:data_out=8'hff;
            6:data_out=2;
            11:data_out=1;
            12:data_out=8'h88;
            13:data_out=8'hb5;
            14:data_out=1;
            15:data_out=2;
            16,17,18,19:data_out=sequences[read_bank][31-(index-16)*8 -: 8];
            20:data_out={2'b0,counts[read_bank]};
            default:if(index>=22) begin
                if(field_index<4)data_out=current_score[31-field_index*8 -: 8];
                else if(field_index==4)data_out=8'(current_score>=0);
                else data_out=8'(current_score>=ROUTING_THRESHOLD);
            end
        endcase
    end
    always_ff @(posedge clk) begin
        if(reset) begin
            state<=IDLE;full<=0;write_bank<=0;read_bank<=0;write_index<=0;index<=0;
            record_index<=0;field_index<=0;sent<=0;error<=0;
        end else begin
            if(result_valid && result_ready) begin
                scores[{write_bank,write_index}]<=result_in[31:0];
                if(write_index==0)sequences[write_bank]<=result_in[63:32];
                if(result_in[64]) begin
                    counts[write_bank]<={1'b0,write_index}+1'b1;
                    full[write_bank]<=1;write_bank<=!write_bank;write_index<=0;
                end else write_index<=write_index+1;
            end
            case(state)
                IDLE:if(full[read_bank] && !tx_busy && !buffer_busy) begin
                    index<=0;record_index<=0;field_index<=0;state<=SEND;
                end
                SEND:if(ready_in) begin
                    if(last_out)state<=WAIT_TX;
                    else begin
                        index<=index+1;
                        // Six bytes per score record, advanced only on acceptance.
                        // Avoid division/modulo in the byte-selection timing path.
                        if(index>=22) begin
                            if(field_index==5) begin
                                field_index<=0;record_index<=record_index+1;
                            end else field_index<=field_index+1;
                        end
                    end
                end
                WAIT_TX:if(tx_done || tx_underflow) begin
                    full[read_bank]<=0;read_bank<=!read_bank;state<=IDLE;
                    if(tx_done)sent<=!sent;
                    if(tx_underflow)error<=1;
                end
                default:state<=IDLE;
            endcase
        end
    end
endmodule
