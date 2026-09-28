"""Ambilight daemon: screen border color -> Lume panel, smooth and fast.

Pipeline: ambicap (capture) -> reader task (latest color) -> sender task
(fixed rate, exponential smoothing toward target) -> panel.

Usage:
  ambilight.py [--brightness 0..1] [--saturation X] [--rate FPS] [--smooth ALPHA]

ALPHA = per-tick approach factor (0.15 buttery .. 0.5 snappy). Stop with Ctrl+C.
"""

import argparse
import asyncio
import colorsys
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from lume import LumePanel


def boost(r, g, b, sat, bri):
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    s = min(1.0, s * sat)
    v = min(1.0, v * bri)
    r2, g2, b2 = colorsys.hsv_to_rgb(h, s, v)
    return r2 * 255, g2 * 255, b2 * 255


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brightness", type=float, default=0.85)
    ap.add_argument("--saturation", type=float, default=1.8)
    ap.add_argument("--rate", type=float, default=20)
    ap.add_argument("--smooth", type=float, default=0.18)
    args = ap.parse_args()

    cap = subprocess.Popen(
        [str(_HERE / "ambicap")],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)

    panel = LumePanel()
    await panel.connect()
    print(f"panel paired — ambilight ON (rate {args.rate}/s, smooth {args.smooth})")

    latest = {"rgb": (0.0, 0.0, 0.0)}
    loop = asyncio.get_running_loop()

    async def reader():
        while True:
            line = await loop.run_in_executor(None, cap.stdout.readline)
            if not line:
                return
            parts = line.split()
            if len(parts) != 3:
                continue
            try:
                r, g, b = (int(x) for x in parts)
            except ValueError:
                continue
            latest["rgb"] = boost(r, g, b, args.saturation, args.brightness)

    async def sender():
        cur = [0.0, 0.0, 0.0]
        period = 1.0 / args.rate
        last_sent = None
        sends = 0
        stat_t = loop.time()
        while True:
            t0 = loop.time()
            tgt = latest["rgb"]
            for i in range(3):
                cur[i] += (tgt[i] - cur[i]) * args.smooth
            rgb = (int(cur[0]), int(cur[1]), int(cur[2]))
            if rgb != last_sent:
                try:
                    await panel.color(*rgb)
                    last_sent = rgb
                    sends += 1
                except Exception as e:
                    print(f"panel write failed ({e}), reconnecting...")
                    try:
                        await panel.disconnect()
                    except Exception:
                        pass
                    await asyncio.sleep(2)
                    await panel.connect()
            if loop.time() - stat_t >= 10:
                print(f"actual send rate: {sends / (loop.time() - stat_t):.1f}/s")
                sends = 0
                stat_t = loop.time()
            dt = loop.time() - t0
            await asyncio.sleep(max(0.0, period - dt))

    try:
        await asyncio.gather(reader(), sender())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        cap.terminate()
        await panel.disconnect()
        print("\nambilight OFF")


if __name__ == "__main__":
    asyncio.run(main())
