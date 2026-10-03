// Two frame banks allow CRC-checked delivery to overlap the next wire frame.
// Same RMII decoder, CRC convention and external interface as ethernet_RX.
module ethernet_RX_overlap #(parameter integer MAX_FRAME_BYTES=1518) (
    input logic clk, reset,
    input logic [1:0] rmii_rxd,
    input logic rmii_crs_dv, rmii_rx_er,
    output logic [7:0] data_out,
    output logic valid_out,
    input logic ready_in,
    output logic last_out, good_frame_out, bad_frame_out, overflow_out
);
    localparam integer WIDTH=$clog2(MAX_FRAME_BYTES+1);
    localparam integer DEPTH=1 << WIDTH;
    (* ram_style="block" *) logic [7:0] memory [0:2*DEPTH-1];
    logic [1:0] full;
    logic write_bank, read_bank, capturing, frame_error, delivering;
    logic [WIDTH-1:0] count, read_index, length [0:1], next_read;
    logic [7:0] rx_data;
    logic rx_valid, rx_start, rx_end, rx_error;
    logic [31:0] crc_value;
    rmii_RX decoder (.clk(clk),.reset(reset),.rmii_rxd(rmii_rxd),.rmii_crs_dv(rmii_crs_dv),
        .rmii_rx_er(rmii_rx_er),.data_out(rx_data),.valid_out(rx_valid),
        .start_out(rx_start),.end_out(rx_end),.error_out(rx_error));
    ethernet_CRC32 crc (.clk(clk),.reset(reset),.clear_in(rx_start),.data_in(rx_data),
        .enable_in(capturing && rx_valid),.crc_out(crc_value));
    assign valid_out=delivering;
    assign last_out=delivering && read_index==length[read_bank]-1;
    assign next_read=delivering ? read_index + ((ready_in && !last_out) ? 1'b1 : 1'b0) : 0;
    always_ff @(posedge clk) begin
        if (capturing && rx_valid && count<MAX_FRAME_BYTES)
            memory[{write_bank,count}] <= rx_data;
        data_out <= memory[{read_bank,next_read}];
    end
    always_ff @(posedge clk) begin
        if (reset) begin
            full<=0; write_bank<=0; read_bank<=0; capturing<=0;
            count<=0; read_index<=0; frame_error<=0; delivering<=0;
            good_frame_out<=0; bad_frame_out<=0; overflow_out<=0;
        end else begin
            good_frame_out<=0; bad_frame_out<=0; overflow_out<=0;
            if (!delivering && full[read_bank]) begin delivering<=1; read_index<=0; end
            if (delivering && ready_in) begin
                if (last_out) begin full[read_bank]<=0; delivering<=0; read_index<=0; read_bank<=!read_bank; end
                else read_index<=read_index+1;
            end
            if (rx_start) begin
                count<=0; frame_error<=0;
                if (full[write_bank]) overflow_out<=1;
                else capturing<=1;
            end
            if (capturing && rx_valid) begin
                if (count<MAX_FRAME_BYTES) count<=count+1;
                else frame_error<=1;
            end
            if (rx_end) begin
                capturing<=0;
                if (capturing) begin
                    if (rx_error || frame_error || count<64 || count>MAX_FRAME_BYTES || crc_value!=32'h2144df1c)
                        bad_frame_out<=1;
                    else begin
                        full[write_bank]<=1; length[write_bank]<=count-4;
                        write_bank<=!write_bank; good_frame_out<=1;
                    end
                end
            end
        end
    end
endmodule
