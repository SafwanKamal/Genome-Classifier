// nexys 4 ddr: periodic raw ethernet broadcast, 100 mbps full duplex
module ethernet_test_top_100M_config (
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
    wire clk_50MHz, clock_locked;
    wire reset_request = reset || !clock_locked;
    (* ASYNC_REG = "TRUE" *) logic [1:0] reset_sync_reg = 2'b11;
    wire core_reset = reset_sync_reg[1];

    // vivado clocking wizard: 100 mhz input, 50 mhz output
    clk_wiz_ethernet clock_generator (
        .clk_out1(clk_50MHz),
        .reset(reset),
        .locked(clock_locked),
        .clk_in1(clk_100MHz)
    );

    // phy samples halfway between our rising-edge data updates
    ODDR #(.DDR_CLK_EDGE("SAME_EDGE"), .INIT(1'b0), .SRTYPE("SYNC"))
    PHY_clock_forward (
        .C(clk_50MHz), .CE(1'b1), .D1(1'b0), .D2(1'b1),
        .Q(PHY_ref_clk), .R(1'b0), .S(1'b0)
    );

    // assert asynchronously; release on the 50 mhz clock
    always_ff @(posedge clk_50MHz or posedge reset_request) begin
        if (reset_request)
            reset_sync_reg <= 2'b11;
        else
            reset_sync_reg <= {reset_sync_reg[0], 1'b0};
    end

    localparam logic [27:0] PHY_RESET_CYCLES = 28'd2_500_000;
    localparam logic [27:0] MDIO_DELAY_CYCLES = 28'd50_000;
    localparam logic [27:0] STARTUP_CYCLES    = 28'd150_000_000;
    localparam logic [27:0] PERIOD_CYCLES    = 28'd50_000_000;
    typedef enum logic [2:0] {
        hold_PHY_reset, mdio_delay, mdio_configure, startup_wait,
        send_frame, wait_result, interval_wait
    } state_t;
    state_t state_reg, state_next;
    logic [27:0] timer_reg, timer_next;
    logic [5:0] index_reg, index_next;
    logic sent_reg, sent_next, error_reg, error_next;
    logic PHY_reset_n_reg, PHY_reset_n_next;
    logic [7:0] source_data, buffered_data;
    logic source_valid, source_last, source_ready;
    logic buffered_valid, buffered_last, TX_ready;
    logic buffer_busy, buffer_overflow, TX_busy, TX_done, TX_underflow;
    logic mdio_start, mdio_busy, mdio_done, mdio_out, mdio_drive_enable;

    // 60 bytes before fcs: broadcast dst, local src, experimental type,
    // followed by payload bytes 00..2d
    function automatic logic [7:0] frame_byte(input logic [5:0] index);
        case (index)
            0, 1, 2, 3, 4, 5: frame_byte = 8'hFF;
            6:                frame_byte = 8'h02;
            7, 8, 9, 10:      frame_byte = 8'h00;
            11:               frame_byte = 8'h01;
            12:               frame_byte = 8'h88;
            13:               frame_byte = 8'hB5;
            default:          frame_byte = {2'b00, index} - 8'd14;
        endcase
    endfunction

    ethernet_TX_buffer frame_buffer (
        .clk(clk_50MHz), .reset(core_reset),
        .data_in(source_data), .valid_in(source_valid),
        .last_in(source_last), .ready_out(source_ready),
        .data_out(buffered_data), .valid_out(buffered_valid),
        .last_out(buffered_last), .ready_in(TX_ready),
        .tx_done_in(TX_done), .tx_underflow_in(TX_underflow),
        .busy_out(buffer_busy), .overflow_out(buffer_overflow)
    );
    ethernet_TX transmitter (
        .clk(clk_50MHz), .reset(core_reset),
        .data_in(buffered_data), .valid_in(buffered_valid),
        .last_in(buffered_last), .ready_out(TX_ready),
        .rmii_txd(rmii_txd), .rmii_tx_en(rmii_tx_en),
        .busy_out(TX_busy), .done_out(TX_done),
        .underflow_out(TX_underflow)
    );
    phy_mdio_config PHY_configurator (
        .clk(clk_50MHz), .reset(core_reset), .start(mdio_start),
        .mdc(PHY_MDC), .mdio_out(mdio_out),
        .mdio_drive_enable(mdio_drive_enable), .busy(mdio_busy), .done(mdio_done)
    );

    always_ff @(posedge clk_50MHz or posedge core_reset) begin
        if (core_reset) begin
            state_reg <= hold_PHY_reset;
            timer_reg <= 0;
            index_reg <= 0;
            sent_reg <= 1'b0;
            error_reg <= 1'b0;
            PHY_reset_n_reg <= 1'b0;
        end else begin
            state_reg <= state_next;
            timer_reg <= timer_next;
            index_reg <= index_next;
            sent_reg <= sent_next;
            error_reg <= error_next;
            PHY_reset_n_reg <= PHY_reset_n_next;
        end
    end

    always_comb begin
        state_next = state_reg;
        timer_next = timer_reg;
        index_next = index_reg;
        sent_next = sent_reg;
        error_next = error_reg;
        PHY_reset_n_next = PHY_reset_n_reg;
        source_data = frame_byte(index_reg);
        source_valid = 1'b0;
        source_last = 1'b0;
        mdio_start = 1'b0;

        case (state_reg)
            hold_PHY_reset: begin
                // 50 ms reset with the reference clock running
                if (timer_reg == PHY_RESET_CYCLES - 1'b1) begin
                    timer_next = 0;
                    PHY_reset_n_next = 1'b1;
                    state_next = mdio_delay;
                end else
                    timer_next = timer_reg + 1'b1;
            end
            mdio_delay: begin
                // Give the PHY 1 ms after reset release before driving MDIO.
                if (timer_reg == MDIO_DELAY_CYCLES - 1'b1) begin
                    timer_next = 0;
                    state_next = mdio_configure;
                end else
                    timer_next = timer_reg + 1'b1;
            end
            mdio_configure: begin
                // start is a one-cycle request; do not retrigger after done
                mdio_start = !mdio_busy && !mdio_done;
                if (mdio_done) begin
                    timer_next = 0;
                    state_next = startup_wait;
                end
            end
            startup_wait: begin
                // Let the forced-speed link settle; this is not a link-status check.
                if (timer_reg == STARTUP_CYCLES - 1'b1) begin
                    timer_next = 0;
                    index_next = 0;
                    state_next = send_frame;
                end else
                    timer_next = timer_reg + 1'b1;
            end
            send_frame: begin
                source_valid = 1'b1;
                source_last = (index_reg == 6'd59);
                if (source_ready) begin
                    if (index_reg == 6'd59)
                        state_next = wait_result;
                    else
                        index_next = index_reg + 1'b1;
                end
            end
            wait_result: begin
                if (TX_done || TX_underflow) begin
                    timer_next = 0;
                    state_next = interval_wait;
                end
            end
            interval_wait: begin
                if (timer_reg == PERIOD_CYCLES - 1'b1) begin
                    if (!buffer_busy && !TX_busy) begin
                        timer_next = 0;
                        index_next = 0;
                        state_next = send_frame;
                    end
                end else
                    timer_next = timer_reg + 1'b1;
            end
            default: begin
                state_next = hold_PHY_reset;
                PHY_reset_n_next = 1'b0;
                timer_next = 0;
                index_next = 0;
            end
        endcase

        if (TX_done)
            sent_next = !sent_reg;
        if (TX_underflow || buffer_overflow)
            error_next = 1'b1;
        if (core_reset) begin
            source_valid = 1'b0;
            source_last = 1'b0;
        end
    end

    // // MDIO is open-drain at the board interface: drive low or release it.
    // // The LAN8720A/board pull-up supplies a logic one.
    // assign PHY_MDIO = (mdio_drive_enable && !mdio_out) ? 1'b0 : 1'bz;

    // Clause 22 writes actively drive both values. MDIO is tri-stated only
    // when the FPGA is not issuing a management transaction.
    assign PHY_MDIO = mdio_drive_enable ? mdio_out : 1'bz;
    assign PHY_reset_n = PHY_reset_n_reg;
    assign LED = {error_reg, sent_reg, PHY_reset_n_reg, clock_locked};
endmodule
