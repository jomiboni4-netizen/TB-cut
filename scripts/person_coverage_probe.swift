#!/usr/bin/env swift
import AVFoundation
import Foundation
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

let arguments = CommandLine.arguments
guard arguments.count >= 3 else {
    fail("usage: person_coverage_probe MEDIA START:END [START:END ...]")
}

let mediaPath = arguments[1]
let summaryOnly = arguments.contains("--summary")
let interval = 0.5
var ranges: [(Double, Double)] = []
for value in arguments.dropFirst(2) where value != "--summary" {
    let fields = value.split(separator: ":", maxSplits: 1).compactMap { Double($0) }
    guard fields.count == 2, fields[1] > fields[0] else { fail("invalid range: \(value)") }
    ranges.append((fields[0], fields[1]))
}

let asset = AVURLAsset(url: URL(fileURLWithPath: mediaPath))
let generator = AVAssetImageGenerator(asset: asset)
generator.appliesPreferredTrackTransform = true
generator.requestedTimeToleranceBefore = CMTime(seconds: 0.08, preferredTimescale: 600)
generator.requestedTimeToleranceAfter = CMTime(seconds: 0.08, preferredTimescale: 600)

var samples: [Sample] = []
for (start, end) in ranges {
    var time = start + interval / 2.0
    while time < end {
        do {
            let image = try generator.copyCGImage(at: CMTime(seconds: time, preferredTimescale: 600), actualTime: nil)
            let faceRequest = VNDetectFaceRectanglesRequest()
            let bodyRequest = VNDetectHumanBodyPoseRequest()
            let humanRequest = VNDetectHumanRectanglesRequest()
            faceRequest.usesCPUOnly = true
            bodyRequest.usesCPUOnly = true
            humanRequest.usesCPUOnly = true
            let handler = VNImageRequestHandler(cgImage: image, options: [:])
            try handler.perform([faceRequest, bodyRequest, humanRequest])
            let faces = faceRequest.results?.count ?? 0
            let humanRects = humanRequest.results?.count ?? 0
            var bodyPoints = 0
            for observation in bodyRequest.results ?? [] {
                let points = try observation.recognizedPoints(.all)
                bodyPoints = max(bodyPoints, points.values.filter { $0.confidence >= 0.2 }.count)
            }
            samples.append(Sample(time: time, person: faces > 0 || bodyPoints >= 4 || humanRects > 0, faces: faces, bodyPoints: bodyPoints, humanRects: humanRects))
        } catch {
            samples.append(Sample(time: time, person: false, faces: 0, bodyPoints: 0, humanRects: 0))
        }
        time += interval
    }
}

let personSamples = samples.filter(\.person).count
let ratio = samples.isEmpty ? 0.0 : Double(personSamples) / Double(samples.count)
let report = Report(
    sampleInterval: interval,
    samples: summaryOnly ? [] : samples,
    personSamples: personSamples,
    totalSamples: samples.count,
    personRatio: ratio
)
let encoder = JSONEncoder()
encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
FileHandle.standardOutput.write(try encoder.encode(report))
FileHandle.standardOutput.write("\n".data(using: .utf8)!)
