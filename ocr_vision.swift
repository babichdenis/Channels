import Vision
import Foundation
import CoreGraphics
import ImageIO

// ocr_vision <image> — строки TSV: text \t x \t y \t w \t h \t conf
// боксы нормализованные (origin bottom-left), размеры картинки — первой строкой:
// SIZE \t width \t height
guard CommandLine.arguments.count > 1 else { exit(2) }
let path = CommandLine.arguments[1]
guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
      let cg = CGImageSourceCreateImageAtIndex(src, 0, nil) else { exit(3) }
print("SIZE\t\(cg.width)\t\(cg.height)")

let req = VNRecognizeTextRequest { req, err in
    guard err == nil else { exit(4) }
    for obs in (req.results as? [VNRecognizedTextObservation] ?? []) {
        guard let cand = obs.topCandidates(1).first else { continue }
        let t = cand.string.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.count < 2 { continue }
        let b = obs.boundingBox
        print("\(t)\t\(b.origin.x)\t\(b.origin.y)\t\(b.size.width)\t\(b.size.height)\t\(cand.confidence)")
    }
}
req.recognitionLanguages = ["ru", "en"]
req.recognitionLevel = .accurate
req.usesLanguageCorrection = false
let handler = VNImageRequestHandler(cgImage: cg, options: [:])
do {
    try handler.perform([req])
} catch {
    exit(5)
}
