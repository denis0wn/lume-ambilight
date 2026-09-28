"""Lume Cube Panel Pro 2.0 — direct BLE control (Telink legacy mesh).

Verified on this Mac against the real panel (2026-09-26):
- pairing: official Telink SDK flow on char 1914 (noout AES convention)
- commands: broadcast dest 0xffff (panel's unicast addr unknown; direct 0x0211 is ignored),
  plain opcode (no 0xC0), revout AES convention (AwoX/dimond), vendor bytes 11 02
- notifications on 1911 MIC-verified decrypt (revout, nonce = mac_rev[0:3] + pkt[0:5])
- after pairing, write 0x01 to 1911 to enable state push (0xdc) packets

Usage:
  lume.py on | off
  lume.py status
  lume.py color R G B          (0-255 each)
  lume.py white [t b]          (temp 0=warm..255=cool, bright 0-255)
  lume.py effect NAME [spd br] (siren|strobe|fire|cycle|police or id 0-5)

Credentials are read from the official Lume Cube app's container plist (adopt the
panel there once). Env overrides: LUME_PLIST, LUME_ADDR, LUME_MAC, LUME_MESH_NAME,
LUME_PASSWORD.
"""

import asyncio
import os
import struct
import sys
from pathlib import Path

from bleak import BleakClient
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes

# Credentials live in the official Lume Cube app's container plist once you have
# adopted the panel there (the mesh name is re-generated on every re-adoption, so
# never hardcode it). Env vars override the plist for unusual setups.
_APP_PLIST = Path(os.environ.get(
    "LUME_PLIST",
    "~/Library/Containers/com.lumecube.lumecube/Data/Library/Preferences/"
    "com.lumecube.lumecube.plist",
)).expanduser()


def _read_device_records():
    """Yield device records the official app stores in DEVICE_STORAGE_KEY."""
    import plistlib
    with open(_APP_PLIST, "rb") as f:
        p = plistlib.load(f)
    objs = plistlib.loads(p["DEVICE_STORAGE_KEY"])["$objects"]

    def rv(v):
        return objs[v.data] if isinstance(v, plistlib.UID) else v

    for o in objs:
        if isinstance(o, dict) and "deviceName" in o:
            yield {k: rv(v) for k, v in o.items()}


def _discover():
    """Resolve credentials: env vars win, the official app's plist fills the rest."""
    cfg = {"addr": None, "mac": None, "mesh_name": None, "password": None}
    try:
        rec = next(_read_device_records(), None)
        if rec:
            cfg = {
                "addr": rec.get("peripheralId"),
                "mac": rec.get("macAddress"),
                "mesh_name": rec.get("deviceName") or rec.get("localName"),
                "password": rec.get("meshPassword"),
            }
    except FileNotFoundError:
        pass
    env = {
        "addr": os.environ.get("LUME_ADDR"),
        "mac": os.environ.get("LUME_MAC"),
        "mesh_name": os.environ.get("LUME_MESH_NAME"),
        "password": os.environ.get("LUME_PASSWORD"),
    }
    merged = {k: env[k] or v for k, v in cfg.items()}
    missing = [k for k, v in merged.items() if not v]
    if missing:
        raise RuntimeError(
            "missing panel credentials: " + ", ".join(missing) +
            "\nAdopt the panel once in the official Lume Cube app (this tool reads its "
            f"container plist automatically: {_APP_PLIST}), or set "
            "LUME_ADDR / LUME_MAC / LUME_MESH_NAME / LUME_PASSWORD."
        )
    return merged


VENDOR = b"\x11\x02"                             # Telink 0x0211 LE
DEST = 0xFFFF                                    # broadcast (verified working)

UUID_NOTIFY = "00010203-0405-0607-0809-0a0b0c0d1911"
UUID_COMMAND = "00010203-0405-0607-0809-0a0b0c0d1912"
UUID_PAIR = "00010203-0405-0607-0809-0a0b0c0d1914"

