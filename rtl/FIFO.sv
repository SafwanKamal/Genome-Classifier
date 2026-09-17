module FIFO#
(
    parameter   DATA_WIDTH=8,
                ADDR_WIDTH=4
)(
    input logic clk, rst, rd, wr,
    input logic [DATA_WIDTH-1:0] wr_data,
    output logic [DATA_WIDTH-1:0] rd_data,
    output logic empty, full
);

    logic [ADDR_WIDTH-1:0] wr_addr, rd_addr;
    logic w_en, full_temp, empty_temp;

    // Asserting the enable signal when the queue is not full
    assign w_en = wr & ~full_temp;
    assign full = full_temp;
    assign empty = empty_temp;

    FIFO_controller #(
        .ADDR_WIDTH(ADDR_WIDTH)
    ) controller (
        .clk    (clk),
        .rst    (rst),
        .rd     (rd),
        .wr     (wr),
        .empty  (empty_temp),
        .full   (full_temp),
        .rd_addr(rd_addr),
        .wr_addr(wr_addr)
    );

    parameterized_reg_file #(
        .DATA_WIDTH(DATA_WIDTH),
        .ADDR_WIDTH(ADDR_WIDTH)
    ) store (
        .clk    (clk),
        .w_en   (w_en),
        .wr_data(wr_data),
        .wr_addr(wr_addr),
        .rd_data(rd_data),
        .rd_addr(rd_addr)
    );

endmodule