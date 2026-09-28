import asyncio
from bleak import BleakScanner

async def main():
    print("Scanning 10s...")
    devices = await BleakScanner.discover(timeout=10, return_adv=True)
    rows = []
    for d, adv in devices.values():
        rows.append((adv.rssi, d.address, d.name or "", sorted(str(s) for s in adv.service_uuids or [])))
    rows.sort(reverse=True)
    for rssi, addr, name, svcs in rows:
        print(f"{rssi:>5} dBm  {addr}  {name!r:30} svcs={svcs}")

asyncio.run(main())
