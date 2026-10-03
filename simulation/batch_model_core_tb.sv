`timescale 1ns/1ps
module batch_model_core_tb;
    logic clk=0, reset=1, start=0;
    always #5 clk=~clk;
    logic [7:0] feature [0:15];
    logic busy, done;
    logic signed [31:0] score;
    logic [159:0] vectors [0:999];
    integer cycles, maximum_cycles=0;
    batch_model_core dut (.*);
    initial begin
        $readmemh("model_v4_test.mem", vectors, 0, 999);
        repeat(4) @(negedge clk);
        reset=0;
        for (int test_index=0; test_index<1000; test_index++) begin
            for (int i=0; i<16; i++) feature[i]=vectors[test_index][159-i*8 -: 8];
            start=1;
            @(negedge clk); start=0;
            cycles=0;
            while (!done && cycles<2500) begin @(negedge clk); cycles++; end
            if (!done || score !== $signed(vectors[test_index][31:0]))
                $fatal(1, "batch core mismatch at %0d: got %0d expected %0d", test_index, score, $signed(vectors[test_index][31:0]));
            if (cycles>maximum_cycles) maximum_cycles=cycles;
            @(negedge clk);
        end
        $display("PASS: 1000 batch core vectors; maximum cycles=%0d", maximum_cycles);
        $finish;
    end
endmodule
