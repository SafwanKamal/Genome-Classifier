// Streaming alternative to batch_model_core; the original core is unchanged.
// Same 32-output x 4-input packing/model. Issue one weight word each clock;
// BRAM read, registered multiplication and accumulation overlap with valid tags.
module batch_model_stream_core (
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
    logic signed [7:0] activations [0:255];
    logic signed [31:0] outputs [0:255];
    logic [1023:0] weight_rom [0:607], weight_word;
    logic [1023:0] bias_rom [0:16], bias_word, product_bias;
    logic signed [7:0] inputs [0:3];
    logic signed [31:0] accumulator [0:31], accumulated [0:31];
    logic signed [17:0] lane_sum [0:31];
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
        weight_word <= weight_rom[weight_address];
        bias_word <= bias_rom[bias_address];
        product_bias <= bias_word;
        if (reset) begin read_valid <= 0; product_valid <= 0; end
        else begin
            read_valid <= state == ISSUE;
            read_first <= issue_input == 0;
            read_last <= input_last;
            read_final <= input_last && group_last;
            read_group <= issue_group;
            product_valid <= read_valid;
            product_first <= read_first;
            product_last <= read_last;
            product_final <= read_final;
            product_group <= read_group;
        end
    end
    for (genvar i=0; i<4; i++) begin : input_register
        always_ff @(posedge clk) inputs[i] <= activations[{issue_input, 2'(i)}];
    end
    for (genvar o=0; o<32; o++) begin : output_lane
        wire signed [15:0] product [0:3];
        for (genvar i=0; i<4; i++) begin : input_lane
            wire signed [7:0] weight_value = weight_word[(o*4+i)*8 +: 8];
            (* use_dsp = "yes" *) logic signed [15:0] multiplied;
            always_ff @(posedge clk) multiplied <= inputs[i] * weight_value;
            assign product[i] = multiplied;
        end
        assign lane_sum[o] = {{2{product[0][15]}}, product[0]} +
                             {{2{product[1][15]}}, product[1]} +
                             {{2{product[2][15]}}, product[2]} +
                             {{2{product[3][15]}}, product[3]};
        assign accumulated[o] = (product_first ? $signed(product_bias[o*32 +: 32]) : accumulator[o]) + lane_sum[o];
    end
    function automatic logic [7:0] requantize(input logic signed [31:0] value, input integer shift);
        logic signed [32:0] rounded;
        rounded = ({{1{value[31]}}, value} + (33'sd1 << (shift-1))) >>> shift;
        if (value <= 0) return 0;
        if (rounded > 127) return 127;
        return rounded[7:0];
    endfunction
    assign busy = state != IDLE;
    always_ff @(posedge clk) begin
        if (reset) begin
            state <= IDLE; layer <= 0; issue_input <= 0; issue_group <= 0;
            done <= 0; score <= 0;
        end else begin
            done <= 0;
            if (product_valid) begin
                for (int o=0; o<32; o++) begin
                    accumulator[o] <= accumulated[o];
                    if (product_last) outputs[{product_group,5'(o)}] <= accumulated[o];
                end
                if (product_final) state <= layer == 2 ? FINISH : QUANTIZE;
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
                    for (int i=0; i<256; i++) activations[i] <= requantize(outputs[i], layer == 0 ? 4 : 6);
                    layer <= layer+1; issue_input <= 0; issue_group <= 0; state <= ISSUE;
                end
                FINISH: begin score <= outputs[0]; done <= 1; state <= IDLE; end
                default: ;
            endcase
        end
    end
endmodule