C_POWER = 0xD0
C_COLOR = 0xE2
C_COLOR_BRIGHTNESS = 0xF2   # 10..100
C_WHITE_TEMPERATURE = 0xF0  # 0..127
C_WHITE_BRIGHTNESS = 0xF1   # 1..127
C_GET_STATUS = 0xDA
C_STATUS = 0xDB
C_NOTIFY = 0xDC


def enc_noout(key, value):
    """Pairing convention: reverse key+data, AES-128-ECB, no output reversal."""
    k = bytearray(key); k.reverse()
    v = bytearray(value.ljust(16, b"\x00")); v.reverse()
    return bytes(AES.new(bytes(k), AES.MODE_ECB).encrypt(bytes(v)))


def enc_revout(key, value):
    """Command/notification convention: reverse key+data AND output."""
    k = bytearray(key); k.reverse()
    v = bytearray(value.ljust(16, b"\x00")); v.reverse()
    out = bytearray(AES.new(bytes(k), AES.MODE_ECB).encrypt(bytes(v)))
    out.reverse()
    return bytes(out)


def _checksum(key, nonce, payload):
    base = bytearray(nonce + bytes([len(payload)])).ljust(16, b"\x00")
    check = enc_revout(key, bytes(base))
    for i in range(0, len(payload), 16):
        chunk = bytearray(payload[i:i + 16].ljust(16, b"\x00"))
        check = bytearray(a ^ b for a, b in zip(check, chunk))
        check = enc_revout(key, bytes(check))
    return check


def _crypt_payload(key, nonce, payload):
    base = bytearray(b"\x00" + nonce).ljust(16, b"\x00")
    result = bytearray()
    for i in range(0, len(payload), 16):
        enc_base = enc_revout(key, bytes(base))
        result += bytearray(a ^ b for a, b in zip(enc_base, bytearray(payload[i:i + 16])))
        base[0] = (base[0] + 1) & 0xFF
    return result


def _as_bytes(v):
    return v.encode() if isinstance(v, str) else bytes(v)


