`timescale 1ns/1ps
// These behavioral models are simulation-only; board builds use Clocking Wizard/ODDR.
module clk_wiz_ethernet(input clk_in1, reset, output logic locked,
    output logic clk_out1=0, clk_out2=0, clk_out3=0);
    initial begin locked=0; #100; locked=1; end
    always #10 clk_out1=~clk_out1;
    initial begin #10; forever #10 clk_out2=~clk_out2; end
    initial begin #2; forever #10 clk_out3=~clk_out3; end
endmodule
module ODDR #(parameter DDR_CLK_EDGE="SAME_EDGE", INIT=0, SRTYPE="SYNC")
    (input C, CE, D1, D2, R, S, output Q);
    assign Q=C;
endmodule
module ethernet_batch_stream_tb;
    logic clk=0, reset=1;
    always #5 clk=~clk;
    wire PHY_ref_clk, PHY_reset_n, PHY_MDC, PHY_MDIO;
    wire [1:0] rmii_txd;
    wire rmii_tx_en;
    wire [3:0] LED;
    logic [1:0] rmii_rxd=0;
    logic rmii_crs_dv=0, rmii_rx_er=0;
    variant_triage_top_ethernet_stream #(.BATCH_MODEL(1), .ROUTING_THRESHOLD(-1833),
        .PHY_RESET_CYCLES(8), .STARTUP_CYCLES(8)) dut (.*);
    logic [159:0] vectors [0:999];
    logic [7:0] frame [0:535], reply [0:213];
    logic [7:0] reply_data;
    logic reply_valid, reply_last, reply_good, reply_bad, reply_overflow;
    integer reply_offset=0, replies=0, starts=0, expected_count=0, expected_begin=0;
    logic [31:0] expected_sequence;
    integer expected_counts [0:4] = '{32,32,3,1,7};
    integer expected_begins [0:4] = '{0,32,64,67,68};
    logic [31:0] expected_sequences [0:4] = '{32'h12340000,32'h12340020,32'h12340040,32'h12340043,32'h12340044};
    ethernet_RX loopback (.clk(dut.clk_mac_50MHz), .reset(dut.ethernet_reset),
        .rmii_rxd(rmii_txd), .rmii_crs_dv(rmii_tx_en), .rmii_rx_er(1'b0),
        .data_out(reply_data), .valid_out(reply_valid), .last_out(reply_last), .ready_in(1'b1),
        .good_frame_out(reply_good), .bad_frame_out(reply_bad), .overflow_out(reply_overflow));
    integer core_clock_count=0, previous_start=0, gap_sum=0, gap_min=1000000, gap_max=0;
    always @(posedge clk) begin
        core_clock_count++;
        if (!dut.system_reset && dut.core_start) begin
            // First 64 variants already queued in two back-to-back full batches.
            if(starts>=1 && starts<64) begin
                gap_sum+=core_clock_count-previous_start;
                if(core_clock_count-previous_start<gap_min)gap_min=core_clock_count-previous_start;
                if(core_clock_count-previous_start>gap_max)gap_max=core_clock_count-previous_start;
            end
            previous_start=core_clock_count;
            starts++;
        end
    end
    always @(posedge dut.clk_mac_50MHz) begin
        if (reply_bad || reply_overflow) $fatal(1,"bad reply CRC/overflow");
        if (reply_valid) begin
            reply[reply_offset]=reply_data;
            reply_offset++;
            if (reply_last) begin
                expected_count=expected_counts[replies];
                expected_begin=expected_begins[replies];
                expected_sequence=expected_sequences[replies];
                if (reply_offset != ((22+expected_count*6<60) ? 60 : 22+expected_count*6))
                    $fatal(1,"reply length mismatch");
                if ({reply[12],reply[13],reply[14],reply[15]} !== 32'h88b50102 ||
                    {reply[16],reply[17],reply[18],reply[19]} !== expected_sequence ||
                    reply[20] !== 8'(expected_count) || reply[21] !== 0)
                    $fatal(1,"batch reply header mismatch");
                if ({reply[6],reply[7],reply[8],reply[9],reply[10],reply[11]} !== 48'h020000000001)
                    $fatal(1,"reply source MAC mismatch");
                for (int i=0; i<expected_count; i++) begin
                    if ({reply[22+i*6],reply[23+i*6],reply[24+i*6],reply[25+i*6]} !== vectors[expected_begin+i][31:0])
                        $fatal(1,"batch score mismatch %0d", i);
                    if (reply[26+i*6] !== 8'($signed(vectors[expected_begin+i][31:0])>=0) ||
                        reply[27+i*6] !== 8'($signed(vectors[expected_begin+i][31:0])>=-1833))
                        $fatal(1,"batch flags mismatch");
                end
                replies++;
                reply_offset=0;
            end
        end
    end
    function automatic logic [31:0] crc_byte(input logic [31:0] prior, input logic [7:0] value);
        logic [31:0] work;
        work=prior ^ value;
        repeat(8) work=(work>>1) ^ (work[0] ? 32'hedb88320 : 0);
        return work;
    endfunction
    task automatic send_byte(input logic [7:0] value);
        for (int i=0; i<4; i++) begin
            @(negedge dut.clk_rx_50MHz); rmii_rxd=value[i*2 +: 2]; rmii_crs_dv=1;
        end
    endtask
    task automatic send_batch(input integer begin_index, count, length_adjust,
                               input logic [31:0] seq, input bit corrupt, wrong_marker);
        logic [31:0] crc;
        integer length;
        length=24+count*16+length_adjust;
        if (length<60) length=60;
        for (int i=0; i<536; i++) frame[i]=0;
        for (int i=0; i<6; i++) frame[i]=8'hff;
        frame[6]=2; frame[11]=2;
        frame[12]=8'h88; frame[13]=8'hb5;
        frame[14]="R"; frame[15]=wrong_marker ? "X" : "B"; frame[16]="0"; frame[17]="1";
        for (int i=0; i<4; i++) frame[18+i]=seq[31-i*8 -: 8];
        frame[22]=8'(count);
        for (int r=0; r<count && r<32; r++)
            for (int i=0; i<16; i++) frame[24+r*16+i]=vectors[begin_index+r][159-i*8 -: 8];
        repeat(6) begin @(negedge dut.clk_rx_50MHz); rmii_crs_dv=1; rmii_rxd=0; end
        repeat(7) send_byte(8'h55);
        send_byte(8'hd5); crc=32'hffffffff;
        for (int i=0; i<length; i++) begin send_byte(frame[i]); crc=crc_byte(crc,frame[i]); end
        crc=~crc;
        if (corrupt) crc^=1;
        for (int i=0; i<4; i++) send_byte(crc[i*8 +: 8]);
        @(negedge dut.clk_rx_50MHz); rmii_crs_dv=0; rmii_rxd=0;
        repeat(48) @(negedge dut.clk_rx_50MHz);
    endtask
    initial begin
        $readmemh("model_v4_test.mem",vectors,0,999);
        #150; reset=0;
        wait(PHY_reset_n);
        send_batch(0, 2, 0, 1, 1, 0); // CRC failure
        send_batch(0, 2, 0, 1, 0, 1); // RX01 is not RB01
        send_batch(0, 3, -1, 1, 0, 0); // truncated batch with valid CRC
        send_batch(0, 0, 0, 1, 0, 0); // invalid count
        if (starts || replies) $fatal(1,"invalid batch accepted");
        // Two full requests at the minimum Ethernet gap, without waiting for replies.
        send_batch(0,32,0,expected_sequences[0],0,0);
        send_batch(32,32,0,expected_sequences[1],0,0);
        wait(replies==2);
        send_batch(64,3,0,expected_sequences[2],0,0); wait(replies==3);
        // Wrap both banks with two partial requests and keep reply order.
        send_batch(67,1,0,expected_sequences[3],0,0);
        send_batch(68,7,0,expected_sequences[4],0,0);
        wait(replies==5);
        if (starts!=75 || LED[3]) $fatal(1,"wrong inference count/error LED");
        $display("TIMING: queued 64 variants start spacing min=%0d max=%0d mean=%0.2f core clocks",gap_min,gap_max,real'(gap_sum)/63);
        $display("PASS: Streamed Ethernet batches; 75 exact scores; overlapping frames and bank wrap");
        $finish;
    end
    initial begin #2000000; $fatal(1,"batch test timeout"); end
endmodule
