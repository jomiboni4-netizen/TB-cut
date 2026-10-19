// Detect any visible person and identify likely close-up shots. No identity matching or tracking.
import Foundation
import Vision
import ImageIO

func fail(_ reason: String) -> Never {
    FileHandle.standardError.write((reason + "\n").data(using: .utf8)!)
    exit(2)
}
if CommandLine.arguments.count == 2 && CommandLine.arguments[1] == "--version" {
    print("apple-frame-v5-person-presence-closeup")
    exit(0)
}
guard CommandLine.arguments.count == 3, CommandLine.arguments[1] == "--frame" else {
    fail("apple-frame-v5 requires --frame PNG")
}
guard let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: CommandLine.arguments[2]) as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { fail("frame decode failed") }
do {
    let humans = VNDetectHumanRectanglesRequest()
    let faces = VNDetectFaceRectanglesRequest()
    let pose = VNDetectHumanBodyPoseRequest()
    try VNImageRequestHandler(cgImage: image).perform([humans, faces, pose])
    func rect(_ box: CGRect, _ confidence: Double) -> [String: Double] {
        ["confidence": confidence, "height": Double(box.height),
         "width": Double(box.width), "area": Double(box.width * box.height)]
    }
    let humanRows = (humans.results ?? []).map { rect($0.boundingBox, Double($0.confidence)) }
    let faceRows = (faces.results ?? []).map { rect($0.boundingBox, Double($0.confidence)) }
    var poseRows: [[String: Double]] = []
    for observation in pose.results ?? [] {
        let points = try observation.recognizedPoints(.all).values.filter { $0.confidence >= 0.2 }
        if let minX = points.map({ $0.location.x }).min(), let maxX = points.map({ $0.location.x }).max(),
           let minY = points.map({ $0.location.y }).min(), let maxY = points.map({ $0.location.y }).max() {
            let width = maxX-minX, height = maxY-minY
            poseRows.append(["points": Double(points.count), "height": Double(height),
                             "width": Double(width), "area": Double(width*height)])
        }
    }
    let confidentHuman = humanRows.contains { $0["confidence", default: 0] >= 0.3 }
    let confidentFace = faceRows.contains { $0["confidence", default: 0] >= 0.3 }
    let poseSignal = poseRows.contains { $0["points", default: 0] >= 2 }
    let report: [String: Any] = ["protocol": "apple-frame-v5", "observations": [
        "body_candidates": humanRows,
        "face_candidates": faceRows,
        "pose_candidates": poseRows,
        "has_person_signal": confidentHuman || confidentFace || poseSignal
    ]]
    FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys]))
} catch { fail("Vision execution error: \(error)") }
