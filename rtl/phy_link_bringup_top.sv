// Nexys 4 DDR LAN8720A physical-link bring-up only.
// No MDIO transactions and no Ethernet frame transmission.
module phy_link_bringup_top (
    input  logic clk_100MHz,
    input  logic reset,

    output wire  PHY_ref_clk,
    output logic PHY_reset_n,
    output wire [1:0] rmii_txd,
    output wire  rmii_tx_en,
    output wire  PHY_MDC,
    inout  wire  PHY_MDIO,

    output logic [3:0] LED
);

    localparam logic [27:0] PHY_RESET_CYCLES = 28'd2_500_000;

    wire clk_50MHz;
    wire clock_locked;
    wire reset_request;

    logic [27:0] reset_count_reg;
    logic [25:0] heartbeat_reg;
    logic PHY_reset_n_reg;

    assign reset_request = reset || !clock_locked;

    // Existing Clocking Wizard: 100 MHz input, 50 MHz output.
    clk_wiz_ethernet clock_generator (
        .clk_out1(clk_50MHz),
        .reset(reset),
        .locked(clock_locked),
        .clk_in1(clk_100MHz)
    );

    // Direct, continuous 50 MHz clock for PHY-only validation.
    assign PHY_ref_clk = clk_50MHz;

    always_ff @(posedge clk_50MHz or posedge reset_request) begin
        if (reset_request) begin
            reset_count_reg <= '0;
            heartbeat_reg <= '0;
            PHY_reset_n_reg <= 1'b0;
        end else begin
            heartbeat_reg <= heartbeat_reg + 1'b1;

            if (!PHY_reset_n_reg) begin
                if (reset_count_reg == PHY_RESET_CYCLES - 1'b1) begin
                    reset_count_reg <= '0;
                    PHY_reset_n_reg <= 1'b1;
                end else begin
                    reset_count_reg <= reset_count_reg + 1'b1;
                end
            end
        end
    end

    // No management or RMII TX activity in this test.
    assign PHY_reset_n = PHY_reset_n_reg;
    assign PHY_MDC = 1'b0;
    assign PHY_MDIO = 1'bz;
    assign rmii_txd = 2'b00;
    assign rmii_tx_en = 1'b0;

    // LED[0]: Clocking Wizard locked
    // LED[1]: PHY reset released
    // LED[2]: FPGA heartbeat
    // LED[3]: always off
    assign LED = {1'b0, heartbeat_reg[25], PHY_reset_n_reg, clock_locked};

endmodule