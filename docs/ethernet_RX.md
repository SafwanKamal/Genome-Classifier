# Ethernet RX

This project implements raw Ethernet reception through the Nexys 4 DDR's LAN8720A PHY. The standalone RX top isolates the receiver for board diagnosis; the [combined classifier top](ethernet_classifier.md) now connects it to the classifier and TX while retaining UART. This page covers the receive path, the board failure and correction, and the physical test evidence.

## Receive path

1. The PHY places two bits on `rmii_rxd[1:0]` for each 50 MHz clock cycle.
2. `rmii_RX.sv` registers the RMII bundle together, skips carrier-acquisition zeros, checks the preamble and `D5` start delimiter, then assembles four dibits into each frame byte.
3. `ethernet_RX.sv` stores the frame in synchronous block RAM and checks its length, PHY error flag, and Ethernet CRC-32.
4. If those checks pass, the module streams the frame bytes to the next block. The preamble and FCS are removed; Ethernet padding remains.

| File | Purpose |
|---|---|
| `rtl/rmii_RX.sv` | RMII dibit-to-byte conversion and frame boundaries |
| `rtl/ethernet_RX.sv` | Frame buffer, length and CRC checks, byte output |
| `rtl/ethernet_RX_test_top.sv` | Standalone board test with LED indicators |
| `constraints/ethernet_RX_test_top.xdc` | Nexys 4 DDR PHY pins and RX timing constraints |
| `simulation/ethernet_RX_tb.sv` | Receive simulation |
| `software/send_ethernet_RX_test.py` | Known PC-to-FPGA test frame |

The RX test top forwards `clk_out1` (50 MHz, 0 degrees) to the PHY through an ODDR output register with the same polarity. Its receiver and ILA use `clk_out2` (50 MHz, 36 degrees), which samples 2 ns later to account for reference-clock output delay. All four receive inputs are captured together into input registers before decoding; this adds one receive clock of latency without changing their alignment. The separate TX test uses a 180-degree `clk_out2` configuration. Restore that phase before reusing the RX clock IP for TX. Data sent to the 100 MHz classifier will need a clock-domain crossing.

## Output interface

`valid_out && ready_in` transfers one byte. `last_out` is asserted on the final byte of the accepted frame. Byte 0 is the first byte of the destination MAC address. A received frame is held until its CRC and size pass, so the next block never sees a partial frame that later fails the FCS check.

`good_frame_out`, `bad_frame_out`, and `overflow_out` are one-clock pulses. The buffer holds one frame at a time. If another frame arrives while the first is being read, the new frame is dropped and `overflow_out` pulses. The PHY cannot be paused by `ready_in`.

The current implementation accepts raw Ethernet frames of 64 to 1518 bytes including FCS. It does not yet select a destination MAC or EtherType. ARP, IP/UDP, and classifier requests are later stages.

## Test on the Nexys 4 DDR

1. Pull the latest `main` branch.
2. In Vivado, add `rtl/rmii_RX.sv`, `rtl/ethernet_CRC32.sv`, `rtl/ethernet_RX.sv`, and `rtl/ethernet_RX_test_top.sv` as SystemVerilog design sources.
3. For this RX top, configure `clk_wiz_ethernet` with `clk_out1 = 50 MHz, 0°` and `clk_out2 = 50 MHz, 36°` (2 ns later). Regenerate output products and rerun synthesis. The TX test's 180° setting is not the RX sampling phase.
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

The testbench covers valid minimum and maximum frames, a bad FCS, a short frame, a PHY error, an invalid preamble, a temporary low `CRS_DV`, output backpressure, and a frame dropped while the buffer is occupied. Regressions also cover zero through nine leading `00` dibits, one- to three-byte preambles, PHY errors during carrier acquisition, recovery after false carrier, intermittent output stalls with byte/last stability checks, an oversized frame, and resetting while a frame is held in RAM.

## Board failure and correction: carrier before preamble

On September 30, 2026, the PC negotiated a 100 Mb/s link and the board's PHY
link/activity LED blinked during sends, but only user LED[0] was lit. LED[1]
(accepted frame) and LED[2] (failed frame checks) both stayed off.

