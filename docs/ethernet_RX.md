# Raw Ethernet receive bring-up

The RX path complements the existing 100 Mb/s RMII transmit path. It runs on
the 50 MHz clock forwarded to the PHY (`clk_out1` of `clk_wiz_ethernet`). The
transmitter still runs on the related 180-degree clock (`clk_out2`). Keep the
receive logic and its consumer on the RX clock, or add a clock-domain crossing
before using the data in the 100 MHz classifier domain.

| Module | Job |
|---|---|
| `rtl/rmii_RX.sv` | Reassemble four little-endian dibits into a byte; recognize `55` preamble and `D5` delimiter; report frame end and PHY error. A single low `CRS_DV` cycle during an active frame does not terminate it. |
| `rtl/ethernet_RX.sv` | Hold up to 1518 frame bytes including FCS, check size, PHY error and Ethernet CRC-32 residue, then stream accepted bytes without FCS. |
| `rtl/ethernet_RX_test_top.sv` | Standalone board bring-up with four sticky LEDs. |

`ethernet_RX` is a raw frame interface. It does not filter destination MACs or
EtherTypes, answer ARP, parse IP/UDP, or submit feature vectors to the
classifier. `valid_out && ready_in` accepts a byte, and `last_out` marks the
final accepted byte. The first output byte is destination MAC byte 0. The
output includes any Ethernet padding, and excludes the 8 preamble/SFD bytes
and 4 FCS bytes. `good_frame_out`, `bad_frame_out`, and `overflow_out` are
one-clock pulses. A second arrival while an accepted frame is being drained
is discarded and raises `overflow_out`. There is no RMII backpressure.

For a Nexys 4 DDR / Nexys A7-100T standalone test, add
`rtl/rmii_RX.sv`, `rtl/ethernet_CRC32.sv`, `rtl/ethernet_RX.sv`, and
`rtl/ethernet_RX_test_top.sv` as SystemVerilog sources. Generate the existing
`clk_wiz_ethernet` IP with 50 MHz 0-degree and 50 MHz 180-degree outputs.
Select `ethernet_RX_test_top` as top and use only
`constraints/ethernet_RX_test_top.xdc` as the constraints file. The board
requires a 100 Mb/s negotiated link. The top holds PHY reset for 50 ms and
then enables receive.

| LED | Meaning |
|---|---|
| 0 | PHY reset released |
| 1 | At least one valid frame received |
| 2 | At least one malformed/CRC-error frame received |
| 3 | At least one frame dropped while the previous frame was draining |

All event LEDs stay lit until reset. Regular traffic on a LAN, including ARP
and broadcasts, will light LED 1; this test does not display packet contents.
The PHY pin assignments follow Digilent's Nexys A7 master XDC. Check input
timing after place and route using the supplied provisional input delays;
the exact board skew must be verified on hardware.

Run the RTL testbench with Icarus Verilog:

```sh
iverilog -g2012 -s ethernet_RX_tb -o /tmp/ethernet_RX_tb.vvp \
  rtl/rmii_RX.sv rtl/ethernet_CRC32.sv rtl/ethernet_RX.sv \
  simulation/ethernet_RX_tb.sv
vvp /tmp/ethernet_RX_tb.vvp
```

The test covers minimum and maximum untagged frames, FCS corruption, short
frames, PHY receive error, malformed preamble, `CRS_DV` toggling, output
backpressure, and a dropped overlapping frame. This does not substitute for
on-board timing and packet-capture verification.
