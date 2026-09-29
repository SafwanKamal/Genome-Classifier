module score_clock_domain_crosser #(
    parameter integer DATA_WIDTH = 32
) (
    // Source clock domain
    input  logic                  source_clk,
    input  logic                  source_reset,
    input  logic [DATA_WIDTH-1:0] source_data,
    input  logic                  source_send,
    output logic                  source_ready,

    // Destination clock domain
    input  logic                  destination_clk,
    input  logic                  destination_reset,
    output logic [DATA_WIDTH-1:0] destination_data,
    output logic                  destination_valid,
    input  logic                  destination_ready
);

    // The source holds this data unchanged until the destination
    // acknowledges that it has consumed it.
    logic [DATA_WIDTH-1:0] source_data_hold_reg;

    logic request_toggle_reg;
    logic acknowledge_toggle_reg;

    (* ASYNC_REG = "TRUE" *)
    logic request_sync_1_reg;
    (* ASYNC_REG = "TRUE" *)
    logic request_sync_2_reg;

    (* ASYNC_REG = "TRUE" *)
    logic acknowledge_sync_1_reg;
    (* ASYNC_REG = "TRUE" *)
    logic acknowledge_sync_2_reg;

    // Equal toggles mean there is no outstanding transfer.
    assign source_ready =
        (request_toggle_reg == acknowledge_sync_2_reg);

    // Source-domain storage and request generation.
    always_ff @(posedge source_clk or posedge source_reset) begin
        if (source_reset) begin
            source_data_hold_reg <= '0;
            request_toggle_reg   <= 1'b0;
        end else begin
            if (source_send && source_ready) begin
                source_data_hold_reg <= source_data;
                request_toggle_reg   <= !request_toggle_reg;
            end
        end
    end

    // Synchronize the destination acknowledgment back into
    // the source clock domain.
    always_ff @(posedge source_clk or posedge source_reset) begin
        if (source_reset) begin
            acknowledge_sync_1_reg <= 1'b0;
            acknowledge_sync_2_reg <= 1'b0;
        end else begin
            acknowledge_sync_1_reg <= acknowledge_toggle_reg;
            acknowledge_sync_2_reg <= acknowledge_sync_1_reg;
        end
    end

    // Synchronize the source request into the destination domain.
    always_ff @(posedge destination_clk or posedge destination_reset) begin
        if (destination_reset) begin
            request_sync_1_reg <= 1'b0;
            request_sync_2_reg <= 1'b0;
        end else begin
            request_sync_1_reg <= request_toggle_reg;
            request_sync_2_reg <= request_sync_1_reg;
        end
    end

    // Destination-domain data capture and acknowledgment.
    always_ff @(posedge destination_clk or posedge destination_reset) begin
        if (destination_reset) begin
            destination_data      <= '0;
            destination_valid     <= 1'b0;
            acknowledge_toggle_reg <= 1'b0;
        end else begin
            // A mismatch indicates a new source value.
            if (!destination_valid &&
                (request_sync_2_reg != acknowledge_toggle_reg)) begin

                destination_data  <= source_data_hold_reg;
                destination_valid <= 1'b1;
            end

            // Acknowledge only after the destination consumes the value.
            if (destination_valid && destination_ready) begin
                destination_valid      <= 1'b0;
                acknowledge_toggle_reg <= request_sync_2_reg;
            end
        end
    end

endmodule