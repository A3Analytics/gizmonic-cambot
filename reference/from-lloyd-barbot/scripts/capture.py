#!/usr/bin/env python3
"""Grab one JPEG from the ESP32-S3-CAM over USB.

Prefers the OTG port (the chip's own USB). The TTL jack is a CH340 and stays at
115200, which is too slow for images.
"""

import argparse
import struct
import sys
import time
from pathlib import Path

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial is missing. From the repo root: python3 -m venv .venv && .venv/bin/pip install pyserial")

WCH_VID = 0x1A86
ESP_VID = 0x303A


def describe(port):
    bits = [port.device]
    if port.vid is not None:
        bits.append(f"vid={port.vid:04x}")
    if port.pid is not None:
        bits.append(f"pid={port.pid:04x}")
    if port.description:
        bits.append(port.description)
    return " ".join(bits)


def choose_port(explicit):
    ports = list(list_ports.comports())
    if explicit:
        match = next((p for p in ports if p.device == explicit), None)
        kind = "otg" if match is not None and match.vid == ESP_VID else "ttl"
        return explicit, 115200, kind

    esp = [p for p in ports if p.vid == ESP_VID]
    if len(esp) == 1:
        return esp[0].device, 115200, "otg"
    if len(esp) > 1:
        sys.exit("more than one ESP32 USB port:\n" + "\n".join(describe(p) for p in esp))

    serial_ports = [
        p for p in ports
        if p.vid == WCH_VID or "usbserial" in p.device or "wchusbserial" in p.device
    ]
    if len(serial_ports) == 1:
        return serial_ports[0].device, 115200, "ttl"
    if len(serial_ports) > 1:
        sys.exit("more than one TTL serial port:\n" + "\n".join(describe(p) for p in serial_ports))

    listing = "\n".join(describe(p) for p in ports) or "(none)"
    sys.exit("no camera USB port found. Plug a data cable into the OTG or TTL USB-C port.\n" + listing)


def main():
    parser = argparse.ArgumentParser(description="Capture one JPEG from the ESP32-S3-CAM over USB")
    parser.add_argument("--port", help="serial device, for example /dev/cu.usbmodem1101")
    parser.add_argument("--baud", type=int, help="override baud. OTG ignores this; TTL is 115200")
    parser.add_argument("-o", "--output", default="captures/frame.jpg")
    args = parser.parse_args()

    port, baud, kind = choose_port(args.port)
    if args.baud:
        baud = args.baud
    print(f"opening {port} ({kind}) at {baud}")

    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baud
    ser.timeout = 2
    ser.dtr = False
    ser.rts = False
    ser.open()
    # TinyUSB only transmits while DTR is asserted. Leave RTS alone so the
    # open does not look like the download-mode sequence.
    if kind == "otg":
        ser.dtr = True

    try:
        time.sleep(0.05)
        ser.reset_input_buffer()
        started = time.perf_counter()
        header = None
        pending = b""
        deadline = time.perf_counter() + 12
        while header is None and time.perf_counter() < deadline:
            ser.write(b"c")
            ser.flush()
            buf = bytearray()
            window = time.perf_counter() + 1.5
            while time.perf_counter() < window:
                chunk = ser.read(4096)
                if not chunk:
                    continue
                buf += chunk
                marker = buf.find(b"\xa5\x5a")
                if marker >= 0 and len(buf) >= marker + 10:
                    header = bytes(buf[marker:marker + 10])
                    pending = bytes(buf[marker + 10:])
                    break
                if len(buf) > 64 and b"\xa5\x5a" not in buf:
                    buf = buf[-9:]
        if header is None:
            sys.exit("board did not send a frame header")
        width, height, length = struct.unpack_from("<HHI", header, 2)
        if length > 200000:
            sys.exit(f"frame length {length} is not a camera jpeg")
        payload = bytearray(pending)
        while len(payload) < length:
            chunk = ser.read(length - len(payload))
            if not chunk:
                payload = None
                break
            payload += chunk
        if payload is not None:
            payload = bytes(payload)
        elapsed = time.perf_counter() - started
        if payload is None:
            sys.exit(f"timed out reading {length} jpeg bytes")
        if length == 0:
            sys.exit("camera is not ready yet")
        if not payload.startswith(b"\xff\xd8"):
            sys.exit("payload is not a JPEG (missing SOI marker)")

        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(payload)
        print(f"wrote {output} ({len(payload)} bytes, {width}x{height}) in {elapsed * 1000:.0f} ms")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
