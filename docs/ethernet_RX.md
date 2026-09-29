# Ethernet RX

The Nexys 4 DDR can now receive raw Ethernet frames through its LAN8720A PHY. The RX modules are separate from the UART classifier path for now. This page covers the receive path, how to test it on the board, and what we should build next.

## Receive path

1. The PHY places two bits on `rmii_rxd[1:0]` for each 50 MHz clock cycle.
2. `rmii_RX.sv` assembles four dibits into a byte, checks the preamble and `D5` start delimiter, and reports the end of the frame.
3. `ethernet_RX.sv` stores the frame, checks its length, PHY error flag, and Ethernet CRC-32.
4. If those checks pass, the module streams the frame bytes to the next block. The preamble and FCS are removed; Ethernet padding remains.

| File | Purpose |
|---|---|
| `rtl/rmii_RX.sv` | RMII dibit-to-byte conversion and frame boundaries |
| `rtl/ethernet_RX.sv` | Frame buffer, length and CRC checks, byte output |
| `rtl/ethernet_RX_test_top.sv` | Standalone board test with LED indicators |
| `constraints/ethernet_RX_test_top.xdc` | Nexys 4 DDR PHY pins and RX timing constraints |
| `simulation/ethernet_RX_tb.sv` | Receive simulation |
| `software/send_ethernet_RX_test.py` | Known PC-to-FPGA test frame |

The RX path uses the 50 MHz clock sent to the PHY (`clk_out1`). The existing TX path uses the related 180-degree 50 MHz clock (`clk_out2`). Data sent to the 100 MHz classifier will need a clock-domain crossing.

## Output interface

`valid_out && ready_in` transfers one byte. `last_out` is asserted on the final byte of the accepted frame. Byte 0 is the first byte of the destination MAC address. A received frame is held until its CRC and size pass, so the next block never sees a partial frame that later fails the FCS check.

`good_frame_out`, `bad_frame_out`, and `overflow_out` are one-clock pulses. The buffer holds one frame at a time. If another frame arrives while the first is being read, the new frame is dropped and `overflow_out` pulses. The PHY cannot be paused by `ready_in`.

The current implementation accepts raw Ethernet frames of 64 to 1518 bytes including FCS. It does not yet select a destination MAC or EtherType. ARP, IP/UDP, and classifier requests are later stages.

## Test on the Nexys 4 DDR

1. Pull the latest `main` branch.
2. In Vivado, add `rtl/rmii_RX.sv`, `rtl/ethernet_CRC32.sv`, `rtl/ethernet_RX.sv`, and `rtl/ethernet_RX_test_top.sv` as SystemVerilog design sources.
3. Use the existing `clk_wiz_ethernet` IP with `clk_out1 = 50 MHz, 0°` and `clk_out2 = 50 MHz, 180°`. Regenerate its output products if Vivado asks.
4. Set `ethernet_RX_test_top` as the top module. Enable `constraints/ethernet_RX_test_top.xdc` and disable the TX test and classifier XDC files for this build.
5. Run synthesis and implementation. Check the timing report, especially paths from `rmii_rxd`, `rmii_crs_dv`, and `rmii_rx_er`.
6. Generate the bitstream, program the FPGA, and connect the board to a network with a 100 Mb/s link.

| LED | What it shows |
|---|---|
| `LED[0]` | PHY reset has been released |
| `LED[1]` | At least one frame passed the receive checks |
| `LED[2]` | At least one frame failed the length, CRC, or PHY-error checks |
| `LED[3]` | A frame arrived while the previous frame was being read |

The LEDs stay on after an event until you reset the FPGA. Ordinary LAN traffic, such as ARP broadcasts, can light `LED[1]`. That confirms the RX chain can accept a frame, but it does not prove that our intended payload was received. A malformed preamble is ignored before a frame starts, so it does not light `LED[2]`.

The XDC uses Digilent's Nexys 4 DDR/Nexys A7 pin mapping. Its input delays include a provisional board-skew allowance. Review timing after implementation and verify receive behavior on the physical board.

## Simulation

The GitHub Actions workflow runs the RX testbench on pushes that change the receive RTL. To run it locally with Icarus Verilog from the repository root:

```sh
iverilog -g2012 -s ethernet_RX_tb -o ethernet_RX_tb.vvp \
  rtl/rmii_RX.sv rtl/ethernet_CRC32.sv rtl/ethernet_RX.sv \
  simulation/ethernet_RX_tb.sv
vvp ethernet_RX_tb.vvp
```

The testbench covers valid minimum and maximum frames, a bad FCS, a short frame, a PHY error, an invalid preamble, a temporary low `CRS_DV`, output backpressure, and a frame dropped while the buffer is occupied.

## Capture a known frame on the board

The RX test top now marks `data_out`, `valid_out`, `last_out`, `good_frame`, `bad_frame`, and `overflow` for Vivado debug. After synthesis, open the synthesized design and use **Set Up Debug** to connect those nets to an ILA clocked by `clk_phy_50MHz`. Use at least 1024 samples. Trigger when `good_frame` is high and leave at least 128 samples after the trigger. Generate a new bitstream with the ILA and program the FPGA.

On the Windows PC, install Npcap if it is not already available, then install Scapy:

```powershell
py -m pip install scapy
py software/send_ethernet_RX_test.py --list-interfaces
```

Arm the ILA, then send one frame from the wired network adapter listed by Scapy:

```powershell
py software/send_ethernet_RX_test.py --iface "Ethernet"
```

Use the exact adapter name or ID returned by `--list-interfaces`. The script prints the 60 bytes expected at the FPGA output and sends a broadcast frame with EtherType `0x88B5`. Its payload starts with `RX01`, a 32-bit sequence number, and 16 sample INT8 values (`-8` through `7`), followed by zeros. The PC's Ethernet adapter supplies the FCS on the wire. `ethernet_RX` removes that FCS before output, so the ILA should show exactly the 60 printed bytes, in order, on cycles where `valid_out` is high. `last_out` should be high with byte 59.

For a preview without sending, supply a source MAC explicitly:

```powershell
py software/send_ethernet_RX_test.py --dry-run --source-mac 02:00:00:00:00:02
```

Ambient LAN traffic can trigger the ILA first. If that happens, rearm it and send the test frame again. Compare the captured destination MAC (`FF` six times), EtherType (`88 B5`), and `RX01` payload before comparing all 60 bytes. Record the timing report and whether any `bad_frame` or `overflow` pulses appear.

## After the board check

Add a parser for the project request format: destination MAC, EtherType `0x88B5`, protocol version, message type, and 16 signed INT8 features. Pass one complete validated request across to the 100 MHz classifier, and use the existing Ethernet TX path to return the score. Keep UART working so both paths can be compared on the same feature vectors.
