// Generic human detection only. This tool intentionally cannot emit HOST_VISIBLE.
import Foundation
import Vision
import ImageIO

func fail(_ reason: String) -> Never {
    FileHandle.standardError.write((reason + "\n").data(using: .utf8)!)
    exit(2)
}
guard CommandLine.arguments.count == 3, CommandLine.arguments[1] == "--frame" else {
    fail("apple-frame-v3 requires --frame PNG")
}
guard let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: CommandLine.arguments[2]) as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { fail("frame decode failed") }
do {
    let humans = VNDetectHumanRectanglesRequest()
    let faces = VNDetectFaceRectanglesRequest()
    try VNImageRequestHandler(cgImage: image).perform([humans, faces])
    let person = (humans.results ?? []).contains { $0.confidence >= 0.5 }
        || (faces.results ?? []).contains { $0.confidence >= 0.5 }
    let report: [String: Any] = ["protocol": "apple-frame-v3", "person_present": person,
                              "host_verified": false, "label": person ? "UNCERTAIN" : "NO_PERSON"]
    FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys]))
} catch { fail("Vision execution error: \(error)") }
