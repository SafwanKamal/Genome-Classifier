module dense_layer #(
    parameter integer FEATURE_NUMBER = 16,
    parameter integer NEURON_NUMBER = 4,
    parameter logic signed [31:0] BIAS_0 =  32'sd25,
    parameter logic signed [31:0] BIAS_1 = -32'sd10,
    parameter logic signed [31:0] BIAS_2 =  32'sd100,
    parameter logic signed [31:0] BIAS_3 =  32'sd0,

    parameter string WEIGHT_FILE_0 = "neuron_0_weights.mem",
    parameter string WEIGHT_FILE_1 = "neuron_1_weights.mem",
    parameter string WEIGHT_FILE_2 = "neuron_2_weights.mem",
    parameter string WEIGHT_FILE_3 = "neuron_3_weights.mem"
) (
    input  logic clk,
    input  logic reset,
    input  logic start,

    input  logic [7:0] feature
        [0:FEATURE_NUMBER - 1],

    output logic signed [31:0] layer_output
        [0:NEURON_NUMBER - 1],

    output logic busy,
    output logic done
);

    typedef enum logic [1:0] {
        IDLE,
        START_NEURONS,
        WAIT_NEURONS
    } state_t;


    state_t state_reg, state_next;

    logic [7:0] feature_reg
        [0:FEATURE_NUMBER - 1];

    logic [7:0] feature_next
        [0:FEATURE_NUMBER - 1];

    logic signed [31:0] neuron_score [0:NEURON_NUMBER - 1];

    logic [3:0] neuron_busy;
    logic [3:0] neuron_done;

    logic [3:0] done_seen_reg;
    logic [3:0] done_seen_next;

    logic neuron_start;

    logic busy_reg, busy_next;
    logic done_reg, done_next;


    dense_neuron #(
        .FEATURE_NUMBER(FEATURE_NUMBER),
        .BIAS          (BIAS_0),
        .WEIGHT_FILE   (WEIGHT_FILE_0)
    ) neuron_0 (
        .clk    (clk),
        .reset  (reset),
        .start  (neuron_start),
        .feature(feature_reg),
        .busy   (neuron_busy[0]),
        .done   (neuron_done[0]),
        .score  (neuron_score[0])
    );


    dense_neuron #(
        .FEATURE_NUMBER(FEATURE_NUMBER),
        .BIAS          (BIAS_1),
        .WEIGHT_FILE   (WEIGHT_FILE_1)
    ) neuron_1 (
        .clk    (clk),
        .reset  (reset),
        .start  (neuron_start),
        .feature(feature_reg),
        .busy   (neuron_busy[1]),
        .done   (neuron_done[1]),
        .score  (neuron_score[1])
    );


    dense_neuron #(
        .FEATURE_NUMBER(FEATURE_NUMBER),
        .BIAS          (BIAS_2),
        .WEIGHT_FILE   (WEIGHT_FILE_2)
    ) neuron_2 (
        .clk    (clk),
        .reset  (reset),
        .start  (neuron_start),
        .feature(feature_reg),
        .busy   (neuron_busy[2]),
        .done   (neuron_done[2]),
        .score  (neuron_score[2])
    );


    dense_neuron #(
        .FEATURE_NUMBER(FEATURE_NUMBER),
        .BIAS          (BIAS_3),
        .WEIGHT_FILE   (WEIGHT_FILE_3)
    ) neuron_3 (
        .clk    (clk),
        .reset  (reset),
        .start  (neuron_start),
        .feature(feature_reg),
        .busy   (neuron_busy[3]),
        .done   (neuron_done[3]),
        .score  (neuron_score[3])
    );


    always_ff @(posedge clk or posedge reset) begin
        if (reset) begin
            state_reg     <= IDLE;
            done_seen_reg <= '0;
            busy_reg      <= 1'b0;
            done_reg      <= 1'b0;

            for (int i = 0; i < FEATURE_NUMBER; i++) begin
                feature_reg[i] <= 8'h00;
            end
        end
        else begin
            state_reg     <= state_next;
            feature_reg   <= feature_next;
            done_seen_reg <= done_seen_next;
            busy_reg      <= busy_next;
            done_reg      <= done_next;
        end
    end


    always_comb begin
        state_next     = state_reg;
        feature_next   = feature_reg;
        done_seen_next = done_seen_reg;
        busy_next      = busy_reg;
        done_next      = 1'b0;
        neuron_start   = 1'b0;

        case (state_reg)

            IDLE: begin
                busy_next      = 1'b0;
                done_seen_next = '0;

                if (start) begin
                    for (
                        int i = 0;
                        i < FEATURE_NUMBER;
                        i++
                    ) begin
                        feature_next[i] = feature[i];
                    end

                    busy_next  = 1'b1;
                    state_next = START_NEURONS;
                end
            end


            START_NEURONS: begin
                /*
                 * The features were registered during the
                 * previous clock cycle. They are now stable,
                 * so all four neurons can start together.
                 */
                neuron_start = 1'b1;
                state_next   = WAIT_NEURONS;
            end


            WAIT_NEURONS: begin
                /*
                 * Record each done pulse. This still works if
                 * the neurons finish on different clock cycles.
                 */
                done_seen_next =
                    done_seen_reg | neuron_done;

                if (&(done_seen_reg | neuron_done)) begin
                    busy_next      = 1'b0;
                    done_next      = 1'b1;
                    done_seen_next = '0;
                    state_next     = IDLE;
                end
            end


            default: begin
                state_next     = IDLE;
                done_seen_next = '0;
                busy_next      = 1'b0;
                done_next      = 1'b0;
                neuron_start   = 1'b0;
            end

        endcase
    end


    assign layer_output[0] = neuron_score[0];
    assign layer_output[1] = neuron_score[1];
    assign layer_output[2] = neuron_score[2];
    assign layer_output[3] = neuron_score[3];

    assign busy = busy_reg;
    assign done = done_reg;

endmodule