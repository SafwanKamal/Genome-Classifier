// Separate 16-256-256-1 experiment. One shared 32-output x 4-input MAC engine.
// ROM words pack output-major, input-minor lanes, least-significant lane first.
module batch_model_core (
    input logic clk, reset, start,
    input logic [7:0] feature [0:15],
    output logic busy, done,
    output logic signed [31:0] score
);
    typedef enum logic [2:0] {IDLE, READ, MULTIPLY, MAC, STORE, QUANTIZE, FINISH} state_t;
    state_t state;
    logic [1:0] layer;
    integer input_group, output_group;
    integer input_groups, output_groups, weight_base, bias_base, shift;
    logic signed [7:0] activations [0:255];
    logic signed [31:0] outputs [0:255];
    logic [1023:0] weight_rom [0:607];
    logic [1023:0] bias_rom [0:16];
    logic [1023:0] weight_word, bias_word;
    logic signed [7:0] inputs [0:3];
    logic signed [31:0] accumulator [0:31];
    logic signed [17:0] lane_sum [0:31];
    initial begin
        $readmemh("batch_weights.mem", weight_rom);
        $readmemh("batch_biases.mem", bias_rom);
    end
    always_comb begin
        input_groups = layer == 0 ? 4 : 64;
        output_groups = layer == 2 ? 1 : 8;
        weight_base = layer == 0 ? 0 : (layer == 1 ? 32 : 544);
        bias_base = layer == 0 ? 0 : (layer == 1 ? 8 : 16);
        shift = layer == 0 ? 4 : 6;
    end
    // Synchronous reads permit BRAM inference. READ gives data one clock to settle.
    always_ff @(posedge clk) begin
        weight_word <= weight_rom[weight_base + output_group*input_groups + input_group];
        bias_word <= bias_rom[bias_base + output_group];
    end
    for (genvar i=0; i<4; i++) begin : input_register
        always_ff @(posedge clk) inputs[i] <= activations[input_group*4+i];
    end
    for (genvar o=0; o<32; o++) begin : output_lane
        wire signed [15:0] product [0:3];
        for (genvar i=0; i<4; i++) begin : input_lane
            wire signed [7:0] weight_value = weight_word[(o*4+i)*8 +: 8];
            (* use_dsp = "yes" *) logic signed [15:0] multiplied;
            always_ff @(posedge clk) multiplied <= inputs[i] * weight_value;
            assign product[i] = multiplied;
        end
        // Extend each signed product before summing; four products exceed 16 bits.
        assign lane_sum[o] = {{2{product[0][15]}}, product[0]} +
                             {{2{product[1][15]}}, product[1]} +
                             {{2{product[2][15]}}, product[2]} +
                             {{2{product[3][15]}}, product[3]};
    end
    function automatic logic [7:0] requantize(input logic signed [31:0] value, input integer amount);
        logic signed [32:0] rounded;
        rounded = ({{1{value[31]}}, value} + (33'sd1 << (amount-1))) >>> amount;
        if (value <= 0) return 0;
        if (rounded > 127) return 127;
        return rounded[7:0];
    endfunction
    assign busy = state != IDLE;
    always_ff @(posedge clk) begin
        if (reset) begin
            state <= IDLE;
            layer <= 0;
            input_group <= 0;
            output_group <= 0;
            done <= 0;
            score <= 0;
        end else begin
            done <= 0;
            case (state)
                IDLE: if (start) begin
                    for (int i=0; i<16; i++) activations[i] <= $signed(feature[i]);
                    layer <= 0;
                    input_group <= 0;
                    output_group <= 0;
                    state <= READ;
                end
                READ: state <= MULTIPLY;
                MULTIPLY: state <= MAC;
                MAC: begin
                    for (int o=0; o<32; o++) begin
                        if (input_group == 0)
                            accumulator[o] <= $signed(bias_word[o*32 +: 32]) + lane_sum[o];
                        else accumulator[o] <= accumulator[o] + lane_sum[o];
                    end
                    if (input_group == input_groups-1) state <= STORE;
                    else begin input_group <= input_group+1; state <= READ; end
                end
                STORE: begin
                    for (int o=0; o<32; o++) outputs[output_group*32+o] <= accumulator[o];
                    input_group <= 0;
                    if (output_group == output_groups-1) state <= layer == 2 ? FINISH : QUANTIZE;
                    else begin output_group <= output_group+1; state <= READ; end
                end
                QUANTIZE: begin
                    for (int i=0; i<256; i++) activations[i] <= requantize(outputs[i], shift);
                    layer <= layer+1;
                    output_group <= 0;
                    state <= READ;
                end
                FINISH: begin score <= outputs[0]; done <= 1; state <= IDLE; end
                default: state <= IDLE;
            endcase
        end
    end
endmodule
