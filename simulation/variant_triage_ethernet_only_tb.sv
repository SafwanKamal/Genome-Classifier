`timescale 1ns/1ps
// Behavioral clock/ODDR models for simulation only; the board uses Xilinx IP.
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

module variant_triage_ethernet_only_tb;
    parameter integer HIDDEN_NUMBER=8, HIDDEN_MAC_LANES=4, ROUTING_THRESHOLD=-276;
    parameter string VECTOR_FILE="model_v2_h8_core_vectors.mem";
    parameter integer BRINGUP_SCORE=-124;
    logic clk=0, reset=1;
    always #5 clk=~clk;
    wire PHY_ref_clk, PHY_reset_n, PHY_MDC, PHY_MDIO;
    wire [1:0] rmii_txd;
    wire rmii_tx_en;
    wire [3:0] LED;
    logic [1:0] rmii_rxd=0;
    logic rmii_crs_dv=0, rmii_rx_er=0;
    variant_triage_top_ethernet #(
        .HIDDEN_NUMBER(HIDDEN_NUMBER), .HIDDEN_MAC_LANES(HIDDEN_MAC_LANES),
        .ROUTING_THRESHOLD(ROUTING_THRESHOLD),
        .PHY_RESET_CYCLES(8), .STARTUP_CYCLES(8)) dut (.*);
    logic [159:0] vectors [0:1000];
    logic [7:0] frame [0:59];
    logic [7:0] reply [0:59];
    logic [7:0] reply_data;
    logic reply_valid, reply_last, reply_good, reply_bad, reply_overflow;
    integer reply_offset=0, replies=0, starts=0;
    integer expected_vector=0;
    logic [31:0] expected_sequence;
    // Decode the actual outgoing RMII frame, including its Ethernet CRC.
    ethernet_RX loopback (
        .clk(dut.clk_mac_50MHz), .reset(dut.ethernet_reset),
        .rmii_rxd(rmii_txd), .rmii_crs_dv(rmii_tx_en), .rmii_rx_er(1'b0),
        .data_out(reply_data), .valid_out(reply_valid), .last_out(reply_last), .ready_in(1'b1),
        .good_frame_out(reply_good), .bad_frame_out(reply_bad), .overflow_out(reply_overflow)
    );
    always @(posedge clk) if (!dut.system_reset && dut.core_start) starts++;
    always @(posedge dut.clk_mac_50MHz) begin
        if (reply_bad || reply_overflow) $fatal(1,"invalid reply frame");
        if (reply_valid) begin
            reply[reply_offset]=reply_data;
            reply_offset++;
            if (reply_last) begin
                if (reply_offset!=60) $fatal(1,"wrong reply length");
                if ({reply[0],reply[1],reply[2],reply[3],reply[4],reply[5]} !== 48'hffffffffffff ||
                    {reply[6],reply[7],reply[8],reply[9],reply[10],reply[11]} !== 48'h020000000001)
                    $fatal(1,"wrong result MAC header");
                if ({reply[12],reply[13],reply[14],reply[15]} !== 32'h88b50101)
                    $fatal(1,"wrong result protocol");
                if ({reply[16],reply[17],reply[18],reply[19]} !== expected_sequence)
                    $fatal(1,"wrong reply sequence");
                if ({reply[20],reply[21],reply[22],reply[23]} !== vectors[expected_vector][31:0])
                    $fatal(1,"wrong classifier score");
                if (reply[24] !== (($signed(vectors[expected_vector][31:0])>=0) ? 8'd1:8'd0) ||
                    reply[25] !== (($signed(vectors[expected_vector][31:0])>=ROUTING_THRESHOLD) ? 8'd1:8'd0))
                    $fatal(1,"wrong result flags");
                for (int i=26;i<60;i++) if (reply[i]!==0) $fatal(1,"wrong padding");
                reply_offset=0;
                replies++;
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
        for (int i=0;i<4;i++) begin
            @(negedge dut.clk_rx_50MHz);
            rmii_rxd=value[i*2 +: 2]; rmii_crs_dv=1;
        end
    endtask
    task automatic ethernet_request(input int vector_index, input logic [31:0] seq,
        input bit arp, corrupt);
        logic [31:0] crc;
        for (int i=0;i<60;i++) frame[i]=0;
        for (int i=0;i<6;i++) frame[i]=8'hff;
        frame[6]=2; frame[11]=2;
        frame[12]=arp ? 8'h08 : 8'h88; frame[13]=arp ? 8'h06 : 8'hb5;
        frame[14]=8'h52; frame[15]=8'h58; frame[16]=8'h30; frame[17]=8'h31;
        for (int i=0;i<4;i++) frame[18+i]=seq[31-i*8 -: 8];
        for (int i=0;i<16;i++) frame[22+i]=vectors[vector_index][159-i*8 -: 8];
        repeat(6) begin @(negedge dut.clk_rx_50MHz); rmii_crs_dv=1; rmii_rxd=0; end
        repeat(7) send_byte(8'h55);
        send_byte(8'hd5);
        crc=32'hffffffff;
        for (int i=0;i<60;i++) begin send_byte(frame[i]); crc=crc_byte(crc,frame[i]); end
        crc=~crc;
        if (corrupt) crc^=1;
        for (int i=0;i<4;i++) send_byte(crc[i*8 +: 8]);
        @(negedge dut.clk_rx_50MHz); rmii_crs_dv=0; rmii_rxd=0;
        repeat(100) @(negedge dut.clk_rx_50MHz);
    endtask
    initial begin
        $readmemh(VECTOR_FILE,vectors,0,999);
        // The physical bring-up features; expected score depends on the model.
        for (int i=0;i<16;i++) vectors[1000][159-i*8 -: 8]=8'(i-8);
        vectors[1000][31:0]=BRINGUP_SCORE;
        #150; reset=0;
        wait(PHY_reset_n); repeat(20) @(negedge clk);
        ethernet_request(0,7,1,0);
        ethernet_request(0,7,0,1);
        if(starts!=0 || replies!=0) $fatal(1,"ignored traffic launched classifier");
        expected_vector=0; expected_sequence=32'h12345678;
        ethernet_request(0,expected_sequence,0,0);
        wait(replies==1); wait(!dut.request_inflight && dut.score_source_ready);
        expected_vector=1; expected_sequence=32'habcdef01;
        ethernet_request(1,expected_sequence,0,0);
        wait(replies==2); wait(!dut.request_inflight && dut.score_source_ready);
        expected_vector=2; expected_sequence=32'h98765432;
        ethernet_request(2,expected_sequence,0,0);
        wait(replies==3);
        wait(!dut.request_inflight && dut.score_source_ready);
        expected_vector=1000; expected_sequence=1;
        ethernet_request(1000,1,0,0);
        wait(replies==4);
        if(starts!=4) $fatal(1,"wrong inference count");
        $display("PASS: Ethernet RX -> parser -> CDC -> classifier -> CDC -> RMII TX (Ethernet-only)");
        $finish;
    end
    initial begin #200000; $fatal(1,"timeout"); end
endmodule
