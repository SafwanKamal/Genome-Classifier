`timescale 1ns/1ps
module ethernet_request_RX_tb;
    logic clk = 0, reset = 1;
    always #10 clk = !clk;
    logic [7:0] data_in;
    logic valid_in = 0, last_in = 0, ready_out;
    logic request_valid, request_ready = 0;
    wire [31:0] sequence_out;
    wire [7:0] features_out [0:15];
    logic [7:0] frame [0:59];
    ethernet_request_RX dut (.*);

    task automatic make_frame(input bit broadcast);
        for (int i=0; i<60; i++) frame[i] = 0;
        for (int i=0; i<6; i++) frame[i] = broadcast ? 8'hff : 0;
        if (!broadcast) begin frame[0]=2; frame[5]=1; end
        frame[12]=8'h88; frame[13]=8'hb5;
        frame[14]=8'h52; frame[15]=8'h58;
        frame[16]=8'h30; frame[17]=8'h31;
        frame[18]=8'h12; frame[19]=8'h34;
        frame[20]=8'h56; frame[21]=8'h78;
        for (int i=0; i<16; i++) frame[22+i] = i-8;
    endtask

    task automatic send_frame(input int length, input bit accepted);
        for (int i=0; i<length; i++) begin
            @(negedge clk);
            if (!ready_out) $fatal(1,"unexpected input stall");
            if (request_valid) $fatal(1,"request published before end of frame");
            data_in=frame[i]; valid_in=1; last_in=(i==length-1);
            // Exercise input gaps as well as contiguous transfers.
            if (i%7==0) begin
                @(negedge clk); valid_in=0; last_in=0;
            end
        end
        @(negedge clk); valid_in=0; last_in=0;
        if (request_valid !== accepted) $fatal(1,"request acceptance mismatch");
        if (accepted) begin
            if (sequence_out !== 32'h12345678) $fatal(1,"sequence mismatch");
            for (int i=0; i<16; i++)
                if (features_out[i] !== frame[22+i]) $fatal(1,"feature %0d mismatch",i);
            // Hold a request while upstream offers another byte.
            data_in=8'haa; valid_in=1;
            repeat (4) begin
                @(negedge clk);
                if (ready_out || !request_valid || sequence_out !== 32'h12345678)
                    $fatal(1,"pending request was not held");
                for (int i=0; i<16; i++)
                    if (features_out[i] !== frame[22+i]) $fatal(1,"held feature changed");
            end
            valid_in=0; request_ready=1;
            @(negedge clk); request_ready=0;
            if (request_valid || !ready_out) $fatal(1,"request not consumed");
        end
    endtask

    initial begin
        repeat (3) @(negedge clk);
        reset=0;
        make_frame(1); send_frame(60,1);
        make_frame(0); send_frame(60,1);
        make_frame(1); frame[12]=8'h08; frame[13]=8'h06; send_frame(60,0); // ARP
        make_frame(1); frame[17]=8'h32; send_frame(60,0);
        make_frame(0); frame[5]=2; send_frame(60,0);
        make_frame(1); send_frame(37,0); // missing final feature
        make_frame(1); send_frame(15,0); // truncated header
        make_frame(1); send_frame(60,1); // recovery after ignored frames
        // Reset in the middle of a frame must abandon the partial request.
        @(negedge clk); valid_in=1; data_in=8'hff;
        @(negedge clk); reset=1; valid_in=0;
        @(negedge clk); reset=0;
        make_frame(1); send_frame(60,1);
        $display("all ethernet_request_RX tests passed");
        $finish;
    end
    initial begin #100000; $fatal(1,"timeout"); end
endmodule
