// Isolated 200 MHz target: same weights and 128 multipliers as the streamed core.
// Register reduction, accumulation, result capture, rounding and saturation separately.
module batch_model_200_core (
    input logic clk, reset, start,
    input logic [7:0] feature [0:15],
    output logic busy, done,
    output logic signed [31:0] score
);
    typedef enum logic [2:0] {IDLE, ISSUE, DRAIN, QUANTIZE, FINISH} state_t;
    state_t state;
    logic [1:0] layer;
    logic [5:0] issue_input;
    logic [2:0] issue_group;
    logic input_last, group_last;
    logic [9:0] weight_address;
    logic [4:0] bias_address;
    logic signed [7:0] activations [0:255], next_activations [0:255];
    logic signed [31:0] captured [0:31];
    logic signed [32:0] rounded [0:31];
    logic [31:0] nonpositive;
    logic signed [16:0] pair_a [0:31], pair_b [0:31];
    logic [1023:0] pair_bias, sum_bias;
    logic pair_valid, pair_first, pair_last, pair_final;
    logic sum_valid, sum_first, sum_last, sum_final;
    logic capture_valid, capture_final, round_valid, round_final;
    logic [2:0] pair_group, sum_group, capture_group, round_group;
    logic [1023:0] weight_rom [0:607], fetched_weight, weight_word;
    logic [1023:0] bias_rom [0:16], bias_word, read_bias, product_bias;
    logic signed [7:0] quarter_inputs [0:3][0:3], inputs [0:3];
    logic [1:0] fetched_quarter;
    logic signed [31:0] accumulator [0:31], accumulated [0:31];
    logic signed [17:0] lane_sum [0:31];
    logic fetch_valid, fetch_first, fetch_last, fetch_final;
    logic [2:0] fetch_group;
    logic read_valid, read_first, read_last, read_final;
    logic product_valid, product_first, product_last, product_final;
    logic [2:0] read_group, product_group;
    initial begin
        $readmemh("batch_weights.mem", weight_rom);
        $readmemh("batch_biases.mem", bias_rom);
    end
    always_comb begin
        input_last = layer == 0 ? issue_input == 3 : issue_input == 63;
        group_last = layer == 2 || issue_group == 7;
        case (layer)
            0: begin
                weight_address = {5'b0, issue_group, issue_input[1:0]};
                bias_address = {2'b0, issue_group};
            end
            1: begin
                weight_address = 10'd32 + {1'b0, issue_group, issue_input};
                bias_address = 5'd8 + issue_group;
            end
            default: begin
                weight_address = 10'd544 + issue_input;
                bias_address = 16;
            end
        endcase
    end
    always_ff @(posedge clk) begin
        fetched_weight <= weight_rom[weight_address];
        weight_word <= fetched_weight;
        bias_word <= bias_rom[bias_address];
        read_bias <= bias_word;
        product_bias <= read_bias;
        pair_bias <= product_bias;
        sum_bias <= pair_bias;
        if (reset) begin
            fetch_valid <= 0; read_valid <= 0; product_valid <= 0; pair_valid <= 0; sum_valid <= 0;
            capture_valid <= 0; round_valid <= 0;
        end
        else begin
            fetch_valid <= state == ISSUE;
            fetch_first <= issue_input == 0;
            fetch_last <= input_last;
            fetch_final <= input_last && group_last;
            fetch_group <= issue_group;
            read_valid <= fetch_valid; read_first <= fetch_first;
            read_last <= fetch_last; read_final <= fetch_final; read_group <= fetch_group;
            product_valid <= read_valid;
            product_first <= read_first;
            product_last <= read_last;
            product_final <= read_final;
            product_group <= read_group;
            pair_valid <= product_valid; pair_first <= product_first;
            pair_last <= product_last; pair_final <= product_final; pair_group <= product_group;
            sum_valid <= pair_valid; sum_first <= pair_first;
            sum_last <= pair_last; sum_final <= pair_final; sum_group <= pair_group;
            capture_valid <= sum_valid && sum_last;
            capture_final <= sum_final; capture_group <= sum_group;
            round_valid <= capture_valid; round_final <= capture_final; round_group <= capture_group;
        end
    end
    // Split the 64-way activation selection across the existing two read stages.
    // Each quarter selects 16 groups first; the next stage selects one quarter.
    always_ff @(posedge clk) fetched_quarter <= issue_input[5:4];
    for (genvar q=0; q<4; q++) begin : input_quarter
        for (genvar i=0; i<4; i++) begin : input_register
            always_ff @(posedge clk)
                quarter_inputs[q][i] <= activations[{2'(q), issue_input[3:0], 2'(i)}];
        end
    end
    for (genvar i=0; i<4; i++) begin : selected_input
        always_ff @(posedge clk) inputs[i] <= quarter_inputs[fetched_quarter][i];
    end
    for (genvar o=0; o<32; o++) begin : output_lane
        wire signed [15:0] product [0:3];
        for (genvar i=0; i<4; i++) begin : input_lane
            wire signed [7:0] weight_value = weight_word[(o*4+i)*8 +: 8];
            (* use_dsp = "yes" *) logic signed [15:0] multiplied;
            always_ff @(posedge clk) multiplied <= inputs[i] * weight_value;
            assign product[i] = multiplied;
        end
        // Balanced reduction: two registered pair sums, then one registered total.
        always_ff @(posedge clk) begin
            pair_a[o] <= {product[0][15],product[0]} + {product[1][15],product[1]};
            pair_b[o] <= {product[2][15],product[2]} + {product[3][15],product[3]};
            lane_sum[o] <= {pair_a[o][16],pair_a[o]} + {pair_b[o][16],pair_b[o]};
            if (sum_valid) accumulator[o] <= accumulated[o];
            // Capture the completed accumulator a cycle after its final addition.
            if (capture_valid) captured[o] <= accumulator[o];
            if (round_valid) begin
                rounded[o] <= ($signed({captured[o][31],captured[o]}) +
                               (layer == 0 ? 33'sd8 : 33'sd32)) >>> (layer == 0 ? 4 : 6);
                nonpositive[o] <= captured[o] <= 0;
            end
        end
        assign accumulated[o] = (sum_first ? $signed(sum_bias[o*32 +: 32]) : accumulator[o]) + lane_sum[o];
    end
    logic write_valid, write_final;
    logic [2:0] write_group;
    always_ff @(posedge clk) begin
        if (reset) write_valid <= 0;
        else begin
            write_valid <= round_valid;
            write_final <= round_final;
            write_group <= round_group;
        end
    end
    assign busy = state != IDLE;
    always_ff @(posedge clk) begin
        if (reset) begin
            state <= IDLE; layer <= 0; issue_input <= 0; issue_group <= 0;
            done <= 0; score <= 0;
        end else begin
            done <= 0;
            if (write_valid) begin
                if (layer == 2) begin
                    score <= captured[0];
                    if (write_final) state <= FINISH;
                end else begin
                    // Write only the completed 32-neuron group, not all 256 at once.
                    for (int o=0; o<32; o++)
                        next_activations[{write_group,5'(o)}] <= nonpositive[o] ? 8'd0 :
                            (rounded[o] > 127 ? 8'd127 : rounded[o][7:0]);
                    if (write_final) begin
                        state <= QUANTIZE;
                    end
                end
            end
            case (state)
                IDLE: if (start) begin
                    for (int i=0; i<16; i++) activations[i] <= $signed(feature[i]);
                    layer <= 0; issue_input <= 0; issue_group <= 0; state <= ISSUE;
                end
                ISSUE: begin
                    if (input_last) begin
                        issue_input <= 0;
                        if (group_last) state <= DRAIN;
                        else issue_group <= issue_group+1;
                    end else issue_input <= issue_input+1;
                end
                QUANTIZE: begin
                    // Keep layer inputs intact until every output group is complete.
                    for (int i=0; i<256; i++) activations[i] <= next_activations[i];
                    layer <= layer+1; issue_input <= 0; issue_group <= 0; state <= ISSUE;
                end
                FINISH: begin done <= 1; state <= IDLE; end
                default: ;
            endcase
        end
    end
endmodule
