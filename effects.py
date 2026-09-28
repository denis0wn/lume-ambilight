"""Custom software effects for the Lume panel — anything the firmware doesn't have.

Each effect is a Python coroutine sending colors at ~10-15 fps. Stop with Ctrl+C.

Usage:
  effects.py candle      — тёплая свеча (случайное мерцание)
  effects.py lightning   — гроза: тёмно-синий фон + случайные вспышки
  effects.py rainbow     — плавный перелив по спектру
  effects.py heartbeat   — пульс сердца (два удара)
  effects.py police2     — красный/синий строб с разгоном
  effects.py aurora      — медленные северные волны (зелёно-фиолетовые)
"""

import asyncio
import colorsys
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lume import LumePanel


async def candle(p):
    while True:
        # warm base 2200K-ish + random flicker
        base = 0.75 + random.uniform(-0.12, 0.12)
        v = max(0.15, min(1.0, base + random.uniform(-0.08, 0.08)))
        r, g, b = colorsys.hsv_to_rgb(0.075, 0.92, v)
        await p.color(int(r * 255), int(g * 255), int(b * 255))
        await asyncio.sleep(random.uniform(0.05, 0.18))


async def lightning(p):
    while True:
        # dark blue ambient, random lightning strikes
        await p.color(4, 6, 26)
        await asyncio.sleep(random.uniform(1.5, 5.0))
        for _ in range(random.randint(1, 3)):
            await p.color(255, 255, 255)
            await asyncio.sleep(0.06)
            await p.color(20, 24, 60)
            await asyncio.sleep(random.uniform(0.04, 0.12))
            await p.color(255, 255, 255)
            await asyncio.sleep(0.05)
        await p.color(4, 6, 26)


async def rainbow(p):
    t = 0.0
    while True:
        r, g, b = colorsys.hsv_to_rgb(t % 1.0, 0.95, 0.85)
        await p.color(int(r * 255), int(g * 255), int(b * 255))
        t += 0.004
        await asyncio.sleep(0.08)


async def heartbeat(p):
    while True:
        for v in (0.9, 0.25, 0.7, 0.1):
            r, g, b = colorsys.hsv_to_rgb(0.995, 0.95, v)
            await p.color(int(r * 255), int(g * 255), int(b * 255))
            await asyncio.sleep(0.14)
        await p.color(8, 0, 0)
        await asyncio.sleep(0.75)


async def police2(p):
    while True:
        for _ in range(2):
            await p.color(255, 0, 0)
            await asyncio.sleep(0.09)
            await p.color(0, 0, 0)
            await asyncio.sleep(0.05)
        for _ in range(2):
            await p.color(0, 0, 255)
            await asyncio.sleep(0.09)
            await p.color(0, 0, 0)
            await asyncio.sleep(0.05)


async def aurora(p):
    t = 0.0
    while True:
        h = 0.33 + 0.12 * (1 + __import__("math").sin(t * 0.7)) / 2  # green..violet drift
        v = 0.35 + 0.3 * (1 + __import__("math").sin(t * 1.3)) / 2
        r, g, b = colorsys.hsv_to_rgb(h % 1.0, 0.85, v)
        await p.color(int(r * 255), int(g * 255), int(b * 255))
        t += 0.03
        await asyncio.sleep(0.1)


EFFECTS = {
    "candle": candle, "lightning": lightning, "rainbow": rainbow,
    "heartbeat": heartbeat, "police2": police2, "aurora": aurora,
}


async def main():
    if len(sys.argv) < 2 or sys.argv[1] not in EFFECTS:
        print(__doc__)
        return
    name = sys.argv[1]
    p = LumePanel()
    await p.connect()
    print(f"effect '{name}' running — Ctrl+C to stop")
    try:
        await EFFECTS[name](p)
    except KeyboardInterrupt:
        pass
    finally:
        await p.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
