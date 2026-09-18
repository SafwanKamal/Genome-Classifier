`timescale 1ns / 1ps

module phy_mdio_config_tb;
    logic clk = 1'b0;
    logic reset = 1'b1;
    logic start = 1'b0;
    logic mdc, mdio_out, mdio_drive_enable, busy, done;
    tri1 mdio_line;
    logic [127:0] captured_bits = '0;
    integer bit_count = 0;

    localparam logic [127:0] EXPECTED_WRITES = {
        32'hFFFF_FFFF, 2'b01, 2'b01, 5'd1, 5'd4, 2'b10, 16'h0101,
        32'hFFFF_FFFF, 2'b01, 2'b01, 5'd1, 5'd0, 2'b10, 16'h1200
    };

    always #10 clk = ~clk;
    // assign mdio_line = (mdio_drive_enable && !mdio_out) ? 1'b0 : 1'bz;
    assign mdio_line = mdio_drive_enable ? mdio_out : 1'bz;

    phy_mdio_config #(.MDC_HALF_PERIOD_CYCLES(2)) DUT (
        .clk(clk), .reset(reset), .start(start), .mdc(mdc),
        .mdio_out(mdio_out), .mdio_drive_enable(mdio_drive_enable),
        .busy(busy), .done(done)
    );

    always @(posedge mdc) begin
        if (mdio_drive_enable) begin
            captured_bits <= {captured_bits[126:0], mdio_line};
            bit_count <= bit_count + 1;
        end
    end

    initial begin
        repeat (3) @(posedge clk);
        reset = 1'b0;
        @(posedge clk);
        start = 1'b1;
        @(posedge clk);
        start = 1'b0;

        @(posedge done);
        #1;
        if (bit_count != 128)
            $fatal(1, "expected 128 MDIO bits, got %0d", bit_count);
        if (captured_bits !== EXPECTED_WRITES)
            $fatal(1, "wrong MDIO writes: %h, expected %h", captured_bits, EXPECTED_WRITES);
        $display("PASS: advertised 100 Mbps full duplex and restarted auto-negotiation");
        $finish;
    end
endmodule
