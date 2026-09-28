"""OTA firmware update for the Lume panel (legacy Telink protocol).

Flow: pair -> 1913 <- [00 FF] (prepare) -> [01 FF] (start) -> 20-byte packets
[idx LE][16 data][crc16 LE] -> [02 FF, idx, ~idx, crc] (end) -> panel reboots.

Usage:
  ota.py probe                — connect, send prepare, read back, DON'T flash (safe)
  ota.py flash FIRMWARE.bin   — full update to the given firmware image
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lume import LumePanel

UUID_OTA = "00010203-0405-0607-0809-0a0b0c0d1913"


def crc16(data):
    crc = 0xFFFF
    for byte in data:
        ds = byte
        for _ in range(8):
            crc = ((crc >> 1) ^ (0xA001 if (crc ^ ds) & 1 else 0)) & 0xFFFF
            ds >>= 1
    return crc


def build_packets(fw):
    """Match the legacy OtaPacketParser exactly: total = len//16 (+1 if remainder);
    LAST packet carries the remaining bytes (no extra empty packet)."""
    length = len(fw)
    total = length // 16 if length % 16 == 0 else length // 16 + 1
    packets = []
    for index in range(total):
        packet = bytearray(b"\xFF" * 20)
        chunk = fw[index * 16:(index + 1) * 16]
        packet[2:2 + len(chunk)] = chunk
        packet[0] = index & 0xFF
        packet[1] = (index >> 8) & 0xFF
        crc = crc16(packet[:-2])
        packet[18] = crc & 0xFF
        packet[19] = (crc >> 8) & 0xFF
        packets.append(bytes(packet))
    return packets, total


async def connect_paired():
    p = LumePanel()
    await p.connect()
    return p


async def connect_plain():
    """Connect without pairing — for panels already in OTA mode."""
    from bleak import BleakClient
    client = BleakClient(LumePanel().addr, timeout=15)
    await client.connect()

    class Bare:
        pass
    p = Bare()
    p.client = client
    async def disconnect():
        await client.disconnect()
    p.disconnect = disconnect
    return p


async def probe():
    p = await connect_paired()
    print("paired; OTA char read:")
    val = await p.client.read_gatt_char(UUID_OTA)
    print("  1913 =", bytes(val).hex())
    print("  -> send PREPARE [00 FF]")
    await p.client.write_gatt_char(UUID_OTA, b"\x00\xFF", response=False)
    await asyncio.sleep(0.8)
    val = await p.client.read_gatt_char(UUID_OTA)
    print("  1913 after prepare =", bytes(val).hex())
    await p.disconnect()
    print("probe done (nothing flashed)")


async def flash():
    if len(sys.argv) < 3:
        sys.exit("usage: ota.py flash FIRMWARE.bin")
    with open(sys.argv[2], "rb") as f:
        fw = f.read()
    print(f"firmware: {len(fw)} bytes")
    packets, total = build_packets(fw)
    print(f"packets: {total}")

    if "--nopair" in sys.argv:
        p = await connect_plain()
        print("connected WITHOUT pairing (OTA mode)")
    else:
        p = await connect_paired()
        print("paired")
    await p.client.write_gatt_char(UUID_OTA, b"\x00\xFF", response=False)  # prepare
    await asyncio.sleep(0.8)
    print("prepare sent; 1913 =", (await p.client.read_gatt_char(UUID_OTA)).hex())
    await p.client.write_gatt_char(UUID_OTA, b"\x01\xFF", response=False)  # start
    await asyncio.sleep(0.8)
    print("start sent; streaming packets...")

    t0 = time.time()
    for i, pkt in enumerate(packets):
        await p.client.write_gatt_char(UUID_OTA, pkt, response=False)
        if i % 200 == 0 or i == total - 1:
            dt = time.time() - t0
            rate = (i + 1) / dt if dt else 0
            eta = (total - i - 1) / rate if rate else 0
            print(f"  {i + 1}/{total} ({(i + 1) * 100 // total}%) elapsed {dt:.0f}s eta {eta:.0f}s", flush=True)
        if i == 0:
            await asyncio.sleep(0.3)  # let firmware settle after first packet
        else:
            await asyncio.sleep(0.02)

    # 4-byte EMPTY finisher (retsimx device-side logic: n_data_len==0 with idx==total
    # triggers "ota ok, save, reboot"). CRC over the 2 index bytes only.
    fin = bytearray(4)
    fin[0] = total & 0xFF
    fin[1] = (total >> 8) & 0xFF
    fcrc = crc16(fin[:2])
    fin[2] = fcrc & 0xFF
    fin[3] = (fcrc >> 8) & 0xFF
    await p.client.write_gatt_char(UUID_OTA, bytes(fin), response=False)
    print(f"finisher sent (index={total})", flush=True)
    await asyncio.sleep(0.5)

    index = total - 1  # parser's index after last packet = total-1 (END carries it)
    end = bytearray(8)
    end[0], end[1] = 0x02, 0xFF
    end[2] = index & 0xFF
    end[3] = (index >> 8) & 0xFF
    end[4] = ~index & 0xFF
    end[5] = (~index >> 8) & 0xFF
    crc = crc16(end[:6])
    end[6] = crc & 0xFF
    end[7] = (crc >> 8) & 0xFF
    await p.client.write_gatt_char(UUID_OTA, bytes(end), response=False)
    print("END sent — panel should reboot into new firmware")
    await asyncio.sleep(2)
    try:
        val = await p.client.read_gatt_char(UUID_OTA)
        print("1913 =", bytes(val).hex())
    except Exception as e:
        print("read after end failed (device rebooting?):", e)
    try:
        await p.disconnect()
    except Exception:
        pass


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("probe", "flash"):
        print(__doc__)
    elif sys.argv[1] == "probe":
        asyncio.run(probe())
    else:
        asyncio.run(flash())
