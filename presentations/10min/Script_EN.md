# 10-Minute Speaker Script
Slides 1–30: 9 min 45 s of allocated speaking time, plus 15 s for handoffs. Slide 31 is a team-role appendix. Use either language as a complete presentation, not both consecutively. Assignments below are suggested from the proposal and can be adjusted by the team.

## 01 HD Map Change Detection, Maintenance and Planning
Cui Yingting · 20 s

Good morning. We are Group 5. Our project studies HD map change detection, maintenance and planning under uncertain observations. We connect road observations to two decisions: how the vehicle should respond now, and whether the map should change.

## 02 Three Modules, Four Questions
Cui Yingting · 20 s

We have three modules. Perception and Reliability asks what changed and how reliable the evidence is. Planning asks how the vehicle should respond. Map Maintenance asks whether to update the map. Huang Ziming also consolidates evaluation evidence from the whole team.

## 03 Problem, Data and Scope
Cui Yingting · 20 s

Road changes can be confused with occlusion or alignment errors. We use nuScenes for current occupancy and observation consistency. Structural-change evaluation requires verified map versions and labels. Our minimum viable workflow is offline, and controlled perturbations are reported separately.

## 04 Perception and Reliability
Cui Yingting · 5 s

First, Perception and Reliability.

## 05 Overall Architecture and Implementation Details
Cui Yingting · 5 s

Here is our overall architecture.

## 06 Minimum Viable Version: Detection and Reliability
Cui Yingting · 15 s

Perception generates change candidates. Reliability analysis evaluates their support, then supplies planning and maintenance. For the structural-change branch, we must verify map roles and labels before treating a sample as evidence of a permanent change.

## 07 Minimum Viable Version: Route and Behavior
Cui Yingting · 15 s

Planning uses route information and vehicle observations. Our initial target is basic route search with conservative behavior. Other vehicles provide context, but a vehicle moving near a candidate does not establish why it moved.

## 08 Module Specifications: Overall Workflow
Cui Yingting · 15 s

The modules share scene identifiers, time windows and map versions. These references connect observations, reliability decisions and downstream outputs, and prevent us from combining evidence from incompatible scenes or map versions.

## 09 Perception: Inputs and Alignment
Cui Yingting · 20 s

Inputs include camera images, LiDAR, ego poses, calibration and the prior map. Alignment comes first because coordinate errors can resemble road changes. Detection and tracking then support multi-frame verification, behavior analysis and evaluation.

## 10 Perception: Detection and Tracking
Cui Yingting · 20 s

Perception covers road elements, frame-level change candidates and vehicle tracks. The current pipeline reads processed candidates and annotation-supported vehicle records. Those records support downstream analysis, but do not establish the accuracy of a newly trained detector. Wang will explain verification.

## 11 Perception: Caution and Association
Wang Zimo · 20 s

We retain uncertainty from sensors, estimated changes and behavior labels. Our current association rules check spatial proximity across samples. An associated pair satisfies those rules, but does not prove that a map change caused the vehicle behavior. Uncertain cases remain available for review.

## 12 Reliability Assessment
Mei Ziheng · 20 s

Reliability combines verification with observation quality. Behavior association adds context, while geometry and visibility still need checking. We also distinguish reliability from persistence: an object can be clearly observed and still be temporary.

## 13 Extension
Mei Ziheng · 15 s

The extensions are closed-loop simulation, dynamic interactions and continuous map updates. They are future goals. Continuous updating needs independent observations from different visits or vehicles before we can justify long-term changes.

## 14 Fall Back
Mei Ziheng · 20 s

Our fallbacks include nuScenes annotations, controlled perturbations and rule-based reliability. Planning can retain basic route search and stop when no feasible route exists. Map demonstrations can use a temporary layer and offline replay. Evaluation will identify which fallback produced each result.

## 15 Local HD Map Reliability Assessment
Mei Ziheng · 25 s

My proposed reliability module first checks coverage, occlusion and alignment. It then compares geometric residuals against an unchanged baseline. The output distinguishes no significant mismatch, suspected change, further verification and insufficient evidence. Thresholds come from development data. This qualified decision supports downstream planning and maintenance.

## 16 Current Evidence in scene-0980
Wang Zimo · 25 s

Scene-0980 contains twenty-nine candidates, thirteen vehicle tracks and one hundred and seventy-five association pairs. Five pairs are associated, eighty-one are not, and eighty-nine are uncertain. These are output counts. All candidates remain unverified as map changes, and the twenty-second window cannot establish long-term persistence.

