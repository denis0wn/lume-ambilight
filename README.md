# lume-ambilight

**DIY Ambilight for your Mac — over BLE, no hardware mods.**
A [Lume Cube Panel Pro 2.0](https://lumecube.com) placed behind your display follows
the on-screen color in real time, driven straight from Python. No official app required
(after a one-time adoption), no cloud, no root.

Everything here was reverse-engineered from the vendor app and verified against the real
panel — including the full Telink legacy-mesh protocol (pairing crypto, packet format,
MIC, opcodes) and the OTA channel. [README на русском →](README.ru.md)

## Features

- 🎬 **Ambilight mode** — the panel tracks the screen's dominant color at ~18 fps
  capture / ~19 BLE updates per second, with exponential smoothing (no flicker).
- 🎛 **Full CLI** — power, RGB, tunable white (CCT), firmware effects, status readback
  with MIC verification.
- 🎨 **Software effects** — candle, lightning storm, rainbow, heartbeat, police strobe,
  aurora. Anything the firmware can't do, Python can.
- 📡 **Documented protocol** — the first public write-up of the Panel Pro 2.0's
  Telink legacy-mesh GATT protocol (see [Protocol](#the-protocol) below).
- 🔌 **Zero-config credentials** — if you paired the panel once with the official app,
  this tool reads the mesh name, password, MAC and CoreBluetooth UUID from the app's
  container plist automatically. Nothing to hardcode.

## How ambilight works

```
screen ── CGWindowListCreateImage ──▶ center region (68% × 64%)
                                          │  saturation²-weighted average,
                                          │  near-black skipped (~18 fps)
                                          ▼
                        ambilight.py daemon: sat/brightness boost,
                        exponential lerp toward target (α 0.18)
                                          │  ~19 sends/s
                                          ▼
                  Lume panel over BLE (encrypted Telink mesh packet)
```

Design choices (A/B-tested against a real panel):

- **Center, not edges.** One small panel behind the display matches the frame's center;
  edge-averaging washed the color out.
- **Saturation² weighting.** Vivid pixels dominate; grey UI chrome doesn't drag the
  color toward beige.
- **Skip near-black.** Letterbox bars and dark scenes would otherwise kill the light;
  instead the last real color decays gently.
- **Rate-limited smoothing.** Raw capture is jittery; the lerp makes transitions look
  like the panel *breathes* with the picture.

Tunables: `--brightness` (default 0.85), `--saturation` (1.8), `--rate` (20),
`--smooth` (0.18 = buttery … 0.5 = snappy).

## Quickstart

**Requirements:** macOS with Bluetooth, Python 3.10+, Xcode command-line tools
(for the capture helper), and a Lume Cube Panel Pro 2.0.

```sh
git clone https://github.com/denis0wn/lume-ambilight
cd lume-ambilight
python3 -m venv venv && venv/bin/pip install -r requirements.txt
swiftc -O ambicap.swift -o ambicap          # screen capture helper
```

**One-time adoption.** Pair the panel with the official Lume Cube app once
(you only need its credentials — the app itself can stay closed afterwards).
The tool then auto-discovers everything from the app's container plist.

```sh
venv/bin/python lume.py status              # should print current panel state
venv/bin/python ambilight.py                # panel follows your screen; Ctrl+C to stop
```

> macOS note: CoreBluetooth hides real MAC addresses and gives each Mac its own
> peripheral UUID, so both values are read from the official app's record. On Linux,
> bleak uses the real MAC directly — set `LUME_ADDR` accordingly.

No official app on this machine? Set the env vars instead:
`LUME_ADDR`, `LUME_MAC`, `LUME_MESH_NAME`, `LUME_PASSWORD`
(or point `LUME_PLIST` at a plist copied from another Mac).

## CLI reference

```sh
venv/bin/python lume.py on                   # power on
venv/bin/python lume.py off                  # power off
venv/bin/python lume.py status               # state readback (MIC-verified)
venv/bin/python lume.py color 255 0 0        # RGB, 0-255 each
venv/bin/python lume.py white 128 255        # CCT white: temp 0=warm..255=cool, brightness
venv/bin/python lume.py effect fire          # siren | strobe | fire | cycle | police
venv/bin/python effects.py aurora            # software effects: candle | lightning |
                                             # rainbow | heartbeat | police2 | aurora
venv/bin/python scan.py                      # find the panel (advertises as "Telink tLight")
venv/bin/python explore.py                   # full GATT dump
```

The panel holds **one** BLE connection: while the official app is connected, this tool
can't be (and vice versa).

## The protocol

Lume's Panel Pro 2.0 speaks **Telink legacy mesh** (not Bluetooth SIG Mesh).
GATT service `00010203-0405-0607-0809-0a0b0c0d1910` with four characteristics:

| Char | Role |
|---|---|
| `…1914` | pairing (login) |
| `…1912` | commands |
| `…1911` | status notifications |
| `…1913` | OTA |

### Pairing (official Telink SDK flow)

1. Generate `randm` (8 random bytes).
2. `enc = AES-128-ECB(key=reverse(randm ‖ 0⁸), data=reverse(name ⊕ password))` —
   `name` is the panel's *mesh name* (its BLE localName, re-generated by the app on
   every re-adoption), `password` the 8-byte mesh password; both zero-padded to 16.
3. Write `0x0C ‖ randm ‖ reverse(enc[8:16])` to `1914`. Response is
   `0x0D ‖ rands ‖ proof`; `0x0E` means wrong credentials.
4. Session key `sk = reverse(AES(name ⊕ password, randm ‖ rands))`.
5. Write `0x01` to `1911` to enable state push (`0xDC`) notifications.

### Command packets (20 bytes → `1912`)

```
seq(3) | MIC(2) | enc( dest(2, LE) | opcode | vendor(11 02) | params… )
```

- AES convention reverses key, input **and output** (the dimond/AwoX convention).
- Nonce: `mac_rev[0:4] ‖ 01 ‖ seq`. MIC is a CBC-MAC-style chain over the payload.
- **Send to broadcast destination `0xFFFF`** — the panel ignores unicast.
- Notifications decrypt with nonce `mac_rev[0:3] ‖ pkt[0:5]`.

### Opcodes (live-verified)

| Command | Opcode | Payload |
|---|---|---|
| Power on/off | `0xD0` | `[01]` / `[00]` |
| RGB color | `0xE2` | `[04, R, G, B]` |
| CCT white | `0xE2` | `[06, temp, bright]` (both 0-255) |
| FX effect | `0xE2` | `[0A, N, speed, bright]` — N: 0 siren, 1 strobe, 2 fire, 3 cycle, 5 police |
| Get status | `0xDA` | answer `0xDB` on `1911`; params from byte 10 |

Classic AwoX opcodes (`0xF0/0xF1/0xF2`, `0x33`, `0xC8`, `0xD2`, `0xEE`) are accepted
silently but do nothing on this firmware (V0.C). The effect opcodes were found by
reversing the official app's objc thunks (`onFireTouched` etc. → `[0A, N]` structs in
`__data`).

### OTA — read before touching

`ota.py` implements the legacy Telink OTA stream (`[00FF]` prepare → `[01FF]` start →
16-byte packets with Modbus-CRC16 → 4-byte empty finisher → `[02FF…]` end). It is
included for completeness and forensics. **Caveats from live experiments:**

- The panel validates the image's target product id (offset `0x1C`, magic `0x8026`);
  a mismatched image is fully received, then rejected.
- A failed `PREPARE` can leave the panel stuck in OTA mode until the official app
  re-adopts it (which rotates the mesh name).
- Do not write to `1913` unless you mean it.

## Limitations & gotchas

- **Single-zone panel.** One RGB+W channel for the whole matrix — no per-LED control,
  so this is ambient bias lighting, not a gradient wall.
- **One BLE connection at a time** (panel firmware constraint).
- **macOS captures only**, for now — `ambicap` uses `CGWindowListCreateImage`;
  the BLE layer itself is cross-platform (bleak).
- Don't send `0xE0` (mesh address change) or `0xE3` (factory reset) casually.

## Repo map

| File | What it is |
|---|---|
| `lume.py` | Protocol implementation + CLI (pairing, crypto, commands, status) |
| `ambilight.py` | Ambilight daemon (capture → smooth → BLE) |
| `ambicap.swift` | Screen capture helper (prints `R G B` lines at ~40 Hz max) |
| `effects.py` | Software effect library |
| `scan.py` / `explore.py` | BLE discovery and GATT dump |
| `ota.py` | OTA transmitter (read the warnings above) |

## License & disclaimer

MIT. This project is not affiliated with Lume Cube or Telink. Protocol details were
obtained by reverse-engineering software you installed yourself; you are responsible
for complying with your local rules and for anything you do to your hardware.
