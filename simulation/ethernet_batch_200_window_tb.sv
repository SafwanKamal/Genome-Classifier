`timescale 1ns/1ps
// These behavioral models are simulation-only; board builds use Clocking Wizard/ODDR.
module clk_wiz_ethernet_200(input clk_in1, reset, output logic locked,
    output logic clk_out1=0, clk_out2=0, clk_out3=0, clk_out4=0);
    initial begin locked=0; #100; locked=1; end
    always #2.5 clk_out4=~clk_out4;
    always #10 clk_out1=~clk_out1;
    initial begin #10; forever #10 clk_out2=~clk_out2; end
    initial begin #2; forever #10 clk_out3=~clk_out3; end
endmodule
module ODDR #(parameter DDR_CLK_EDGE="SAME_EDGE", INIT=0, SRTYPE="SYNC")
    (input C, CE, D1, D2, R, S, output Q);
    assign Q=C;
endmodule
module ethernet_batch_200_window_tb;
    logic clk=0, reset=1;
    always #5 clk=~clk;
    wire PHY_ref_clk, PHY_reset_n, PHY_MDC, PHY_MDIO;
    wire [1:0] rmii_txd;
    wire rmii_tx_en;
    wire [3:0] LED;
    logic [1:0] rmii_rxd=0;
    logic rmii_crs_dv=0, rmii_rx_er=0;
    variant_triage_top_ethernet_200 #(.BATCH_MODEL(1), .ROUTING_THRESHOLD(-1833),
        .PHY_RESET_CYCLES(8), .STARTUP_CYCLES(8)) dut (.*);
    logic [159:0] vectors [0:999];
    logic [7:0] frame [0:535], reply [0:213];
    logic [7:0] reply_data;
    logic reply_valid, reply_last, reply_good, reply_bad, reply_overflow;
    integer reply_offset=0, replies=0, starts=0, expected_count=0, expected_begin=0;
    logic [31:0] expected_sequence;
    integer expected_counts [0:127], expected_begins [0:127];
    logic [31:0] expected_sequences [0:127];
    integer total_requests=0, sent_requests=0;
    integer max_outstanding=0, rx_stall_cycles=0;
    bit saw_rx_both_full=0, saw_parser_both_full=0;
    logic allow_accept=0;
    always @(posedge dut.clk_rx_50MHz) begin
        if (!dut.rx_reset) begin
            if (dut.rx_overflow) $fatal(1,"window four overflowed RX frame storage");
            if (dut.receiver.full == 2'b11) saw_rx_both_full=1;
            if (dut.batch_parser.parser.full == 2'b11) saw_parser_both_full=1;
            if (dut.rx_valid && !dut.rx_ready) rx_stall_cycles++;
            if (sent_requests-replies > max_outstanding) max_outstanding=sent_requests-replies;
            if (sent_requests-replies > 4) $fatal(1,"host exceeded four credits");
        end
    end
    // Use two-bank capture: minimum-gap short replies can overlap decoding.
    ethernet_RX_overlap loopback (.clk(dut.clk_mac_50MHz), .reset(dut.ethernet_reset),
        .rmii_rxd(rmii_txd), .rmii_crs_dv(rmii_tx_en), .rmii_rx_er(1'b0),
        .data_out(reply_data), .valid_out(reply_valid), .last_out(reply_last), .ready_in(1'b1),
        .good_frame_out(reply_good), .bad_frame_out(reply_bad), .overflow_out(reply_overflow));
    integer core_clock_count=0, previous_start=0, gap_sum=0, gap_min=1000000, gap_max=0;
    always @(posedge dut.clk_core_200MHz) begin
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
        if (reply_bad || reply_overflow) $fatal(1,"bad reply CRC/overflow: bad=%b overflow=%b replies=%0d",reply_bad,reply_overflow,replies);
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
        // 1000 full-batch variants followed by 1000 mixed-batch variants.
        // The same golden vectors are reused; sequence bases remain unique.
        for (int pattern=0; pattern<2; pattern++) begin
            int begin_index, count;
            begin_index=0;
            while (begin_index<1000) begin
                count=32;
                if (pattern==1) begin
                    case (total_requests % 7)
                        2: count=3;
                        3: count=1;
                        4: count=1;
                        5: count=7;
                        default: count=32;
                    endcase
                end
                if (count>1000-begin_index) count=1000-begin_index;
                expected_counts[total_requests]=count;
                expected_begins[total_requests]=begin_index;
                expected_sequences[total_requests]=32'h12340000+pattern*1000+begin_index;
                begin_index+=count;
                total_requests++;
            end
        end
        // Hold compute so the first four requests fill every available request
        // slot. This exercises RX backpressure rather than only lucky timing.
        force dut.classifier_request_ready = allow_accept && !dut.request_inflight &&
            !dut.core_busy && dut.score_source_ready;
        while (sent_requests<total_requests) begin
            wait(sent_requests-replies<4);
            send_batch(expected_begins[sent_requests],expected_counts[sent_requests],0,
                       expected_sequences[sent_requests],0,0);
            sent_requests++;
            if (sent_requests==4) begin
                if (!saw_rx_both_full || !saw_parser_both_full || rx_stall_cycles==0)
                    $fatal(1,"did not exercise all four request slots/backpressure");
                allow_accept=1;
                // Release after the first accepted request changes the natural
                // ready expression; do not force an output-port variable busy.
                @(negedge dut.clk_core_200MHz);
                @(negedge dut.clk_core_200MHz);
                release dut.classifier_request_ready;
            end
        end
        wait(replies==total_requests);
        if (starts!=2000 || LED[3] || max_outstanding!=4)
            $fatal(1,"window four inference/error/coverage failure");
        $display("COVERAGE: %0d replies, outstanding max=%0d, RX stall cycles=%0d; both banks full in RX and parser",replies,max_outstanding,rx_stall_cycles);
        $display("PASS: 200 MHz window four; 2000 exact scores; full and mixed batches, sustained credits and RX backpressure");
        $finish;
    end
    initial begin #15000000; $fatal(1,"batch test timeout: sent=%0d replies=%0d starts=%0d busy=%b inflight=%b valid=%b ready=%b corestate=%0d RXfull=%b parserfull=%b",sent_requests,replies,starts,dut.core_busy,dut.request_inflight,dut.classifier_request_valid,dut.classifier_request_ready,dut.batch_core.core.state,dut.receiver.full,dut.batch_parser.parser.full); end
endmodule