An ILA capture showed `rmii_crs_dv_IBUF` rising at approximately sample 128,
with `rmii_rxd_IBUF[1:0]` remaining `00` until about sample 134. Repeated `01`
preamble dibits followed. `rmii_rx_er_IBUF` and `rx_reset` stayed low, but
`receiver/rx_start` never pulsed. This isolated the failure to decoding before
CRC checking. The [LAN8720A datasheet, section 3.4.1.1](https://ww1.microchip.com/downloads/en/DeviceDoc/00002165B.pdf)
specifies that carrier can precede decoded data and RXD remains `00` during
that interval.

The original decoder started byte assembly on the first carrier cycle. It
treated the initial zeros as a malformed preamble byte and silently rejected
the entire carrier event. Because no frame start reached `ethernet_RX`, neither
the good-frame nor bad-frame indicator could light.

The corrected decoder waits through the initial `00` dibits, starts alignment
on the first `01`, and recognizes the final `11` dibit of the `D5` delimiter.
It requires at least one `55` preamble byte plus the three `01` dibits of `D5`,
and checks the dibit phase so malformed preambles are still rejected. It
preserves PHY errors observed during carrier acquisition. Bytes after the
delimiter start at the destination MAC address, regardless of the leading-zero
count. Two consecutive low CRS_DV cycles still terminate a frame.

The initial ILA build also failed setup timing: WNS was -3.980 ns on a raw
PHY-input-to-decoder path, with another failing path from the decoder's data
register to the frame buffer. The buffer had synthesized into 12,144 register
bits because its write process was mixed with asynchronous reset logic and
its read was combinational.

The timing fix captures the RMII bundle in input registers, forwards REF_CLK
through an ODDR, and samples with a 2 ns receive-clock offset. The frame buffer
uses synchronous block RAM with one-byte prefetch; stalled output stays stable.
The 15 ns maximum and 2 ns minimum input-delay limits are unchanged.

For the shifted clock, input setup checks a symbol launched at 0 ns against
capture at 22 ns. Input hold checks that capture against the next symbol
launched at 20 ns. The input-only setup multicycle is 2 and hold adjustment is
0; internal register paths retain their one-cycle timing requirement. The
Clocking Wizard supplies the input clock definition without a duplicate XDC clock.

The corrected September 30, 2026 build passed the RX simulation and routed
timing checks:

| Check | Result |
|---|---|
| Worst setup slack | +1.192 ns; zero failing endpoints |
| Worst hold slack, entire design | +0.028 ns; zero failing endpoints |
| RMII input hold slack | +0.976 ns, checked at capture 22 ns / launch 20 ns |
| DRC | Zero errors; warnings remain |
| Frame buffer | One RAMB18E1 block |

The matched bitstream and probes file are in `build/ethernet_RX_ila`. The
corrected build passed the targeted physical-board checks below.

## On-board RX validation — September 30, 2026

The PC negotiated a 100 Mbps link. Before the fix, only LED[0] was lit,
although the PHY's link/activity LED blinked during sends. The original ILA
capture showed carrier followed by leading `00` dibits, with no `rx_start`.
After the fixes, LEDs **0, 1, and 3** were lit: PHY reset released, at least
one CRC-checked frame accepted, and at least one receive overflow recorded.
LED[2] was not lit. These LEDs are sticky until reset; LED[3] does not prove
that the known test frame itself overflowed.

The known-frame send used uv and Scapy:

```powershell
uv run --with scapy python software/send_ethernet_RX_test.py --iface "Ethernet" --count 10
```

The ILA used a 1024-sample capture with trigger position 128. A trigger on
`data_out[7:0] == 52` in hexadecimal reliably captured the test send. The
waveform also showed `valid_out` high at that byte. Filling 128 samples when
armed, then waiting until the send to fill the remainder, is normal: those
are the pre-trigger samples.

| Checked field | Frame byte offsets | Captured samples | Observed bytes |
|---|---|---|---|
| EtherType | 12–13 | 126–127 | `88 B5` |
| Request marker | 14–17 | 128–131 | `52 58 30 31` (`RX01`) |
| Sequence number | 18–21 | 132–135 | `00 00 00 01` |
| Features -8 through -1 | 22–29 | 136–143 | `F8 F9 FA FB FC FD FE FF` |
| Features 0 through 7 | 30–37 | 144–151 | `00 01 02 03 04 05 06 07` |
| Padding | 38–59 | 152–173 | Zero bytes |
| End of frame | 59 | 173 | `last_out` high with `valid_out`; `valid_out` low at 174 |

This confirms the intended payload and final-byte boundary at the output of
the CRC-checking receiver. The screenshots do not establish ten accepted
frames out of ten, an exhaustive check of the MAC header, or lossless operation.

Earlier captures completed without running the send because the PC also sent
background traffic. One capture had EtherType `08 06` (ARP). A `good_frame`
trigger accepts that traffic too. Combining a rising `dv_sample` trigger and
`good_frame == 1` waited indefinitely because carrier acquisition and CRC
acceptance occur at different times. For a known request, use the payload
trigger and check its surrounding bytes and `valid_out`; a byte match alone
does not identify a frame.

## Capture a known frame on the board

### ILA for a receiver that never lights LED[1] or LED[2]

`scripts/build_ethernet_RX_ila.tcl` builds an independent debug project under
`build/ethernet_RX_ila` in this workspace. It uses the same RX logic, pin constraints,
and 50 MHz clock configuration, and inserts a 1024-sample ILA after synthesis.
Run it from a PowerShell terminal with Vivado on PATH:

```powershell
vivado -mode batch -source scripts/build_ethernet_RX_ila.tcl
```

The same command rebuilds an existing dedicated ILA project. It regenerates
and synthesizes the clock IP, synthesizes the receiver, inserts the ILA, and
runs placement and routing. Setup and hold timing must pass before bitstream
generation. The resulting
`ethernet_RX_ila.bit` and `ethernet_RX_ila.ltx` are a matched pair: select both in
Hardware Manager's **Program Device** dialog (bitstream and debug probes files).
The routed design is saved in `ethernet_RX_ila_routed.dcp`, with timing in
`timing_summary.rpt`. Rebuilding the clock IP matters: regenerated HDL alone
can leave an older clock checkpoint active.

To insert the same ILA in your existing project instead, rerun synthesis with the
updated RTL, open the synthesized `ethernet_RX_test_top` design, and source
`scripts/insert_ethernet_RX_ila.tcl` in Vivado's Tcl Console. Save the debug
constraints through Vivado's **Save Constraints** workflow before launching
implementation and generating a new bitstream and debug probes file.

The corrected build probes `receiver/decoder/dv_sample`,
`receiver/decoder/rxd_sample[1:0]`, and `receiver/decoder/er_sample`: the RMII
bundle after the input registers, delayed together by one clock. It also probes
the RX reset, decoder byte/start/end/error signals, and the receive outputs.
In Hardware Manager:

1. In **Settings**, set trigger position to 128 and capture depth to 1024.
2. Add `receiver/decoder/dv_sample` to the trigger setup and select its rising-edge condition (`R`).
3. Arm using **Run Trigger**, or run
   `run_hw_ila [get_hw_ilas hw_ila_1]` in the Tcl Console (use the actual ILA name).
   Then send the ten test frames. Ambient traffic may trigger first; a capture
   without the test send can still establish PHY activity and preamble decoding.
4. Inspect `receiver/decoder/rxd_sample` at the beginning of `receiver/decoder/dv_sample`. Check for leading `00`
   dibits, preamble `01` dibits, and whether `receiver/rx_start` ever pulses.

If the ILA never triggers, check the raw PHY signal path and clock. If activity
appears without `rx_start`, investigate preamble decoding before CRC checking.
The ILA adds debug resources; it does not change the receiver state machine.
The older `_IBUF` names appear in the original failure screenshots. The fixed
build uses `_sample` signals because the dedicated buffer-to-input-register
connections cannot be probed by an ILA. Set the data bus radix to **Binary** to
inspect individual dibits.

Program the `.bit` and `.ltx` from the same `build/ethernet_RX_ila` directory.
Selecting the new `.ltx` alongside the old `Genome_Classification.runs/impl_1`
bitstream does not add an ILA: Hardware Manager will report no supported soft
debug cores and drop `u_ila_rx`. A debug-probes file describes the cores already
present in its matching bitstream.

The RX test top now marks `data_out`, `valid_out`, `last_out`, `good_frame`, `bad_frame`, and `overflow` for Vivado debug. After synthesis, open the synthesized design and use **Set Up Debug** to connect those nets to an ILA clocked by `clk_phy_50MHz`. Use at least 1024 samples. Trigger when `good_frame` is high and leave at least 128 samples after the trigger. Generate a new bitstream with the ILA and program the FPGA.

On the Windows PC, install Npcap if it is not already available. With uv, run:

```powershell
uv run --with scapy python software/send_ethernet_RX_test.py --list-interfaces
```

Arm the ILA, then send ten frames from the wired network adapter listed by Scapy:

```powershell
uv run --with scapy python software/send_ethernet_RX_test.py --iface "Ethernet" --count 10
```

Use the exact adapter name or ID returned by `--list-interfaces`. The script prints the 60 bytes expected at the FPGA output and sends a broadcast frame with EtherType `0x88B5`. Its payload starts with `RX01`, a 32-bit sequence number, and 16 sample INT8 values (`-8` through `7`), followed by zeros. The PC's Ethernet adapter supplies the FCS on the wire. `ethernet_RX` removes that FCS before output, so the ILA should show exactly the 60 printed bytes, in order, on cycles where `valid_out` is high. `last_out` should be high with byte 59.

For a preview without sending, supply a source MAC explicitly:

```powershell
uv run python software/send_ethernet_RX_test.py --dry-run --source-mac 02:00:00:00:00:02
```

Ambient LAN traffic can trigger the ILA first. If that happens, rearm it and send the test frame again. Compare the captured destination MAC (`FF` six times), EtherType (`88 B5`), and `RX01` payload before comparing all 60 bytes. Record the timing report and whether any `bad_frame` or `overflow` pulses appear.

## Request parser and next board milestone

`rtl/ethernet_request_RX.sv` parses the existing, physically verified `RX01`
format rather than adding another protocol for this step. Connect its input
to `ethernet_RX`'s data/valid/last outputs and connect `ready_out` to the
receiver's `ready_in`, using the same receive clock. It accepts broadcast or
local destination MAC `02:00:00:00:00:01`, EtherType `0x88B5`, the `RX01`
marker, a big-endian 32-bit sequence number, and 16 INT8 feature bytes.
Source MAC and trailing padding are ignored. Ethernet CRC checking remains
the upstream receiver's responsibility.

The parser asserts `request_valid` only after the frame's final byte. Its
sequence and feature outputs remain stable while a request waits for
`request_ready`; input is paused during that wait. Feature outputs retain
the original eight-bit two's-complement representation for the classifier.
Invalid destinations, unrelated traffic, incorrect markers, and truncated
requests are consumed without publishing a request.

`simulation/ethernet_request_RX_tb.sv` checks broadcast/local requests,
sequence and all features, input gaps, held output under backpressure,
ARP/wrong-marker/wrong-destination rejection, truncation, and reset recovery.
Run it with:

```powershell
iverilog -g2012 -s ethernet_request_RX_tb -o build/ethernet_request_RX_tb.vvp rtl/ethernet_request_RX.sv simulation/ethernet_request_RX_tb.sv
vvp build/ethernet_request_RX_tb.vvp
```

The parser is now connected to the combined UART/Ethernet classifier top via
request and result mailboxes. See [the combined build and board check](ethernet_classifier.md).
It is not present in the standalone RX ILA bitstream. The combined top retains
UART and separate RX/TX clock phases; its end-to-end simulation compares scores
against golden vectors. The combined board test has now returned sequence 1
and the expected score -124 for features -8 through 7; the linked page records
that result and the remaining repeated-request checks.
