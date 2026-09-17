module parameterized_reg_file 
#(
    parameter DATA_WIDTH = 8,
    parameter ADDR_WIDTH = 2
)(
    input logic clk,
    input logic w_en,
    input logic [DATA_WIDTH-1:0] wr_data,
    input logic [ADDR_WIDTH-1:0] wr_addr,
    output logic [DATA_WIDTH-1:0] rd_data,
    input logic [ADDR_WIDTH-1:0] rd_addr
);

    // Very compact logic here
    // But synthesizing softwares are able to decode wr_addr and rd_addr
    // to array indices
    localparam DATA_DEPTH = 2**ADDR_WIDTH;
    logic [DATA_WIDTH - 1:0] reg_file [0:DATA_DEPTH - 1];

    always_ff @(posedge clk) begin
        if(w_en) reg_file[wr_addr] <= wr_data;
    end

    always_comb begin
        rd_data = reg_file[rd_addr];
    end

endmodule

