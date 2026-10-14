#!/usr/bin/env swift
import Foundation
import ImageIO
import Vision

struct Sample: Codable {
    let time: Double
    let person: Bool
    let faces: Int
    let bodyPoints: Int
    let humanRects: Int
}

struct Report: Codable {
    let sampleInterval: Double
    let samples: [Sample]
    let personSamples: Int
    let totalSamples: Int
    let personRatio: Double
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(1)
}

var reportedDetectorFailures = Set<String>()

func detectPerson(_ image: CGImage) throws -> (Int, Int, Int, Bool) {
    let faceRequest = VNDetectFaceRectanglesRequest()
    let bodyRequest = VNDetectHumanBodyPoseRequest()
    let humanRequest = VNDetectHumanRectanglesRequest()
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    var failures = 0
    var errorMessages: [String] = []
    for request in [faceRequest, humanRequest, bodyRequest] {
        do { try handler.perform([request]) }
        catch {
            failures += 1
            let name = String(describing: type(of: request))
            let message = "\(name): \(error.localizedDescription)"
            errorMessages.append(message)
            if reportedDetectorFailures.insert(name).inserted {
                FileHandle.standardError.write(("Vision request failed: " + message + "\n").data(using: .utf8)!)
            }
        }
    }
    if failures == 3 {
        throw NSError(domain: "PersonCoverage", code: 1,
                      userInfo: [NSLocalizedDescriptionKey: "all Vision person detectors failed: " + errorMessages.joined(separator: "; ")])
    }
    let faces = faceRequest.results?.count ?? 0
    let humanRects = humanRequest.results?.count ?? 0
    var bodyPoints = 0
    for observation in bodyRequest.results ?? [] {
        let points = try observation.recognizedPoints(.all)
        bodyPoints = max(bodyPoints, points.values.filter { $0.confidence >= 0.2 }.count)
    }
    return (faces, bodyPoints, humanRects, faces > 0 || bodyPoints >= 4 || humanRects > 0)
}

let arguments = CommandLine.arguments
if arguments.contains("--version") {
    print("person-coverage-vision-v2")
    exit(0)
}
guard arguments.count >= 3 else {
    fail("usage: person_coverage_probe MEDIA START:END [START:END ...] [--summary]")
}
let ffmpeg = "/opt/homebrew/bin/ffmpeg"
guard FileManager.default.isExecutableFile(atPath: ffmpeg) else { fail("ffmpeg not found: \(ffmpeg)") }
let mediaPath = arguments[1]
let summaryOnly = arguments.contains("--summary")
let interval = 0.5
var ranges: [(Double, Double)] = []
for value in arguments.dropFirst(2) where value != "--summary" {
    let fields = value.split(separator: ":", maxSplits: 1).compactMap { Double($0) }
    guard fields.count == 2, fields[1] > fields[0] else { fail("invalid range: \(value)") }
    ranges.append((fields[0], fields[1]))
}

var samples: [Sample] = []
for (start, end) in ranges {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent("tb-cut-frames-\(UUID().uuidString)")
    do { try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true) }
    catch { fail("cannot create frame directory: \(error)") }
    defer { try? FileManager.default.removeItem(at: directory) }
    let outputPattern = directory.appendingPathComponent("frame_%06d.jpg").path
    let process = Process()
    process.executableURL = URL(fileURLWithPath: ffmpeg)
    process.arguments = [
        "-hide_banner", "-loglevel", "error", "-ss", String(start), "-i", mediaPath,
        "-t", String(end - start), "-vf", "fps=2,scale=480:-2", "-q:v", "3", "-f", "image2", outputPattern,
    ]
    let errors = Pipe()
    process.standardError = errors
    do { try process.run(); process.waitUntilExit() }
    catch { fail("ffmpeg frame extraction failed: \(error)") }
    if process.terminationStatus != 0 {
        let errorText = String(data: errors.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? "unknown error"
        fail("ffmpeg frame extraction failed: \(errorText)")
    }
    let frames = (try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil))?
        .filter { $0.pathExtension == "jpg" }.sorted { $0.lastPathComponent < $1.lastPathComponent } ?? []
    if frames.isEmpty { fail("ffmpeg returned no frames for \(start):\(end)") }
    for (index, url) in frames.enumerated() {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
            fail("cannot decode extracted frame: \(url.lastPathComponent)")
        }
        do {
            let result = try detectPerson(image)
            samples.append(Sample(time: start + Double(index) * interval + interval / 2.0,
                                  person: result.3, faces: result.0, bodyPoints: result.1, humanRects: result.2))
        } catch {
            fail("Vision classification failed: \(error)")
        }
    }
}

let personSamples = samples.filter(\.person).count
let ratio = samples.isEmpty ? 0.0 : Double(personSamples) / Double(samples.count)
let report = Report(sampleInterval: interval, samples: summaryOnly ? [] : samples,
                    personSamples: personSamples, totalSamples: samples.count, personRatio: ratio)
let encoder = JSONEncoder()
encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
FileHandle.standardOutput.write(try encoder.encode(report))
FileHandle.standardOutput.write("\n".data(using: .utf8)!)
