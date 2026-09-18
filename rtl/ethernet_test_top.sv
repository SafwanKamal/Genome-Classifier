// nexys 4 ddr: periodic raw ethernet broadcast, 100 mbps full duplex
module ethernet_test_top (
    input  logic       clk_100MHz,
    input  logic       reset,

    output wire        PHY_ref_clk,
    output logic       PHY_reset_n,
    output wire [1:0]  rmii_txd,
    output wire        rmii_tx_en,

    output wire        PHY_MDC,
    inout  wire        PHY_MDIO,

    output logic [3:0] LED
);

    wire clk_50MHz;
    wire clock_locked;
    wire reset_request;

    (* ASYNC_REG = "TRUE" *)
    logic [1:0] reset_sync_reg = 2'b11;

    wire core_reset;

    assign reset_request = reset || !clock_locked;
    assign core_reset = reset_sync_reg[1];

    // vivado clocking wizard: 100 mhz input, 50 mhz output
    clk_wiz_ethernet clock_generator (
        .clk_out1(clk_50MHz),
        .reset(reset),
        .locked(clock_locked),
        .clk_in1(clk_100MHz)
    );

    // phy samples halfway between our rising-edge data updates
    ODDR #(
        .DDR_CLK_EDGE("SAME_EDGE"),
        .INIT(1'b0),
        .SRTYPE("SYNC")
    ) PHY_clock_forward (
        .C(clk_50MHz),
        .CE(1'b1),
        .D1(1'b0),
        .D2(1'b1),
        .Q(PHY_ref_clk),
        .R(1'b0),
        .S(1'b0)
    );

    // assert asynchronously; release on the 50 mhz clock
    always_ff @(posedge clk_50MHz or posedge reset_request) begin
        if (reset_request)
            reset_sync_reg <= 2'b11;
        else
            reset_sync_reg <= {reset_sync_reg[0], 1'b0};
    end

    localparam logic [27:0] PHY_RESET_CYCLES = 28'd2_500_000;
    localparam logic [27:0] STARTUP_CYCLES   = 28'd150_000_000;
    localparam logic [27:0] PERIOD_CYCLES    = 28'd50_000_000;

    typedef enum logic [2:0] {
        hold_PHY_reset,
        startup_wait,
        send_frame,
        wait_result,
        interval_wait
    } state_t;

    state_t state_reg, state_next;

    logic [27:0] timer_reg, timer_next;
    logic [5:0] index_reg, index_next;

    logic sent_reg, sent_next;
    logic error_reg, error_next;
    logic PHY_reset_n_reg, PHY_reset_n_next;

    logic [7:0] source_data;
    logic       source_valid;
    logic       source_last;
    logic       source_ready;

    logic [7:0] buffered_data;
    logic       buffered_valid;
    logic       buffered_last;

    logic TX_ready;
    logic TX_busy;
    logic TX_done;
    logic TX_underflow;

    logic buffer_busy;
    logic buffer_overflow;

    // 60 bytes before fcs:
    // broadcast destination mac
    // locally administered source mac
    // experimental ethertype
    // payload bytes 00 through 2d
    function automatic logic [7:0] frame_byte(
        input logic [5:0] index
    );
        case (index)
            0, 1, 2, 3, 4, 5:
                frame_byte = 8'hFF;

            6:
                frame_byte = 8'h02;

            7, 8, 9, 10:
                frame_byte = 8'h00;

            11:
                frame_byte = 8'h01;

            12:
                frame_byte = 8'h88;

            13:
                frame_byte = 8'hB5;

            default:
                frame_byte = {2'b00, index} - 8'd14;
        endcase
    endfunction

    ethernet_TX_buffer frame_buffer (
        .clk(clk_50MHz),
        .reset(core_reset),

        .data_in(source_data),
        .valid_in(source_valid),
        .last_in(source_last),
        .ready_out(source_ready),

        .data_out(buffered_data),
        .valid_out(buffered_valid),
        .last_out(buffered_last),
        .ready_in(TX_ready),

        .tx_done_in(TX_done),
        .tx_underflow_in(TX_underflow),

        .busy_out(buffer_busy),
        .overflow_out(buffer_overflow)
    );

    ethernet_TX transmitter (
        .clk(clk_50MHz),
        .reset(core_reset),

        .data_in(buffered_data),
        .valid_in(buffered_valid),
        .last_in(buffered_last),
        .ready_out(TX_ready),

        .rmii_txd(rmii_txd),
        .rmii_tx_en(rmii_tx_en),

        .busy_out(TX_busy),
        .done_out(TX_done),
        .underflow_out(TX_underflow)
    );

    always_ff @(posedge clk_50MHz or posedge core_reset) begin
        if (core_reset) begin
            state_reg       <= hold_PHY_reset;
            timer_reg       <= 0;
            index_reg       <= 0;
            sent_reg        <= 1'b0;
            error_reg       <= 1'b0;
            PHY_reset_n_reg <= 1'b0;
        end else begin
            state_reg       <= state_next;
            timer_reg       <= timer_next;
            index_reg       <= index_next;
            sent_reg        <= sent_next;
            error_reg       <= error_next;
            PHY_reset_n_reg <= PHY_reset_n_next;
        end
    end

    always_comb begin
        state_next       = state_reg;
        timer_next       = timer_reg;
        index_next       = index_reg;
        sent_next        = sent_reg;
        error_next       = error_reg;
        PHY_reset_n_next = PHY_reset_n_reg;

        source_data  = frame_byte(index_reg);
        source_valid = 1'b0;
        source_last  = 1'b0;

        case (state_reg)

            hold_PHY_reset: begin
                // hold the phy in reset for 50 ms
                if (timer_reg == PHY_RESET_CYCLES - 1'b1) begin
                    timer_next       = 0;
                    PHY_reset_n_next = 1'b1;
                    state_next       = startup_wait;
                end else begin
                    timer_next = timer_reg + 1'b1;
                end
            end

            startup_wait: begin
                // allow time for ethernet auto-negotiation
                if (timer_reg == STARTUP_CYCLES - 1'b1) begin
                    timer_next = 0;
                    index_next = 0;
                    state_next = send_frame;
                end else begin
                    timer_next = timer_reg + 1'b1;
                end
            end

            send_frame: begin
                source_valid = 1'b1;
                source_last  = (index_reg == 6'd59);

                if (source_ready) begin
                    if (index_reg == 6'd59) begin
                        state_next = wait_result;
                    end else begin
                        index_next = index_reg + 1'b1;
                    end
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
                end else begin
                    timer_next = timer_reg + 1'b1;
                end
            end

            default: begin
                state_next       = hold_PHY_reset;
                PHY_reset_n_next = 1'b0;
                timer_next       = 0;
                index_next       = 0;
            end

        endcase

        if (TX_done)
            sent_next = !sent_reg;

        if (TX_underflow || buffer_overflow)
            error_next = 1'b1;

        if (core_reset) begin
            source_valid = 1'b0;
            source_last  = 1'b0;
        end
    end

    // retain the board's default phy auto-negotiation configuration
    assign PHY_MDC = 1'b0;
    assign PHY_MDIO = 1'bz;

    assign PHY_reset_n = PHY_reset_n_reg;

    // led[0]: clock locked
    // led[1]: phy reset released
    // led[2]: toggles after each completed transmission
    // led[3]: sticky underflow or overflow error
    assign LED = {
        error_reg,
        sent_reg,
        PHY_reset_n_reg,
        clock_locked
    };

endmodule