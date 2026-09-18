// one-time Clause 22 MDIO configuration for the Nexys 4 DDR LAN8720A PHY
// Advertises only 100 Mbps full duplex, then restarts auto-negotiation.
module phy_mdio_config #(
    // Nexys 4 DDR straps LAN8720A PHYAD[4:0] to 00001.
    parameter logic [4:0] PHY_ADDRESS = 5'd1,
    // MDC maximum is 2.5 MHz.  Ten 50 MHz clocks per half period gives 2.5 MHz.
    parameter int unsigned MDC_HALF_PERIOD_CYCLES = 10
) (
    input  logic clk,
    input  logic reset,
    input  logic start,
    output logic mdc,
    output logic mdio_out,
    output logic mdio_drive_enable,
    output logic busy,
    output logic done
);

    localparam logic [15:0] ANAR_100_FULL_ONLY = 16'h0101;
    localparam logic [15:0] BMCR_RESTART_AUTONEG = 16'h1200;
    // Each Clause 22 write is: preamble, start, opcode, PHY address, register,
    // turnaround, and data. ANAR must be written before the BMCR restart request.
    localparam logic [63:0] MDIO_WRITE_ANAR = {
        32'hFFFF_FFFF, 2'b01, 2'b01, PHY_ADDRESS, 5'd4, 2'b10,
        ANAR_100_FULL_ONLY
    };
    localparam logic [63:0] MDIO_WRITE_BMCR = {
        32'hFFFF_FFFF, 2'b01, 2'b01, PHY_ADDRESS, 5'd0, 2'b10,
        BMCR_RESTART_AUTONEG
    };
    localparam int unsigned COUNTER_WIDTH = $clog2(MDC_HALF_PERIOD_CYCLES);

    typedef enum logic {idle, transfer} state_t;
    state_t state_reg;
    logic [COUNTER_WIDTH-1:0] half_period_count_reg;
    logic [7:0] bit_index_reg;

    function automatic logic transaction_bit(input logic [7:0] bit_index);
        if (bit_index < 8'd64)
            transaction_bit = MDIO_WRITE_ANAR[7'd63 - bit_index];
        else
            transaction_bit = MDIO_WRITE_BMCR[8'd127 - bit_index];
    endfunction

    // All bits in both writes are driven by the MAC, including write turnaround.
    assign mdio_drive_enable = (state_reg == transfer);
    assign mdio_out = transaction_bit(bit_index_reg);
    assign busy = (state_reg == transfer);

    always_ff @(posedge clk) begin
        if (reset) begin
            state_reg <= idle;
            half_period_count_reg <= '0;
            bit_index_reg <= '0;
            mdc <= 1'b0;
            done <= 1'b0;
        end else begin
            done <= 1'b0;

            case (state_reg)
                idle: begin
                    mdc <= 1'b0;
                    half_period_count_reg <= '0;
                    bit_index_reg <= '0;
                    if (start)
                        state_reg <= transfer;
                end

                transfer: begin
                    if (half_period_count_reg == MDC_HALF_PERIOD_CYCLES - 1) begin
                        half_period_count_reg <= '0;

                        if (!mdc) begin
                            // MDIO was stable through the low half-cycle; create the sampling edge.
                            mdc <= 1'b1;
                        end else begin
                            // Advance only after the falling edge, so the next bit has a full
                            // low half-cycle of setup time before its sampling edge.
                            mdc <= 1'b0;
                            if (bit_index_reg == 8'd127) begin
                                state_reg <= idle;
                                done <= 1'b1;
                            end else begin
                                bit_index_reg <= bit_index_reg + 1'b1;
                            end
                        end
                    end else begin
                        half_period_count_reg <= half_period_count_reg + 1'b1;
                    end
                end

                default: state_reg <= idle;
            endcase
        end
    end
endmodule
