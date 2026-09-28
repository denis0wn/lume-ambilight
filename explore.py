"""GATT dump for a Lume panel.

Usage:
  explore.py [COREBLUETOOTH_UUID]   (default: auto-discovered from the Lume app plist)
"""
import asyncio
import sys

from bleak import BleakClient

from lume import _discover


async def main():
    addr = sys.argv[1] if len(sys.argv) > 1 else _discover()["addr"]
    async with BleakClient(addr, timeout=20) as client:
        print(f"connected: {client.is_connected}")
        for service in client.services:
            print(f"\n[SERVICE] {service.uuid}  {service.description}")
            for ch in service.characteristics:
                props = ",".join(ch.properties)
                print(f"  [CHAR] {ch.uuid}  ({props})  {ch.description}")
                if "read" in ch.properties:
                    try:
                        val = await client.read_gatt_char(ch.uuid)
                        print(f"         read: {val.hex()}  {val!r}")
                    except Exception as e:
                        print(f"         read failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