class LumePanel:
    def __init__(self, addr=None, dest=DEST):
        cfg = _discover()
        self.addr = addr or cfg["addr"]
        mac_hex = "".join(c for c in cfg["mac"] if c in "0123456789abcdefABCDEF")
        self.mac_rev = bytes(reversed(bytes.fromhex(mac_hex)))
        self.mesh_name = _as_bytes(cfg["mesh_name"])
        self.password = _as_bytes(cfg["password"])
        self.dest = dest
        self.sk = None
        self.client = None
        self.last_status = None

    async def connect(self):
        self.client = BleakClient(self.addr, timeout=15)
        await self.client.connect()
        randm = get_random_bytes(8)
        creds = bytes(a ^ b for a, b in zip(
            self.mesh_name.ljust(16, b"\0"), self.password.ljust(16, b"\0")))
        encd = enc_noout(bytearray(randm) + b"\0" * 8, creds)
        pkt = bytearray(17)
        pkt[0] = 0x0C
        pkt[1:9] = randm
        pkt[9:17] = encd[8:16][::-1]
        await self.client.write_gatt_char(UUID_PAIR, bytes(pkt), response=True)
        await asyncio.sleep(0.5)
        reply = await self.client.read_gatt_char(UUID_PAIR)
        if len(reply) != 17 or reply[0] == 0x0E:
            raise RuntimeError(f"pairing rejected: {bytes(reply).hex()}")
        self.sk = enc_noout(creds, bytearray(randm + bytes(reply[1:9])))[::-1]
        await self.client.start_notify(UUID_NOTIFY, self._on_notify)
        await self.client.write_gatt_char(UUID_NOTIFY, b"\x01", response=True)
        await asyncio.sleep(0.3)

    def _on_notify(self, _, data):
        raw = bytes(data)
        nonce = bytes(self.mac_rev[0:3] + raw[0:5])
        payload = _crypt_payload(self.sk, nonce, raw[7:])
        if _checksum(self.sk, nonce, payload)[0:2] != raw[5:7]:
            return  # MIC mismatch — not for us / corrupt
        dec = raw[0:7] + payload
        opcode = dec[7]
        if opcode == C_STATUS:
            self.last_status = self._parse_status(dec)
        elif opcode == C_NOTIFY:
            st = self._parse_status(dec)
            if st:
                self.last_status = st

    @staticmethod
    def _parse_status(dec):
        # Lume layout (verified 2026-09-26): params start at byte 10.
        # RGB mode: [10:13]=R,G,B. CCT mode (E2 first byte 0x06): [10]=temp, [11]=brightness.
        if len(dec) < 17:
            return None
        return {
            "p10_temp_or_r": dec[10],
            "p11_bright_or_g": dec[11],
            "p12_b": dec[12],
            "params": dec[10:20].hex(),
        }

    async def send(self, opcode, data=()):
        s = get_random_bytes(3)
        nonce = bytes(self.mac_rev[0:4]) + b"\x01" + s
        payload = (self.dest.to_bytes(2, "little") + bytes([opcode]) + VENDOR + bytes(data)).ljust(15, b"\x00")
        mic = _checksum(self.sk, nonce, payload)
        packet = s + mic[0:2] + _crypt_payload(self.sk, nonce, payload)
        await self.client.write_gatt_char(UUID_COMMAND, packet, response=False)

    async def status(self, wait=2.0):
        self.last_status = None
        await self.send(C_GET_STATUS)
        await asyncio.sleep(wait)
        return self.last_status

    async def on(self):
        await self.send(C_POWER, [0x01])

    async def off(self):
        await self.send(C_POWER, [0x00])

    async def color(self, r, g, b):
        await self.send(C_COLOR, struct.pack("BBBB", 0x04, r, g, b))

    async def color_brightness(self, level):
        await self.send(C_COLOR_BRIGHTNESS, [max(10, min(100, level))])

    async def white(self, temperature=0x80, brightness=0xFF):
        """CCT white. Verified on Panel Pro 2.0: E2 [0x06, temp, bright], both 0-255.
        temp: 0x00 = warmest .. 0xFF = coolest."""
        t = max(0, min(0xFF, temperature))
        b = max(0, min(0xFF, brightness))
        await self.send(C_COLOR, [0x06, t, b])

    # Effects (user-verified 2026-09-26): E2 [0x0A, N, speed, bright]
    EFFECTS = {
        "siren": 0,      # мигалки
        "strobe": 1,     # белые вспышки
        "fire": 2,       # огонь
        "cycle": 3,      # цвета по кругу
        "police": 5,     # красный/синий полиция
    }

    async def effect(self, name_or_id, speed=0xFF, brightness=0xFF):
        """Run a firmware effect: E2 [0x0A, id, speed, brightness]."""
        if isinstance(name_or_id, str):
            n = self.EFFECTS[name_or_id.lower()]
        else:
            n = int(name_or_id)
        await self.send(C_COLOR, [0x0A, n, max(0, min(0xFF, speed)), max(0, min(0xFF, brightness))])

    async def disconnect(self):
        if self.client:
            try:
                await self.client.stop_notify(UUID_NOTIFY)
            except Exception:
                pass
            await self.client.disconnect()


async def _main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    panel = LumePanel()
    await panel.connect()

    cmd = args[0]
    if cmd == "on":
        await panel.on()
    elif cmd == "off":
        await panel.off()
    elif cmd == "status":
        st = await panel.status()
        print("status:", st if st else "NO ANSWER")
    elif cmd == "color" and len(args) == 4:
        await panel.color(*[int(x) for x in args[1:4]])
    elif cmd == "cbright" and len(args) == 2:
        await panel.color_brightness(int(args[1]))
    elif cmd == "white":
        t = int(args[1]) if len(args) > 1 else 0x80
        b = int(args[2]) if len(args) > 2 else 0xFF
        await panel.white(t, b)
    elif cmd == "effect" and len(args) >= 2:
        spd = int(args[2]) if len(args) > 2 else 0xFF
        bri = int(args[3]) if len(args) > 3 else 0xFF
        name = args[1] if not args[1].isdigit() else int(args[1])
        await panel.effect(name, spd, bri)
    else:
        print(__doc__)
        await panel.disconnect()
        return

    await panel.disconnect()
    print("ok")


if __name__ == "__main__":
    asyncio.run(_main())
