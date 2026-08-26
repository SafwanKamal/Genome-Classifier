(* use_dsp = "yes" *)
module dense_neuron #(
    parameter integer FEATURE_NUMBER = 16,
    parameter logic signed [31:0] BIAS = 32'sd25,
    parameter string WEIGHT_FILE = "neuron_weights.mem"
) (
    input  logic clk,
    input  logic reset,
    input  logic start,

    input  logic [7:0] feature
        [0:FEATURE_NUMBER - 1],

    output logic busy,
    output logic done,
    output logic signed [31:0] score
);

    localparam integer INDEX_WIDTH =
        (FEATURE_NUMBER <= 1)
        ? 1
        : $clog2(FEATURE_NUMBER);


    typedef enum logic {
        IDLE,
        MAC
    } state_t;

    logic signed [7:0] weight_ROM [0:FEATURE_NUMBER - 1];

    initial begin
        for (int i = 0; i < FEATURE_NUMBER; i++) begin
            weight_ROM[i] = 8'sd0;
        end

        $readmemh(WEIGHT_FILE, weight_ROM);
    end

    function automatic logic signed [7:0] get_weight (
    input logic [INDEX_WIDTH-1:0] index
    );
    begin
        get_weight = weight_ROM[index];
    end
    endfunction


    state_t state_reg, state_next;

    logic [INDEX_WIDTH-1:0] index_reg, index_next;

    logic signed [31:0] accumulator_reg;
    logic signed [31:0] accumulator_next;

    logic signed [31:0] score_reg, score_next;

    logic busy_reg, busy_next;
    logic done_reg, done_next;

    logic signed [7:0]  feature_signed;
    logic signed [7:0]  weight;

    /*
    This came up when we were doing the implementation for the modules in vivado.
    Firstly, there was the fact that we faced a critical warning of negative slack.
    So, some signal was late to reach a module to another. 
    The timing report identified the neurons delared in this layer to be responsible for that.
    It happaned because vivado did not actually infer DSPs from the multiplication operations,
    rather implementing them with LUTS. That required quite a few sequential steps,
    which broke the timing constraints. So, we are explicitly saying that we are using DSPs here. 
    */
    // (* use_dsp = "yes" *)
    logic signed [15:0] product;
    logic signed [31:0] product_extended;


    always_ff @(posedge clk or posedge reset) begin
        if (reset) begin
            state_reg       <= IDLE;
            index_reg       <= '0;
            accumulator_reg <= BIAS;
            score_reg       <= '0;
            busy_reg        <= 1'b0;
            done_reg        <= 1'b0;
        end
        else begin
            state_reg       <= state_next;
            index_reg       <= index_next;
            accumulator_reg <= accumulator_next;
            score_reg       <= score_next;
            busy_reg        <= busy_next;
            done_reg        <= done_next;
        end
    end


    always_comb begin
        state_next       = state_reg;
        index_next       = index_reg;
        accumulator_next = accumulator_reg;
        score_next       = score_reg;
        busy_next        = busy_reg;
        done_next        = 1'b0;

        feature_signed  = $signed(feature[index_reg]);
        weight          = get_weight(index_reg);

        // We are using signed multiplication here, so we need to extend the product to 32 bits to avoid overflow
        // Also, we need to be careful about using direct multiplication here, as it can lead to synthesis issues.
        // The board has separate units for multiplication and we are only using 1 unit of those.
        // So this will be synthesized as a single multiplier, and we will not have any issues with timing or resource usage.
        // But in the future, if we want to use multiple multipliers, we need to be careful about how we use them.
        // And we also need to optimize our resources the the number of neurons increases. 
        product         = feature_signed * weight;
        product_extended = {
            {16{product[15]}},
            product
        };


        case (state_reg)

            IDLE: begin
                busy_next  = 1'b0;
                index_next = '0;

                if (start) begin
                    accumulator_next = BIAS;
                    busy_next        = 1'b1;
                    state_next       = MAC;
                end
            end


            MAC: begin
                accumulator_next =
                    accumulator_reg + product_extended;

                if (index_reg == FEATURE_NUMBER - 1) begin
                    score_next =
                        accumulator_reg + product_extended;

                    index_next = '0;
                    busy_next  = 1'b0;
                    done_next  = 1'b1;
                    state_next = IDLE;
                end
                else begin
                    index_next = index_reg + 1'b1;
                end
            end


            default: begin
                state_next       = IDLE;
                index_next       = '0;
                accumulator_next = BIAS;
                busy_next        = 1'b0;
                done_next        = 1'b0;
            end

        endcase
    end


    assign busy  = busy_reg;
    assign done  = done_reg;
    assign score = score_reg;

endmodule