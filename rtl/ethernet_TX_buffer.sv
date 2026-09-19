module ethernet_TX_buffer #(
    parameter int ADDR_WIDTH = 11,
    parameter int MAX_FRAME_BYTES = 1514
)(
    input logic clk,
    input logic reset,

    // incoming frame from the packet builder
    input  logic [7:0] data_in,
    input  logic valid_in,
    input  logic last_in,
    output logic ready_out,

    // buffered frame sent to ethernet_TX
    output logic [7:0] data_out,
    output logic valid_out,
    output logic last_out,
    input  logic ready_in,

    // transmission result from ethernet_TX
    input logic tx_done_in,
    input logic tx_underflow_in,

    output logic busy_out,
    output logic overflow_out
);

    localparam int BUFFER_DEPTH = 1 << ADDR_WIDTH;

    typedef enum logic [2:0] {
        collect, prefetch, transmit, wait_result, discard
    } state_t;

    state_t state_reg, state_next;

    logic [ADDR_WIDTH:0] byte_count_reg, byte_count_next;
    logic [ADDR_WIDTH-1:0] read_address_reg, read_address_next;
    logic overflow_reg, overflow_next;

    (* ram_style = "block" *)
    logic [7:0] frame_memory [0:BUFFER_DEPTH-1];

    logic memory_write_enable, memory_read_enable;
    logic [ADDR_WIDTH-1:0] memory_write_address;
    logic [ADDR_WIDTH-1:0] memory_read_address;
    logic [7:0] memory_data_reg;

    // clocked memory access without resetting the array or its read output
    // Separate structure to ensure BRAM inference
    always_ff @(posedge clk) begin
        if (memory_write_enable)
            frame_memory[memory_write_address] <= data_in;

        if (memory_read_enable)
            memory_data_reg <= frame_memory[memory_read_address];
    end

    always_ff @(posedge clk or posedge reset) begin
        if (reset) begin
            state_reg <= collect;
            byte_count_reg <= 0;
            read_address_reg <= 0;
            overflow_reg <= 1'b0;
        end else begin
            state_reg <= state_next;
            byte_count_reg <= byte_count_next;
            read_address_reg <= read_address_next;
            overflow_reg <= overflow_next;
        end
    end

    always_comb begin
        state_next = state_reg;
        byte_count_next = byte_count_reg;
        read_address_next = read_address_reg;
        overflow_next = 1'b0;

        ready_out = 1'b0;
        valid_out = 1'b0;
        // last_out = 1'b0;

        memory_write_enable = 1'b0;
        memory_read_enable = 1'b0;
        memory_write_address = byte_count_reg[ADDR_WIDTH-1:0];
        memory_read_address = read_address_reg;

        case (state_reg)
            collect: begin
                ready_out = 1'b1;

                if (valid_in) begin
                    if (byte_count_reg < MAX_FRAME_BYTES) begin
                        memory_write_enable = 1'b1;
                        byte_count_next = byte_count_reg + 1'b1;

                        // commit the complete frame before beginning any read
                        if (last_in) begin
                            read_address_next = 0;
                            state_next = prefetch;
                        end
                    end else begin
                        // Rejection pathway
                        // this accepted byte exceeds the permitted frame size

                        overflow_next = 1'b1;
                        byte_count_next = 0;
                        read_address_next = 0;

                        if (last_in)
                            state_next = collect;
                        else
                            state_next = discard;
                    end
                end
            end

            prefetch: begin
                // the first byte and transmit state become valid at this edge
                memory_read_enable = 1'b1;
                state_next = transmit;
            end

            transmit: begin
                valid_out = 1'b1;

                if (ready_in) begin
                    if ({1'b0, read_address_reg} == byte_count_reg - 1'b1) begin
                        state_next = wait_result;
                    end else begin
                        // consume the current byte and fetch the next at once
                        memory_read_enable = 1'b1;
                        memory_read_address = read_address_reg + 1'b1;
                        read_address_next = read_address_reg + 1'b1;
                    end
                end
            end

            wait_result: begin
                // ethernet_TX still has to finish padding and fcs transmission
                if (tx_done_in) begin
                    byte_count_next = 0;
                    read_address_next = 0;
                    state_next = collect;
                end
            end

            discard: begin
                // consume the oversized frame without writing or transmitting
                ready_out = 1'b1;

                if (valid_in && last_in)
                    state_next = collect;
            end

            default: begin
                byte_count_next = 0;
                read_address_next = 0;
                state_next = collect;
            end
        endcase

        // an aborted transmission takes priority over sending and completion
        if (tx_underflow_in &&
            ((state_reg == transmit) || (state_reg == wait_result))) begin
            valid_out = 1'b0;
            // last_out = 1'b0;
            memory_read_enable = 1'b0;
            byte_count_next = 0;
            read_address_next = 0;
            state_next = collect;
        end

        // prevent transfers during reset while leaving the memory unreset
        if (reset) begin
            ready_out = 1'b0;
            valid_out = 1'b0;
            // last_out = 1'b0;
            memory_write_enable = 1'b0;
            memory_read_enable = 1'b0;
        end
    end

    assign data_out = memory_data_reg;
    assign busy_out = (state_reg != collect) || (byte_count_reg != 0);
    assign overflow_out = overflow_reg;
    // assign last_out = ({1'b0, read_address_reg} == byte_count_reg - 1'b1);
    assign last_out = (state_reg == transmit) && ({1'b0, read_address_reg} == byte_count_reg - 1'b1);

    // synthesis translate_off
    initial begin
        if (ADDR_WIDTH < 1 || ADDR_WIDTH > 30)
            $fatal(1, "ADDR_WIDTH must be between 1 and 30");

        if (MAX_FRAME_BYTES < 1 || MAX_FRAME_BYTES > BUFFER_DEPTH)
            $fatal(1, "MAX_FRAME_BYTES must fit within BUFFER_DEPTH");
    end
    // synthesis translate_on


endmodule