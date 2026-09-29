module variant_triage_top_UART_ethernet #(
    parameter integer CLOCK_HZ         = 100_000_000,
    parameter integer BAUD_RATE        = 115_200,
    parameter integer FEATURE_NUMBER   = 16,
    parameter integer HIDDEN_NUMBER    = 8,
    parameter integer HIDDEN_MAC_LANES = 4
) (
    input  logic       clk,
    input  logic       reset,
    input  logic       rx,
    output logic       tx,

    output logic       PHY_ref_clk,
    output logic       PHY_reset_n,
    output logic       PHY_MDC,
    inout  wire        PHY_MDIO,

    output logic [1:0] rmii_txd,
    output logic       rmii_tx_en,

    output logic [3:0] LED
);

    `include "model_parameters.svh"

    localparam logic [27:0] PHY_RESET_CYCLES = 28'd2_500_000;
    localparam logic [27:0] STARTUP_CYCLES   = 28'd150_000_000;

    localparam logic [7:0] PROTOCOL_VERSION = 8'h01;
    localparam logic [7:0] MESSAGE_RESULT   = 8'h01;

    // 14-byte Ethernet header + 16-byte result payload.
    localparam logic [5:0] FRAME_BYTE_COUNT = 6'd30;
    localparam logic [5:0] LAST_FRAME_INDEX =
        FRAME_BYTE_COUNT - 1'b1;

    typedef enum logic [2:0] {
        hold_PHY_reset,
        startup_wait,
        wait_score,
        send_frame,
        wait_result
    } state_t;

    logic [7:0] packet [0:FEATURE_NUMBER - 1];
    logic       packet_valid;

    logic               core_busy;
    logic               core_done;
    logic signed [31:0] core_score;

    logic               response_busy;
    logic signed [31:0] response [0:0];

    logic clk_phy_50MHz;
    logic clk_mac_50MHz;
    logic clock_locked;

    logic reset_request;
    logic ethernet_reset;

    (* ASYNC_REG = "TRUE" *)
    logic [1:0] reset_sync_reg = 2'b11;

    logic               score_source_ready;
    logic signed [31:0] ethernet_score;
    logic               ethernet_score_valid;
    logic               ethernet_score_consumed;

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

    state_t state_reg;
    state_t state_next;

    logic [27:0] timer_reg;
    logic [27:0] timer_next;

    logic [5:0] index_reg;
    logic [5:0] index_next;

    logic [31:0] sequence_reg;
    logic [31:0] sequence_next;

    logic signed [31:0] packet_score_reg;
    logic signed [31:0] packet_score_next;

    logic PHY_reset_n_reg;
    logic PHY_reset_n_next;

    logic sent_reg;
    logic sent_next;

    logic error_reg;
    logic error_next;

    assign response[0] = core_score;

    /*
     * UART input and classifier
     */

    UART_packet_RX #(
        .CLOCK_HZ          (CLOCK_HZ),
        .BAUD_RATE         (BAUD_RATE),
        .PACKET_BYTE_NUMBER(FEATURE_NUMBER)
    ) UART_packet_RX_inst (
        .clk         (clk),
        .reset       (reset),
        .rx          (rx),
        .packet      (packet),
        .packet_valid(packet_valid)
    );

    variant_triage_core #(
        .FEATURE_NUMBER   (FEATURE_NUMBER),
        .REQUANT_SHIFT    (MODEL_QSHIFT),
        .HIDDEN_NUMBER    (HIDDEN_NUMBER),
        .HIDDEN_MAC_LANES (HIDDEN_MAC_LANES),

        .HIDDEN_WEIGHT_FILE(
            "dense_hidden_weights.mem"
        ),

        .HIDDEN_BIAS_FILE(
            "dense_hidden_biases.mem"
        ),

        .OUTPUT_WEIGHT_FILE(
            "dense_output_weights.mem"
        ),

        .OUTPUT_BIAS_FILE(
            "dense_output_biases.mem"
        )
    ) variant_triage_core_inst (
        .clk    (clk),
        .reset  (reset),
        .start  (packet_valid),
        .feature(packet),
        .busy   (core_busy),
        .done   (core_done),
        .score  (core_score)
    );

    /*
     * Keep UART output enabled while Ethernet is verified.
     */

    UART_response_TX #(
        .CLOCK_HZ   (CLOCK_HZ),
        .BAUD_RATE  (BAUD_RATE),
        .DATA_NUMBER(1)
    ) UART_response_TX_inst (
        .clk   (clk),
        .reset (reset),
        .tx    (tx),
        .data32(response),
        .send  (core_done),
        .busy  (response_busy)
    );

    /*
     * Ethernet clocks
     *
     * clk_out1: 50 MHz, 0 degrees, PHY reference clock
     * clk_out2: 50 MHz, 180 degrees, MAC transmit clock
     */

    clk_wiz_ethernet clock_generator (
        .clk_out1(clk_phy_50MHz),
        .clk_out2(clk_mac_50MHz),
        .reset   (reset),
        .locked  (clock_locked),
        .clk_in1 (clk)
    );

    assign PHY_ref_clk = clk_phy_50MHz;

    /*
     * Asynchronously assert the Ethernet reset when the external
     * reset is active or the Clocking Wizard is not locked.
     * Deassert it synchronously in the 50 MHz MAC domain.
     */

    assign reset_request = reset || !clock_locked;
    assign ethernet_reset = reset_sync_reg[1];

    always_ff @(posedge clk_mac_50MHz or posedge reset_request) begin
        if (reset_request)
            reset_sync_reg <= 2'b11;
        else
            reset_sync_reg <= {
                reset_sync_reg[0],
                1'b0
            };
    end

    /*
     * One-entry score mailbox:
     * classifier domain, 100 MHz -> Ethernet domain, 50 MHz.
     */

    score_clock_domain_crosser #(
        .DATA_WIDTH(32)
    ) score_clock_domain_crosser_inst (
        .source_clk        (clk),
        .source_reset      (reset),
        .source_data       (core_score),
        .source_send       (core_done),
        .source_ready      (score_source_ready),

        .destination_clk   (clk_mac_50MHz),
        .destination_reset (ethernet_reset),
        .destination_data  (ethernet_score),
        .destination_valid (ethernet_score_valid),
        .destination_ready (ethernet_score_consumed)
    );

    /*
     * Result frame format after EtherType 0x88B5:
     *
     *   Byte 0:      protocol version
     *   Byte 1:      message type
     *   Bytes 2-5:   sequence number, big-endian
     *   Bytes 6-9:   signed score, big-endian
     *   Byte 10:     score >= 0 classification
     *   Byte 11:     score >= -276 deep-review route
     *   Bytes 12-15: reserved
     */

    function automatic logic [7:0] frame_byte(
        input logic [5:0]         index,
        input logic [31:0]        sequence_number,
        input logic signed [31:0] score
    );
        case (index)
            // Destination MAC: broadcast.
            0, 1, 2, 3, 4, 5:
                frame_byte = 8'hFF;

            // Source MAC: 02:00:00:00:00:01.
            6:
                frame_byte = 8'h02;

            7, 8, 9, 10:
                frame_byte = 8'h00;

            11:
                frame_byte = 8'h01;

            // Experimental EtherType: 0x88B5.
            12:
                frame_byte = 8'h88;

            13:
                frame_byte = 8'hB5;

            // Result payload.
            14:
                frame_byte = PROTOCOL_VERSION;

            15:
                frame_byte = MESSAGE_RESULT;

            16:
                frame_byte = sequence_number[31:24];

            17:
                frame_byte = sequence_number[23:16];

            18:
                frame_byte = sequence_number[15:8];

            19:
                frame_byte = sequence_number[7:0];

            20:
                frame_byte = score[31:24];

            21:
                frame_byte = score[23:16];

            22:
                frame_byte = score[15:8];

            23:
                frame_byte = score[7:0];

            24:
                frame_byte =
                    (score >= 32'sd0) ? 8'h01 : 8'h00;

            25:
                frame_byte =
                    (score >= -32'sd276) ? 8'h01 : 8'h00;

            default:
                frame_byte = 8'h00;
        endcase
    endfunction

    /*
     * Ethernet frame buffer and RMII transmitter.
     */

    ethernet_TX_buffer frame_buffer (
        .clk             (clk_mac_50MHz),
        .reset           (ethernet_reset),

        .data_in         (source_data),
        .valid_in        (source_valid),
        .last_in         (source_last),
        .ready_out       (source_ready),

        .data_out        (buffered_data),
        .valid_out       (buffered_valid),
        .last_out        (buffered_last),
        .ready_in        (TX_ready),

        .tx_done_in      (TX_done),
        .tx_underflow_in (TX_underflow),

        .busy_out        (buffer_busy),
        .overflow_out    (buffer_overflow)
    );

    ethernet_TX transmitter (
        .clk           (clk_mac_50MHz),
        .reset         (ethernet_reset),

        .data_in       (buffered_data),
        .valid_in      (buffered_valid),
        .last_in       (buffered_last),
        .ready_out     (TX_ready),

        .rmii_txd      (rmii_txd),
        .rmii_tx_en    (rmii_tx_en),

        .busy_out      (TX_busy),
        .done_out      (TX_done),
        .underflow_out (TX_underflow)
    );

    /*
     * Packet controller registers.
     */

    always_ff @(posedge clk_mac_50MHz or posedge ethernet_reset) begin
        if (ethernet_reset) begin
            state_reg       <= hold_PHY_reset;
            timer_reg       <= '0;
            index_reg       <= '0;
            sequence_reg    <= '0;
            packet_score_reg <= '0;
            PHY_reset_n_reg <= 1'b0;
            sent_reg        <= 1'b0;
            error_reg       <= 1'b0;
        end else begin
            state_reg       <= state_next;
            timer_reg       <= timer_next;
            index_reg       <= index_next;
            sequence_reg    <= sequence_next;
            packet_score_reg <= packet_score_next;
            PHY_reset_n_reg <= PHY_reset_n_next;
            sent_reg        <= sent_next;
            error_reg       <= error_next;
        end
    end

    /*
     * Packet controller next-state logic.
     */

    always_comb begin
        state_next        = state_reg;
        timer_next        = timer_reg;
        index_next        = index_reg;
        sequence_next     = sequence_reg;
        packet_score_next = packet_score_reg;
        PHY_reset_n_next  = PHY_reset_n_reg;
        sent_next         = sent_reg;
        error_next        = error_reg;

        source_data = frame_byte(
            index_reg,
            sequence_reg,
            packet_score_reg
        );

        source_valid = 1'b0;
        source_last  = 1'b0;

        ethernet_score_consumed = 1'b0;

        case (state_reg)
            hold_PHY_reset: begin
                if (timer_reg == PHY_RESET_CYCLES - 1'b1) begin
                    timer_next       = '0;
                    PHY_reset_n_next = 1'b1;
                    state_next       = startup_wait;
                end else begin
                    timer_next = timer_reg + 1'b1;
                end
            end

            startup_wait: begin
                if (timer_reg == STARTUP_CYCLES - 1'b1) begin
                    timer_next = '0;
                    state_next = wait_score;
                end else begin
                    timer_next = timer_reg + 1'b1;
                end
            end

            wait_score: begin
                if (
                    ethernet_score_valid &&
                    !buffer_busy &&
                    !TX_busy
                ) begin
                    packet_score_next = ethernet_score;
                    index_next        = '0;
                    state_next        = send_frame;
                end
            end

            send_frame: begin
                source_valid = 1'b1;
                source_last =
                    (index_reg == LAST_FRAME_INDEX);

                if (source_ready) begin
                    if (index_reg == LAST_FRAME_INDEX) begin
                        state_next = wait_result;
                    end else begin
                        index_next = index_reg + 1'b1;
                    end
                end
            end

            wait_result: begin
                if (TX_done) begin
                    ethernet_score_consumed = 1'b1;
                    sequence_next = sequence_reg + 1'b1;
                    sent_next     = !sent_reg;
                    state_next    = wait_score;
                end else if (TX_underflow) begin
                    ethernet_score_consumed = 1'b1;
                    error_next = 1'b1;
                    state_next = wait_score;
                end
            end

            default: begin
                state_next       = hold_PHY_reset;
                timer_next       = '0;
                index_next       = '0;
                PHY_reset_n_next = 1'b0;
            end
        endcase

        if (buffer_overflow)
            error_next = 1'b1;

        // A score arrived before the previous score was acknowledged.
        // if (core_done && !score_source_ready)
        //     error_next = 1'b1;
    end

    /*
     * Leave the LAN8720A in its board-default configuration.
     */

    assign PHY_MDC     = 1'b0;
    assign PHY_MDIO    = 1'bz;
    assign PHY_reset_n = PHY_reset_n_reg;

    assign LED = {
        error_reg,
        sent_reg,
        PHY_reset_n_reg,
        clock_locked
    };

endmodule