## 17 Planning
Pan Mingyang · 5 s

Next is Planning.

## 18 Reliability-Aware Local Planning
Pan Mingyang · 30 s

Our proposed planner uses A-star on a directed lane graph, with nonnegative penalties for uncertain sections. An edge is blocked only when independent evidence shows it is impassable. Given verified occupancy and the ego state, the minimum viable behavior is to continue on a feasible route, or stop and wait. Dynamic interactions are a later extension.

## 19 Planning Comparisons and Handoff
Pan Mingyang · 20 s

We compare prior-map and reliability-aware planning under the same scene, start and goal. We record route differences, feasibility and behavior, and pass them to Huang with scene and version references. Controlled perturbations remain a separate comparison.

## 20 Map Maintenance
Huang Ziming · 10 s

I am Huang Ziming. My part decides which map layer should receive the evidence, and keeps that decision traceable.

## 21 Map Updating and Evidence Aggregation
Huang Ziming · 25 s

I collect candidate geometry, coverage and reliability decisions, plus planning outputs and evaluation records from the other members. After matching the scene, time window and map version, I decide whether to record a temporary event, update the base map, or retain an unresolved case. I also produce visualization and the final evidence package.

## 22 Temporary, Structural and Unresolved Changes
Huang Ziming · 30 s

There are three outcomes. Short-term events enter the temporary event layer. Structural changes with sufficient long-term support can update the base map. Unresolved cases stay available for verification. Verified current occupancy can enter the event layer even while its long-term status remains uncertain. This represents the current situation without prematurely changing road geometry.

## 23 Persistence and Independent Evidence
Huang Ziming · 25 s

Persistence records include first and last observations, valid batches and independent visits. Geometry must agree across observations. Existing elements use IDs, while new elements need distance, direction and overlap checks. Fifteen adjacent frames from one drive are correlated observations, not fifteen independent visits. Conflicting evidence keeps the case unresolved.

## 24 Temporary Event Layer
Huang Ziming · 25 s

The temporary layer records location, affected elements, observation times, validity and supporting evidence. New observations can extend or close an event after verification. Expiry needs careful interpretation: an old record does not prove the obstacle has disappeared. We distinguish stale observations from verified clearance so planning can use the record correctly.

## 25 Versioned Base-Map Updates
Huang Ziming · 25 s

A base-map edit needs reliable structural geometry and independent persistence evidence. We may insert, modify or deactivate an element. Each edit retains the previous version, the exact change and its supporting evidence, allowing review and rollback. A high score helps prioritize inspection, but geometry and long-term support still determine whether an edit is justified.

## 26 A Candidate with Strong Short-Window Evidence
Huang Ziming · 30 s

This repository candidate has fifteen supporting frames over six point eight nine nine seconds, and a score of one. Its map-change status is still unverified. I would retain the evidence and verify occupancy before an event-layer entry. A base-map edit still needs independent long-term evidence and usable structural geometry. This shows why maintenance requires more than a score.

## 27 Map Visualization and Decision Review
Huang Ziming · 25 s

These are conceptual illustrations. The first is the prior map. The second overlays a temporary event while keeping road geometry. The third illustrates a supported structural update with old geometry for comparison. Actual views should share coordinates and link each change to evidence and a version. Integrated before-and-after map editing remains a next step.

## 28 Evaluation Protocol and Evidence Package
Huang Ziming · 30 s

I consolidate evaluation records from all modules. Perception and reliability need independent labels. Planning needs matched comparison conditions. Maintenance needs known changes or controlled cases to assess correct updates, false edits, timing and rollback. We also retain failures and decision history. Real observations and controlled perturbations remain separate, with traceable sources for every result.

## 29 Current Progress and Next Integration Steps
Huang Ziming · 20 s

The repository currently supports scoring, behavior analysis, association and evidence visualization. Thirty rule and consistency checks pass, while accuracy metrics still need independent labels. Next, we integrate reliability and planning, implement map-layer decisions, and add cross-visit evidence to evaluate long-term changes.

## 30 Thank you
Huang Ziming · 5 s

Thank you. We welcome your questions.

## 31 Team Roles
Cui Yingting / 全组 · 0 s

This appendix summarizes the responsibilities in our proposal. Cui Yingting handles perception and change candidates. Wang Zimo handles multi-frame verification and behavior analysis. Mei Ziheng handles local map reliability. Pan Mingyang handles route and behavior planning. Huang Ziming handles map updates, visualization and the final evaluation and evidence package.
