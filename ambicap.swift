// Ambilight capture helper: vividness-weighted average color of the screen CENTER
// (single centered panel design) -> stdout "R G B".
import CoreGraphics
import Foundation

@_silgen_name("CGWindowListCreateImage")
func capf(_ r: CGRect, _ o: CGWindowListOption, _ w: CGWindowID, _ i: CGWindowImageOption) -> CGImage?

func elog(_ s: String) { FileHandle.standardError.write((s + "\n").data(using: .utf8)!) }

let b = CGDisplayBounds(CGMainDisplayID())
let W = Double(b.width), H = Double(b.height)

// Center region: 68% x 64% of the screen, skipping menu bar strip on top.
// The panel is small and centered — the center of the frame is what matters.
let cw = W * 0.68, ch = H * 0.64
let region = CGRect(x: (W - cw) / 2, y: H * 0.14, width: cw, height: ch)

elog("center region: \(region)")

var frames = 0
while true {
    autoreleasepool {
        if let img = capf(region, .optionOnScreenOnly, kCGNullWindowID, [.nominalResolution]) {
            let w = img.width, h = img.height
            if let provider = img.dataProvider, let data = provider.data,
               let ptr = CFDataGetBytePtr(data) {
                let bpr = img.bytesPerRow
                var rw = 0.0, gw = 0.0, bw = 0.0, ws = 0.0
                var y = 0
                while y < h {
                    var x = 0
                    let row = ptr + y * bpr
                    while x < w {
                        let px = row + x * 4
                        let pr = Double(px[2]), pg = Double(px[1]), pb = Double(px[0])
                        let mx = max(pr, max(pg, pb)), mn = min(pr, min(pg, pb))
                        if mx > 24 {  // skip near-black (letterbox bars / dark)
                            let sat = (mx - mn) / mx
                            let weight = sat * sat  // vivid pixels dominate
                            rw += pr * weight; gw += pg * weight; bw += pb * weight
                            ws += weight
                        }
                        x += 12
                    }
                    y += 12
                }
                if ws > 1 {
                    print("\(Int(rw / ws)) \(Int(gw / ws)) \(Int(bw / ws))")
                } else {
                    print("2 2 2")  // center fully dark — dim panel almost off
                }
                fflush(stdout)
            }
        }
    }
    frames += 1
    usleep(25_000)
}
