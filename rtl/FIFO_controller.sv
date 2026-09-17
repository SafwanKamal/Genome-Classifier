module FIFO_controller#
(
    parameter ADDR_WIDTH = 4
)(
    input logic clk, rst, rd, wr,
    output logic empty, full,
    output logic [ADDR_WIDTH-1:0] rd_addr, wr_addr
);

    // Will use the overflow of the address to circle back to 0, so, the FIFO will be a circular buffer


    logic [ADDR_WIDTH-1:0] rd_addr_reg, wr_addr_reg, rd_addr_next, wr_addr_next, rd_addr_plus1, wr_addr_plus1;
    logic empty_reg, empty_next, full_reg, full_next;

    always_ff @(posedge clk, posedge rst) begin
        if (rst) begin
            empty_reg <= 1;
            full_reg <= 0;
            rd_addr_reg <= 0;
            wr_addr_reg <= 0;
        end else begin
            empty_reg <= empty_next;
            full_reg <= full_next;
            rd_addr_reg <= rd_addr_next;
            wr_addr_reg <= wr_addr_next;
        end
    end

    always_comb begin
        // Potential values for the _reg and _next if read/write is done
        rd_addr_plus1 = rd_addr_reg + 1'b1;
        wr_addr_plus1 = wr_addr_reg + 1'b1;

        // Next default values for the registers
        full_next = full_reg;
        empty_next = empty_reg;
        rd_addr_next = rd_addr_reg;
        wr_addr_next = wr_addr_reg;

        case({rd, wr})
            // write
            2'b01:begin
                if(!full_reg) begin
                    empty_next = 0;
                    wr_addr_next = wr_addr_plus1;
                    if(wr_addr_plus1 == rd_addr_reg) begin
                        full_next = 1;
                    end
                end
            end
            // read
            2'b10:begin
                if(!empty_reg) begin
                    full_next = 0;
                    rd_addr_next = rd_addr_plus1;
                    if(rd_addr_plus1 == wr_addr_reg) begin
                        empty_next = 1;
                    end
                end
            end
            // read-write
            2'b11:begin
                // Do not need to check for full/empty flags,
                // because, if both read and write are happening at the same time,
                // then, the FIFO is neither getting more full nor more empty
                // Actually, this will create a problem.
                // So, just to be safe, we will check for full/empty flags and not allow read/write if the FIFO is full/empty
                if(!full_reg && !empty_reg) begin
                    rd_addr_next = rd_addr_plus1;
                    wr_addr_next = wr_addr_plus1;
                end else if(full_reg && !empty_reg) begin
                    // only read is allowed
                    rd_addr_next = rd_addr_plus1;
                    full_next = 0;
                end else if(!full_reg && empty_reg) begin
                    // only write is allowed
                    wr_addr_next = wr_addr_plus1;
                    empty_next = 0;
                end
            end
            // no read/write
            2'b00:begin
                // do nothing, just stay in the same state
                rd_addr_next = rd_addr_reg;
                wr_addr_next = wr_addr_reg;
            end
        endcase
    end

    always_comb begin
        rd_addr = rd_addr_reg;
        wr_addr = wr_addr_reg;
        empty = empty_reg;
        full = full_reg;
    end

endmodule