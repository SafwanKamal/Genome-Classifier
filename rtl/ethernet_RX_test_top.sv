// Standalone Nexys 4 DDR receive bring-up. LEDs show PHY reset release,
// accepted frames, rejected frames and frames dropped while draining the RAM.
module ethernet_RX_test_top (
    input  logic       clk_100MHz,
    input  logic       reset,
    output logic       PHY_ref_clk,
    output logic       PHY_reset_n,
    output logic       PHY_MDC,
    inout  wire        PHY_MDIO,
    input logic [1:0] rmii_rxd,
    input logic       rmii_crs_dv,
    input logic       rmii_rx_er,
    output logic [1:0] rmii_txd,
    output logic       rmii_tx_en,
    output logic [3:0] LED
);
    (* keep = "true" *) logic clk_phy_50MHz;
    logic clk_phy_ref_50MHz, clock_locked;
    logic reset_request;
    (* mark_debug = "true" *) logic rx_reset;
    (* ASYNC_REG = "TRUE" *) logic [1:0] reset_sync_reg = 2'b11;
    logic [21:0] reset_timer_reg;
    // Vivado Set Up Debug can attach an ILA to these nets after synthesis.
    (* mark_debug = "true" *) logic [7:0] data_out;
    (* mark_debug = "true" *) logic valid_out, last_out;
    (* mark_debug = "true" *) logic good_frame, bad_frame, overflow;
    logic good_seen_reg, bad_seen_reg, overflow_seen_reg;

    clk_wiz_ethernet clock_generator (
        .clk_out1(clk_phy_ref_50MHz),
        .clk_out2(clk_phy_50MHz),
        .reset(reset), .locked(clock_locked), .clk_in1(clk_100MHz)
    );
    // Forward the clock through its dedicated output register instead of
    // fabric routing, reducing skew between PHY REF_CLK and RX input flops.
    ODDR #(.DDR_CLK_EDGE("SAME_EDGE"), .INIT(1'b0), .SRTYPE("SYNC"))
    PHY_clock_forward (
        .C(clk_phy_ref_50MHz), .CE(1'b1), .D1(1'b1), .D2(1'b0),
        .Q(PHY_ref_clk), .R(1'b0), .S(1'b0)
    );
    // RX test Clocking Wizard: out1 = 50 MHz/0 degrees, out2 = 50 MHz/36
    // degrees (2 ns later). This centers sampling in the PHY data-valid window.
    assign reset_request = reset || !clock_locked;
    assign rx_reset = reset_sync_reg[1];
    assign PHY_MDC = 1'b0;
    assign PHY_MDIO = 1'bz;
    assign rmii_txd = 2'b00;
    assign rmii_tx_en = 1'b0;
    assign LED = {overflow_seen_reg, bad_seen_reg,
                  good_seen_reg, PHY_reset_n};

    always_ff @(posedge clk_phy_50MHz or posedge reset_request) begin
        if (reset_request)
            reset_sync_reg <= 2'b11;
        else
            reset_sync_reg <= {reset_sync_reg[0], 1'b0};
    end

    // Keep the PHY in reset for 50 ms after the MMCM locks.
    always_ff @(posedge clk_phy_50MHz or posedge rx_reset) begin
        if (rx_reset) begin
            reset_timer_reg <= '0;
            PHY_reset_n <= 1'b0;
            good_seen_reg <= 1'b0;
            bad_seen_reg <= 1'b0;
            overflow_seen_reg <= 1'b0;
        end else begin
            if (!PHY_reset_n) begin
                if (reset_timer_reg == 22'd2_499_999)
                    PHY_reset_n <= 1'b1;
                else
                    reset_timer_reg <= reset_timer_reg + 1'b1;
            end
            if (good_frame) good_seen_reg <= 1'b1;
            if (bad_frame) bad_seen_reg <= 1'b1;
            if (overflow) overflow_seen_reg <= 1'b1;
        end
    end

    ethernet_RX receiver (
        .clk(clk_phy_50MHz), .reset(rx_reset || !PHY_reset_n),
        .rmii_rxd(rmii_rxd), .rmii_crs_dv(rmii_crs_dv),
        .rmii_rx_er(rmii_rx_er),
        .data_out(data_out), .valid_out(valid_out),
        .ready_in(1'b1), .last_out(last_out),
        .good_frame_out(good_frame), .bad_frame_out(bad_frame),
        .overflow_out(overflow)
    );
endmodule
