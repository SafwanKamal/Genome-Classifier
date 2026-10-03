# FPGA synthesis notes

## DSP inference and attribute placement

During the classifier implementation, Vivado initially mapped the MAC
multiplications into LUT logic instead of the FPGA's DSP blocks. That mapping
used fabric inefficiently and contributed to negative timing slack. The
`use_dsp` attribute was needed for the intended mapping in this project;
writing a multiplication expression alone did not produce that result.

Placement matters. The current `rtl/dense_engine.sv` places the attribute
directly on each signed product signal inside the per-lane generate block:

```systemverilog
(* use_dsp = "yes" *)
logic signed [PRODUCT_WIDTH-1:0] lane_product;
assign lane_product = weight_reg[lane] * input_data_reg[input_index_reg];
```

This targets the multiplier rather than applying the directive to the whole
module and encouraging unrelated arithmetic to use DSPs. A commented
attribute has no effect. The older `dense_neuron.sv` has a module-level
attribute; the current classifier uses `dense_engine.sv` and its narrower
product-level placement. Check the synthesized DSP48E1 count and utilization
report after changing this code, alongside the timing report. Simulation
checks the arithmetic but does not establish DSP mapping.

The combined Ethernet classifier's synthesis report records **five DSP48E1
blocks**: four hidden-layer MAC lanes and one output-layer lane. The report
is generated at `build/ethernet_classifier/synthesis_utilization.rpt` by
`scripts/build_variant_triage_ethernet.tcl`.

## Synchronizer attributes

`ASYNC_REG` placement matters too: put it immediately before the declarations
of the actual synchronizer flip-flops. The request/acknowledge mailbox in
`rtl/score_clock_domain_crosser.sv` marks both stages of each toggle
synchronizer. The combined Ethernet top similarly marks the two-stage reset
release registers in each clock domain. A directive attached to another
declaration or a combinational next-state signal does not mark the intended
register chain.

These attributes identify the synchronizers for implementation tools; they
do not replace a crossing protocol. The mailbox holds its entire data bundle
stable while the synchronized request is pending and acknowledges it only
after the destination consumes it. Feature bytes are transferred together,
not synchronized individually. Check the implemented CDC report as well as
the attributes on the actual registers.
