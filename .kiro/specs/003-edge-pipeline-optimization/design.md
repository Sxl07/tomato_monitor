# Design - Edge Pipeline Optimization

## Overview

This spec defines how to optimize the existing Tomato Monitor pipeline for Raspberry Pi 5. The optimization strategy must be incremental and benchmark-driven.

The first optimizations should focus on reducing unnecessary CPU, I/O, and thermal load without changing model behavior.

## Optimization Strategy

The optimization process follows this order:

1. Measure current behavior.
2. Identify bottlenecks.
3. Disable or reduce non-essential artifacts.
4. Reduce input size or frequency.
5. Separate heavy processing from UI if necessary.
6. Evaluate model replacement only if evidence requires it.

## Edge Profile

Introduce a configuration profile for Raspberry Pi execution.

Suggested profile name:

edge

Possible settings:

ENABLE_ANNOTATED_VIDEO=false
ENABLE_SNAPSHOTS=false
ENABLE_CROPS=false
PROCESSING_WIDTH=640
PROCESSING_HEIGHT=480
DETECTOR_EVERY_N_FRAMES=5
MAX_FRAMES_PER_RUN=100
LOG_RESOURCE_USAGE=true

These are initial candidates and must be adjusted based on benchmark results.

Full/Desktop Profile

The current behavior should be preserved under a profile such as:

full

Possible settings:

ENABLE_ANNOTATED_VIDEO=true
ENABLE_SNAPSHOTS=true
ENABLE_CROPS=true
PROCESSING_WIDTH=original
PROCESSING_HEIGHT=original
DETECTOR_EVERY_N_FRAMES=1
MAX_FRAMES_PER_RUN=none
Configuration Location

Review existing configuration before adding new settings.

Known candidate:

src/infrastructure/config/settings.py

Any new configuration should be centralized and documented.

Pipeline Areas to Review
Video inspection runner

Candidate file:

src/infrastructure/vision/video_inspection_runner.py

Review:

frame loop;
frame resizing;
output writing;
snapshot generation;
annotated video generation;
model reuse;
configuration handling.
Detector

Candidate file:

src/infrastructure/vision/detectron_detector.py

Review:

model loading time;
CPU execution;
inference time;
image size assumptions.
Health classifier

Candidate file:

src/infrastructure/vision/resnet_health_classifier.py

Review:

model loading;
crop preprocessing;
batch or single-crop inference possibilities.
Maturity estimator

Candidate file:

src/infrastructure/vision/maturity_estimator.py

Review:

colorimetry cost;
segmentation cost;
unnecessary repeated work.
Tracker

Candidate files:

src/infrastructure/vision/visual_tracker.py
src/infrastructure/vision/tracker_adapter.py

Review:

whether optical flow reduces total cost or adds overhead;
detection frequency trade-offs.
First Optimization Candidates
Disable annotated video by default in edge mode

Reason:

Annotated video generation increases CPU and I/O load.

Expected impact:

Lower CPU usage.
Lower disk writes.
Lower temperature.
Disable snapshots/crops by default in edge mode

Reason:

Frequent image writing increases I/O and processing cost.

Expected impact:

Lower storage usage.
Lower latency.
Fewer blocking operations.
Reduce resolution

Reason:

Detector and image operations scale with image size.

Expected impact:

Faster inference.
Lower CPU usage.
Possible accuracy trade-off.
Reduce detector frequency

Reason:

Detectron2 is likely the heaviest component.

Expected impact:

Lower average CPU usage.
Lower temperature.
Possible tracking/accuracy trade-off.
Benchmark Requirements

Every optimization must be compared against baseline:

Metric	Baseline	Optimized	Change
Total runtime			
Average FPS			
Detector inference time			
RAM max			
Temperature max			
Output quality observation			
Security and Availability Considerations
Avoid unbounded output generation.
Avoid filling disk with snapshots or videos.
Avoid long blocking API requests.
Avoid overheating during unattended tests.
Keep configuration explicit and reviewable.
Risks
Risk	Impact	Mitigation
Lower resolution reduces detection quality	High	Benchmark quality on real images
Disabling crops affects traceability	Medium	Make it configurable
Reduced detector frequency misses tomatoes	High	Compare accuracy and tracking behavior
Too many settings increase complexity	Medium	Use clear profiles
Optimizations hide bugs	Medium	Preserve full mode
Decision Points

The following decisions should be documented as ADRs if implemented:

Disabling annotated video by default on Raspberry.
Disabling snapshots/crops by default on Raspberry.
Reducing default resolution.
Reducing detector frequency.
Replacing Detectron2.
Moving detection to AI Camera or another model.