module variant_triage_top_ethernet #(
    parameter integer FEATURE_NUMBER   = 16,
    parameter integer HIDDEN_NUMBER    = 8,
    parameter integer HIDDEN_MAC_LANES = 4,
    parameter integer REQUANT_SHIFT = 4,
    parameter integer ROUTING_THRESHOLD = -276,
    parameter bit BATCH_MODEL = 0,
    parameter integer PHY_RESET_CYCLES = 2_500_000,
    parameter integer STARTUP_CYCLES = 150_000_000
) (
    input  logic       clk,
    input  logic       reset,
    output logic       PHY_ref_clk,
    output logic       PHY_reset_n,
    output logic       PHY_MDC,
    inout  wire        PHY_MDIO,

    output logic [1:0] rmii_txd,
    output logic       rmii_tx_en,
    input  logic [1:0] rmii_rxd,
    input  logic       rmii_crs_dv,
    input  logic       rmii_rx_er,

    output logic [3:0] LED
);

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

    logic [7:0] classifier_features [0:FEATURE_NUMBER-1];
    logic core_start, request_inflight;
    logic active_batch_last, request_batch_last;
    logic [31:0] active_sequence;
    logic clk_rx_50MHz, rx_reset, system_reset;
    (* ASYNC_REG = "TRUE" *) logic [1:0] rx_reset_sync = 2'b11;
    (* ASYNC_REG = "TRUE" *) logic [1:0] system_reset_sync = 2'b11;
    logic [7:0] rx_data;
    logic rx_valid, rx_last, rx_ready, rx_good, rx_bad, rx_overflow;
    logic [7:0] request_features [0:15];
    logic [31:0] request_sequence;
    logic request_valid, request_ready, request_mailbox_ready;
    logic [160:0] request_bundle, classifier_request;
    logic classifier_request_valid, classifier_request_ready;
    logic [64:0] ethernet_result;

    logic               core_busy;
    logic               core_done;
    logic signed [31:0] core_score;

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
    logic [7:0] single_data, batch_data;
    logic single_valid, single_last, single_consumed;
    logic batch_valid, batch_last, batch_consumed, batch_sent, batch_error;

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

    if (BATCH_MODEL) begin : batch_core
        batch_model_core core (.clk(clk), .reset(system_reset), .start(core_start),
            .feature(classifier_features), .busy(core_busy), .done(core_done), .score(core_score));
    end else begin : single_core
    variant_triage_core #(
        .FEATURE_NUMBER   (FEATURE_NUMBER),
        .REQUANT_SHIFT    (REQUANT_SHIFT),
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
        .reset  (system_reset),
        .start  (core_start),
        .feature(classifier_features),
        .busy   (core_busy),
        .done   (core_done),
        .score  (core_score)
    );
    end

    /*
     * Ethernet clocks
     *
     * clk_out1: 50 MHz, 0 degrees, PHY reference clock
     * clk_out2: 50 MHz, 180 degrees, MAC transmit clock
     * clk_out3: 50 MHz, 36 degrees, MAC receive clock
     */

    clk_wiz_ethernet clock_generator (
        .clk_out1(clk_phy_50MHz),
        .clk_out2(clk_mac_50MHz),
        .clk_out3(clk_rx_50MHz),
        .reset   (reset),
        .locked  (clock_locked),
        .clk_in1 (clk)
    );

    ODDR #(.DDR_CLK_EDGE("SAME_EDGE"), .INIT(1'b0), .SRTYPE("SYNC"))
    PHY_clock_forward (
        .C(clk_phy_50MHz), .CE(1'b1), .D1(1'b1), .D2(1'b0),
        .Q(PHY_ref_clk), .R(1'b0), .S(1'b0)
    );

    /*
     * Asynchronously assert the Ethernet reset when the external
     * reset is active or the Clocking Wizard is not locked.
     * Deassert it synchronously in the 50 MHz MAC domain.
     */

    assign reset_request = reset || !clock_locked;
    assign ethernet_reset = reset_sync_reg[1];
    assign system_reset = system_reset_sync[1];
    assign rx_reset = rx_reset_sync[1];

    always_ff @(posedge clk or posedge reset_request) begin
        if (reset_request) system_reset_sync <= 2'b11;
        else system_reset_sync <= {system_reset_sync[0], 1'b0};
    end
    // Release RX only after the PHY's reset interval has completed.
    wire rx_reset_request = reset_request || !PHY_reset_n;
    always_ff @(posedge clk_rx_50MHz or posedge rx_reset_request) begin
        if (rx_reset_request) rx_reset_sync <= 2'b11;
        else rx_reset_sync <= {rx_reset_sync[0], 1'b0};
    end

    ethernet_RX receiver (
        .clk(clk_rx_50MHz), .reset(rx_reset),
        .rmii_rxd(rmii_rxd), .rmii_crs_dv(rmii_crs_dv), .rmii_rx_er(rmii_rx_er),
        .data_out(rx_data), .valid_out(rx_valid), .last_out(rx_last), .ready_in(rx_ready),
        .good_frame_out(rx_good), .bad_frame_out(rx_bad), .overflow_out(rx_overflow)
    );
    if (BATCH_MODEL) begin : batch_parser
        ethernet_batch_request_RX parser (
            .clk(clk_rx_50MHz), .reset(rx_reset), .data_in(rx_data),
            .valid_in(rx_valid), .last_in(rx_last), .ready_out(rx_ready),
            .request_valid(request_valid), .request_ready(request_ready),
            .sequence_out(request_sequence), .features_out(request_features),
            .batch_last(request_batch_last));
    end else begin : single_parser
    assign request_batch_last = 1;
    ethernet_request_RX request_parser (
        .clk(clk_rx_50MHz), .reset(rx_reset),
        .data_in(rx_data), .valid_in(rx_valid), .last_in(rx_last), .ready_out(rx_ready),
        .request_valid(request_valid), .request_ready(request_ready),
        .sequence_out(request_sequence), .features_out(request_features)
    );
    end
    assign request_bundle[160] = request_batch_last;
    assign request_bundle[159:128] = request_sequence;
    for (genvar i=0; i<16; i++) begin : request_pack
        assign request_bundle[i*8 +: 8] = request_features[i];
    end
    assign request_ready = request_mailbox_ready;
    score_clock_domain_crosser #(.DATA_WIDTH(161)) request_mailbox (
        .source_clk(clk_rx_50MHz), .source_reset(rx_reset),
        .source_data(request_bundle), .source_send(request_valid), .source_ready(request_mailbox_ready),
        .destination_clk(clk), .destination_reset(system_reset),
        .destination_data(classifier_request), .destination_valid(classifier_request_valid),
        .destination_ready(classifier_request_ready)
    );

    // Reserve the result mailbox before accepting the next request.
    assign classifier_request_ready = !request_inflight && !core_busy && score_source_ready;
    assign core_start = classifier_request_valid && classifier_request_ready;
    for (genvar i=0; i<FEATURE_NUMBER; i++) begin : classifier_unpack
        assign classifier_features[i] = classifier_request[i*8 +: 8];
    end
    always_ff @(posedge clk or posedge system_reset) begin
        if (system_reset) begin
            request_inflight <= 0;
            active_sequence <= 0;
            active_batch_last <= 0;
        end else begin
            if (core_done) request_inflight <= 0;
            if (core_start) begin
                request_inflight <= 1;
                active_sequence <= classifier_request[159:128];
                active_batch_last <= classifier_request[160];
            end
        end
    end

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
        .DATA_WIDTH(65)
    ) score_clock_domain_crosser_inst (
        .source_clk        (clk),
        .source_reset      (system_reset),
        .source_data       ({active_batch_last, active_sequence, core_score}),
        .source_send       (core_done),
        .source_ready      (score_source_ready),

        .destination_clk   (clk_mac_50MHz),
        .destination_reset (ethernet_reset),
        .destination_data  (ethernet_result),
        .destination_valid (ethernet_score_valid),
        .destination_ready (ethernet_score_consumed)
    );
    assign ethernet_score = ethernet_result[31:0];

    if (BATCH_MODEL) begin : batch_reply
        ethernet_batch_result #(.ROUTING_THRESHOLD(ROUTING_THRESHOLD)) builder (
            .clk(clk_mac_50MHz), .reset(ethernet_reset), .enabled(state_reg == wait_score),
            .result_in(ethernet_result), .result_valid(ethernet_score_valid),
            .result_ready(batch_consumed), .data_out(batch_data),
            .valid_out(batch_valid), .last_out(batch_last), .ready_in(source_ready),
            .tx_done(TX_done), .tx_underflow(TX_underflow), .sent(batch_sent), .error(batch_error));
        assign source_data = batch_data;
        assign source_valid = batch_valid;
        assign source_last = batch_last;
        assign ethernet_score_consumed = batch_consumed;
    end else begin : single_reply
        assign source_data = single_data;
        assign source_valid = single_valid;
        assign source_last = single_last;
        assign ethernet_score_consumed = single_consumed;
        assign batch_sent = 0;
        assign batch_error = 0;
    end

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
                    (score >= ROUTING_THRESHOLD) ? 8'h01 : 8'h00;

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

        single_data = frame_byte(
            index_reg,
            sequence_reg,
            packet_score_reg
        );

        single_valid = 1'b0;
        single_last  = 1'b0;

        single_consumed = 1'b0;

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
                    !BATCH_MODEL && ethernet_score_valid &&
                    !buffer_busy &&
                    !TX_busy
                ) begin
                    packet_score_next = ethernet_score;
                    sequence_next     = ethernet_result[63:32];
                    index_next        = '0;
                    state_next        = send_frame;
                end
            end

            send_frame: begin
                single_valid = 1'b1;
                single_last =
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
                    single_consumed = 1'b1;
                    sent_next     = !sent_reg;
                    state_next    = wait_score;
                end else if (TX_underflow) begin
                    single_consumed = 1'b1;
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

    end

    /*
     * Leave the LAN8720A in its board-default configuration.
     */

    assign PHY_MDC     = 1'b0;
    assign PHY_MDIO    = 1'bz;
    assign PHY_reset_n = PHY_reset_n_reg;

    assign LED = {
        error_reg | batch_error,
        BATCH_MODEL ? batch_sent : sent_reg,
        PHY_reset_n_reg,
        clock_locked
    };

endmodule
