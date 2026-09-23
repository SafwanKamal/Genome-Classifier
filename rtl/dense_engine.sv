// (* use_dsp = "yes" *)
module dense_engine #(
    parameter integer INPUT_NUMBER  = 16,
    parameter integer OUTPUT_NUMBER = 4,
    parameter integer MAC_LANES     = 4,

    parameter integer INPUT_WIDTH    = 8,
    parameter integer WEIGHT_WIDTH   = 8,
    parameter integer ACC_WIDTH      = 32,

    parameter string WEIGHT_FILE = "dense_weights.mem",
    parameter string BIAS_FILE   = "dense_biases.mem",

    // Where the synthesis tool should put the weight ROM.
    // "" leaves the choice to the tool (the original behaviour),
    // "block" asks for BRAM and "distributed" asks for LUTRAM.
    parameter string ROM_STYLE = "",

    // 0: one input every 3 clocks (READ_DATA -> LOAD_DATA -> MAC), the original behaviour.
    // 1: the same three steps overlapped, so one input is accumulated every clock.
    //    Outputs are identical in both modes; only the latency changes.
    parameter integer PIPELINED = 0
) (
    input logic clk,
    input logic reset,
    input logic start,

    input logic signed [INPUT_WIDTH-1:0] input_data
        [0:INPUT_NUMBER-1],

    output logic busy,
    output logic done,

    output logic signed [ACC_WIDTH-1:0] output_data
        [0:OUTPUT_NUMBER-1]
);

    localparam integer WEIGHT_WORD_WIDTH = MAC_LANES * WEIGHT_WIDTH;
    // No function like this, so have to do it manually
    // localparam integer OUTPUT_GROUP_NUMBER = CEIL(OUTPUT_NUMBER / MAC_LANES);
    localparam integer OUTPUT_GROUP_NUMBER = (OUTPUT_NUMBER + MAC_LANES - 1) / MAC_LANES;
    localparam integer WEIGHT_DEPTH = OUTPUT_GROUP_NUMBER * INPUT_NUMBER;
    localparam integer WEIGHT_WORD_INDEX_WIDTH = (WEIGHT_DEPTH <= 1) ? 1 : $clog2(WEIGHT_DEPTH);

    localparam integer OUTPUT_GROUP_INDEX_WIDTH = (OUTPUT_GROUP_NUMBER <= 1) ? 1 : $clog2(OUTPUT_GROUP_NUMBER);
    localparam integer INPUT_INDEX_WIDTH =(INPUT_NUMBER <= 1) ? 1 : $clog2(INPUT_NUMBER);
    localparam integer OUTPUT_INDEX_WIDTH =(OUTPUT_NUMBER <= 1) ? 1 : $clog2(OUTPUT_NUMBER);
    
    localparam integer BIAS_WORD_WIDTH = MAC_LANES * ACC_WIDTH;
    localparam integer BIAS_DEPTH = OUTPUT_GROUP_NUMBER;
    localparam integer BIAS_WIDTH = ACC_WIDTH;
    localparam integer OUTPUT_WIDTH = ACC_WIDTH;

    localparam integer PRODUCT_WIDTH = WEIGHT_WIDTH + INPUT_WIDTH;

    typedef enum logic [2:0] {
        IDLE,
        READ_DATA,
        LOAD_DATA,
        MAC, // Multiply-accumulate state
        OUTPUT
    } state_t;

    state_t state_reg, state_next;

    // The weight ROM itself is declared in the rom_style generate block further down,
    // because a synthesis attribute cannot take its value from a parameter.

    logic signed [WEIGHT_WORD_WIDTH - 1:0] weight_word_reg; // weight_word_next;



    logic [INPUT_INDEX_WIDTH-1:0] input_index_reg, input_index_next;
    logic signed [INPUT_WIDTH-1:0] input_data_reg [0:INPUT_NUMBER-1];
    logic signed [INPUT_WIDTH-1:0] input_data_next [0:INPUT_NUMBER-1];

    logic [OUTPUT_GROUP_INDEX_WIDTH-1:0] output_group_index_reg, output_group_index_next;
    // logic [WEIGHT_WORD_INDEX_WIDTH-1:0] weight_word_index_reg, weight_word_index_next;



    logic signed [WEIGHT_WIDTH - 1:0] weight_reg [0:MAC_LANES - 1]; // Will combinationally separate out the weights 
                                                                    // from the weight word
    logic signed [WEIGHT_WIDTH - 1:0] weight_next [0:MAC_LANES - 1];
    logic signed [ACC_WIDTH - 1:0] acc_reg [0:MAC_LANES - 1];
    logic signed [ACC_WIDTH - 1:0] acc_next [0:MAC_LANES - 1];
    logic signed [OUTPUT_WIDTH-1:0] output_data_reg [0:OUTPUT_NUMBER-1];
    logic signed [OUTPUT_WIDTH-1:0] output_data_next [0:OUTPUT_NUMBER-1];

    // (* use_dsp = "yes" *)
    logic signed [PRODUCT_WIDTH-1:0] product [0:MAC_LANES-1];

    logic signed [BIAS_WORD_WIDTH-1:0] bias_ROM [0:BIAS_DEPTH-1];
    logic signed [BIAS_WORD_WIDTH-1:0] bias_word_reg;// bias_word_next;

    initial begin
        for (int i = 0; i < BIAS_DEPTH; i++) begin
            bias_ROM[i] = '0;
        end

        $readmemh(BIAS_FILE, bias_ROM);
    end

    // Pipelined-mode registers. They are unused (and removed by synthesis) when PIPELINED = 0.
    // read_* is the ROM address running ahead, load_* is the word being unpacked into
    // weight_reg, and the MAC stage reuses input_index_reg and output_group_index_reg.
    logic [INPUT_INDEX_WIDTH-1:0] read_input_index_reg, read_input_index_next;
    logic [OUTPUT_GROUP_INDEX_WIDTH-1:0] read_group_index_reg, read_group_index_next;
    logic read_active_reg, read_active_next;

    logic [INPUT_INDEX_WIDTH-1:0] load_input_index_reg, load_input_index_next;
    logic [OUTPUT_GROUP_INDEX_WIDTH-1:0] load_group_index_reg, load_group_index_next;
    logic load_valid_reg, load_valid_next;
    logic mac_valid_reg, mac_valid_next;

    // The bias has to travel with its group through the pipeline, because the last MAC
    // of one group happens in the same clock as the load of the next group's first word.
    logic signed [ACC_WIDTH - 1:0] bias_reg [0:MAC_LANES - 1];
    logic signed [ACC_WIDTH - 1:0] bias_next [0:MAC_LANES - 1];

    // ROM read port, driven by whichever control block is generated below.
    logic weight_read_enable;
    logic [WEIGHT_WORD_INDEX_WIDTH-1:0] weight_read_address;
    logic bias_read_enable;
    logic [OUTPUT_GROUP_INDEX_WIDTH-1:0] bias_read_address;

    always_ff @(posedge clk or posedge reset) begin
        if (reset) begin
            state_reg <= IDLE;
            // weight_word_reg <= '0;
            // bias_word_reg <= '0;
            input_index_reg <= '0;
            output_group_index_reg <= '0;
            // weight_word_index_reg <= '0;
            for (int i = 0; i < MAC_LANES; i++) begin
                weight_reg[i] <= '0;
                acc_reg[i] <= '0;
            end
            for (int i = 0; i < OUTPUT_NUMBER; i++) begin
                output_data_reg[i] <= '0;
            end
            for (int i = 0; i < INPUT_NUMBER; i++) begin
                input_data_reg[i] <= '0;
            end
            read_input_index_reg <= '0;
            read_group_index_reg <= '0;
            read_active_reg <= 1'b0;
            load_input_index_reg <= '0;
            load_group_index_reg <= '0;
            load_valid_reg <= 1'b0;
            mac_valid_reg <= 1'b0;
            for (int i = 0; i < MAC_LANES; i++) begin
                bias_reg[i] <= '0;
            end
        end else begin
            state_reg <= state_next;
            // weight_word_reg <= weight_word_next;
            // bias_word_reg <= bias_word_next;
            input_index_reg <= input_index_next;
            output_group_index_reg <= output_group_index_next;
            // weight_word_index_reg <= weight_word_index_next;
            weight_reg <= weight_next;
            acc_reg <= acc_next;
            output_data_reg <= output_data_next;
            input_data_reg <= input_data_next;
            read_input_index_reg <= read_input_index_next;
            read_group_index_reg <= read_group_index_next;
            read_active_reg <= read_active_next;
            load_input_index_reg <= load_input_index_next;
            load_group_index_reg <= load_group_index_next;
            load_valid_reg <= load_valid_next;
            mac_valid_reg <= mac_valid_next;
            bias_reg <= bias_next;
        end
    end


    // According to chatGPT, for the FPGA synthesis tool to infer synched BRAM,
    // the sensitivity of the block should be on the clock, not a * sensitivity like in the always_comb block.
    // That is why we have a separate always_ff block for reading the weight and bias ROMs.
    // Practically, both designs resolve this in 1 clock. But, just for synthesis purposes, 
    //we have to separate the reading of the ROMs into a separate always_ff block.
    //
    // The read enable and address now come from the control block, so the same read
    // works for the sequential and the pipelined schedule.
    always_ff @(posedge clk) begin
        if (bias_read_enable) begin
            bias_word_reg <= bias_ROM[bias_read_address];
        end
    end

    // One copy of the weight ROM per rom_style, only one of which is elaborated.
    // Each copy is zero-filled and then loaded, exactly as before.
    generate
        if (ROM_STYLE == "block") begin
            : block_weight_ROM

            (* rom_style = "block" *)
            logic signed [WEIGHT_WORD_WIDTH - 1:0] weight_ROM [0:WEIGHT_DEPTH - 1];

            initial begin
                for (int i = 0; i < WEIGHT_DEPTH; i++) begin
                    weight_ROM[i] = '0;
                end
                $readmemh(WEIGHT_FILE, weight_ROM);
            end

            always_ff @(posedge clk) begin
                if (weight_read_enable) begin
                    weight_word_reg <= weight_ROM[weight_read_address];
                end
            end

        end else if (ROM_STYLE == "distributed") begin
            : distributed_weight_ROM

            (* rom_style = "distributed" *)
            logic signed [WEIGHT_WORD_WIDTH - 1:0] weight_ROM [0:WEIGHT_DEPTH - 1];

            initial begin
                for (int i = 0; i < WEIGHT_DEPTH; i++) begin
                    weight_ROM[i] = '0;
                end
                $readmemh(WEIGHT_FILE, weight_ROM);
            end

            always_ff @(posedge clk) begin
                if (weight_read_enable) begin
                    weight_word_reg <= weight_ROM[weight_read_address];
                end
            end

        end else begin
            : default_weight_ROM

            logic signed [WEIGHT_WORD_WIDTH - 1:0] weight_ROM [0:WEIGHT_DEPTH - 1];

            initial begin
                for (int i = 0; i < WEIGHT_DEPTH; i++) begin
                    weight_ROM[i] = '0;
                end
                $readmemh(WEIGHT_FILE, weight_ROM);
            end

            always_ff @(posedge clk) begin
                if (weight_read_enable) begin
                    weight_word_reg <= weight_ROM[weight_read_address];
                end
            end
        end
    endgenerate

    // This generate block is necessary to scope the FPGA DSPs to only the product generation.
    // Otherwise, the synthesis tool will use LUTs for multiplication. Or,
    // if dsp identifiers are used at the top of the module,
    // synthesis tool will infer unnecessary DSPs for the MAC operation, which is not what we want.
    generate
        for (genvar lane = 0; lane < MAC_LANES; lane++) begin
            : product_generation

            (* use_dsp = "yes" *)
            logic signed [PRODUCT_WIDTH-1:0]
                lane_product;

            assign lane_product =
                weight_reg[lane]
                * input_data_reg[input_index_reg];

            assign product[lane] = lane_product;
        end
    endgenerate

    generate
        if (PIPELINED == 0) begin
            : sequential_control

            // Original schedule: READ_DATA -> LOAD_DATA -> MAC for every input.
            always_comb begin
                weight_read_enable = (state_reg == READ_DATA);
                weight_read_address = output_group_index_reg * INPUT_NUMBER + input_index_reg;
                bias_read_enable = (state_reg == READ_DATA) && (input_index_reg == 0);
                bias_read_address = output_group_index_reg;

                // The pipelined registers are not used in this mode
                read_input_index_next = read_input_index_reg;
                read_group_index_next = read_group_index_reg;
                read_active_next = read_active_reg;
                load_input_index_next = load_input_index_reg;
                load_group_index_next = load_group_index_reg;
                load_valid_next = load_valid_reg;
                mac_valid_next = mac_valid_reg;
                bias_next = bias_reg;
            end

        always_comb begin
            state_next = state_reg;
            // weight_word_next = weight_word_reg;
            // bias_word_next = bias_word_reg;
            input_index_next = input_index_reg;
            output_group_index_next = output_group_index_reg;
            // weight_word_index_next = weight_word_index_reg;
            weight_next = weight_reg;
            acc_next = acc_reg;
            output_data_next = output_data_reg;
            input_data_next = input_data_reg;
            // for (int i = 0; i < MAC_LANES; i++) begin
            //     product[i] = '0;
            // end
            case(state_reg)
                IDLE: begin
                    if (start) begin
                        state_next = READ_DATA;
                        output_group_index_next = '0;
                        input_index_next = '0;
                        input_data_next = input_data;
                        for (int i = 0; i < MAC_LANES; i++) begin
                            weight_next[i] = '0;
                            acc_next[i] = '0;
                        end
                    end
                end

                // We will be reading and loading in data in 2 clocks.
                // Basically to keep everything synced. 
                READ_DATA: begin
                    // Check the note before about why we are not reading the weight and bias ROMs in this always_comb block.
                    // weight_word_next = weight_ROM[output_group_index_reg * INPUT_NUMBER + input_index_reg];
                    // // Loading the bias is not necessary for every input, only when the group changes is it necessary.
                    // bias_word_next = bias_ROM[output_group_index_reg];
                    state_next = LOAD_DATA;
                end

                LOAD_DATA: begin

                    for (int i = 0; i < MAC_LANES; i++) begin
                        weight_next[i] = $signed(weight_word_reg[WEIGHT_WORD_WIDTH - i * WEIGHT_WIDTH - 1 -: WEIGHT_WIDTH]);

                        if (input_index_reg == 0) begin
                            acc_next[i] = $signed(bias_word_reg[BIAS_WORD_WIDTH - i * ACC_WIDTH - 1 -: ACC_WIDTH]);
                        end else begin
                            acc_next[i] = acc_reg[i];
                        end
                    end
                    state_next = MAC;
                end


                MAC: begin
                    // (* use_dsp = "yes" *)
                    for(int i = 0; i < MAC_LANES; i++)begin
                        // Have to do this to use FPGA DSPs.
                        // Otherwise, the synthesis tool will use LUTs for multiplication.
                        // product[i] = weight_reg[i] * input_data_reg[input_index_reg];
                        acc_next[i] = acc_reg[i] + product[i];
                    end

                    if (input_index_reg == INPUT_NUMBER - 1) begin
                        input_index_next = '0;
                        // When the accumulation for the current output group is done, We store the accumulated values into the output_data array
                        for (int i = 0; i < MAC_LANES; i++) begin
                            int output_index = output_group_index_reg * MAC_LANES + i;
                            // This condition is for the last group
                            if (output_index < OUTPUT_NUMBER) begin
                                output_data_next[output_index] = acc_next[i];
                            end
                        end

                        output_group_index_next = output_group_index_reg + 1;
                        if (output_group_index_reg == OUTPUT_GROUP_NUMBER - 1) begin   
                            state_next = OUTPUT;
                            output_group_index_next = '0;
                        end else begin
                            state_next = READ_DATA;
                        end
                    end else begin
                        input_index_next = input_index_reg + 1;
                        state_next = READ_DATA;
                    end

                    // Not using this because will need a extra cycle 
                    // weight_word_index_next = output_group_index_reg * INPUT_NUMBER + input_index_reg;
                end

                OUTPUT: begin
                    state_next = IDLE;
                end
            endcase
        end

        end else begin
            : pipelined_control

            // The same three steps, overlapped. In any one clock:
            //   read: the ROM word for (read_group, read_input) is requested
            //   load: last clock's word is unpacked into weight_reg (and the bias, for input 0)
            //   MAC:  weight_reg * input_data_reg[input_index_reg] is accumulated
            // so a new input enters every clock and the result is ready
            // OUTPUT_GROUP_NUMBER * INPUT_NUMBER + 2 clocks after start.
            always_comb begin
                state_next = state_reg;
                input_index_next = input_index_reg;
                output_group_index_next = output_group_index_reg;
                weight_next = weight_reg;
                acc_next = acc_reg;
                output_data_next = output_data_reg;
                input_data_next = input_data_reg;

                read_input_index_next = read_input_index_reg;
                read_group_index_next = read_group_index_reg;
                read_active_next = read_active_reg;
                load_input_index_next = load_input_index_reg;
                load_group_index_next = load_group_index_reg;
                load_valid_next = 1'b0;
                mac_valid_next = 1'b0;
                bias_next = bias_reg;

                weight_read_enable = 1'b0;
                weight_read_address = read_group_index_reg * INPUT_NUMBER + read_input_index_reg;
                bias_read_enable = 1'b0;
                bias_read_address = read_group_index_reg;

                case(state_reg)
                    IDLE: begin
                        if (start) begin
                            state_next = MAC;
                            input_data_next = input_data;
                            read_input_index_next = '0;
                            read_group_index_next = '0;
                            read_active_next = 1'b1;
                            for (int i = 0; i < MAC_LANES; i++) begin
                                weight_next[i] = '0;
                                acc_next[i] = '0;
                            end
                        end
                    end

                    MAC: begin
                        // read
                        if (read_active_reg) begin
                            weight_read_enable = 1'b1;
                            bias_read_enable = (read_input_index_reg == 0);

                            load_valid_next = 1'b1;
                            load_input_index_next = read_input_index_reg;
                            load_group_index_next = read_group_index_reg;

                            if (read_input_index_reg == INPUT_NUMBER - 1) begin
                                read_input_index_next = '0;
                                read_group_index_next = read_group_index_reg + 1;
                                if (read_group_index_reg == OUTPUT_GROUP_NUMBER - 1) begin
                                    read_active_next = 1'b0;
                                end
                            end else begin
                                read_input_index_next = read_input_index_reg + 1;
                            end
                        end

                        // load
                        if (load_valid_reg) begin
                            for (int i = 0; i < MAC_LANES; i++) begin
                                weight_next[i] = $signed(weight_word_reg[WEIGHT_WORD_WIDTH - i * WEIGHT_WIDTH - 1 -: WEIGHT_WIDTH]);

                                if (load_input_index_reg == 0) begin
                                    bias_next[i] = $signed(bias_word_reg[BIAS_WORD_WIDTH - i * ACC_WIDTH - 1 -: ACC_WIDTH]);
                                end
                            end

                            mac_valid_next = 1'b1;
                            input_index_next = load_input_index_reg;
                            output_group_index_next = load_group_index_reg;
                        end

                        // MAC
                        if (mac_valid_reg) begin
                            for (int i = 0; i < MAC_LANES; i++) begin
                                // Input 0 starts from the bias, like LOAD_DATA does in the original schedule
                                if (input_index_reg == 0) begin
                                    acc_next[i] = bias_reg[i] + product[i];
                                end else begin
                                    acc_next[i] = acc_reg[i] + product[i];
                                end
                            end

                            if (input_index_reg == INPUT_NUMBER - 1) begin
                                for (int i = 0; i < MAC_LANES; i++) begin
                                    int output_index = output_group_index_reg * MAC_LANES + i;
                                    // This condition is for the last group
                                    if (output_index < OUTPUT_NUMBER) begin
                                        output_data_next[output_index] = acc_next[i];
                                    end
                                end

                                if (output_group_index_reg == OUTPUT_GROUP_NUMBER - 1) begin
                                    state_next = OUTPUT;
                                end
                            end
                        end
                    end

                    OUTPUT: begin
                        state_next = IDLE;
                        input_index_next = '0;
                        output_group_index_next = '0;
                    end

                    default: begin
                        state_next = IDLE;
                    end
                endcase
            end
        end
    endgenerate

    assign busy = (state_reg != IDLE);
    assign done = (state_reg == OUTPUT);
    assign output_data = output_data_reg;
endmodule 
