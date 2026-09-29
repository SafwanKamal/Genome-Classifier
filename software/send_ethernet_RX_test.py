"""Send one known raw Ethernet frame for the Nexys RX/ILA board test."""

import argparse
import time


ETHERTYPE = 0x88B5
DESTINATION = bytes.fromhex("ff ff ff ff ff ff")
FEATURES = bytes(value & 0xFF for value in range(-8, 8))


def parse_mac(value: str) -> bytes:
    parts = value.split(":")
    if len(parts) != 6 or any(len(part) != 2 for part in parts):
        raise ValueError("MAC must contain six colon-separated hex bytes")
    try:
        return bytes(int(part, 16) for part in parts)
    except ValueError as exc:
        raise ValueError("MAC contains a non-hex byte") from exc


def build_frame(source_mac: str, sequence: int) -> bytes:
    if not 0 <= sequence <= 0xFFFFFFFF:
        raise ValueError("sequence must fit in 32 bits")
    payload = b"RX01" + sequence.to_bytes(4, "big") + FEATURES
    payload += bytes(46 - len(payload))
    frame = DESTINATION + parse_mac(source_mac) + ETHERTYPE.to_bytes(2, "big") + payload
    assert len(frame) == 60  # The NIC appends the 4-byte Ethernet FCS on the wire.
    return frame


def show_frame(frame: bytes) -> None:
    print(f"Expected RX output: {len(frame)} bytes (FCS removed by FPGA)")
    for offset in range(0, len(frame), 16):
        print(f"{offset:02d}: {frame[offset:offset + 16].hex(' ')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list-interfaces", action="store_true")
    parser.add_argument("--iface", help="Scapy/Npcap name of the wired Ethernet adapter")
    parser.add_argument("--source-mac", help="Adapter MAC; inferred from --iface if omitted")
    parser.add_argument("--sequence", type=int, default=1)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Seconds between frames when count is greater than one")
    parser.add_argument("--dry-run", action="store_true", help="Print bytes without sending")
    args = parser.parse_args()

    if args.list_interfaces:
        from scapy.all import conf
        conf.ifaces.show()
        return

    if args.count < 1 or args.interval < 0:
        parser.error("count must be positive and interval must not be negative")
    if not args.dry_run and not args.iface:
        parser.error("--iface is required when sending")
    if not args.source_mac and not args.iface:
        parser.error("provide --source-mac or --iface")

    if args.source_mac:
        source_mac = args.source_mac
    else:
        from scapy.all import get_if_hwaddr
        source_mac = get_if_hwaddr(args.iface)

    for index in range(args.count):
        frame = build_frame(source_mac, args.sequence + index)
        show_frame(frame)
        if not args.dry_run:
            from scapy.all import Ether, sendp
            sendp(Ether(frame), iface=args.iface, verbose=False)
            print(f"Sent on {args.iface}")
        if index + 1 < args.count:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
