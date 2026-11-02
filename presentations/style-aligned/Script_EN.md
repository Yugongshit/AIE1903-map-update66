# Group 5 · 10-Minute Speaker Script

For the revised 36-slide deck. Slides 1–35 have 9 min 45 s of speaking time, plus 15 s for handoffs; slide 36 is an optional appendix. Existing script wording is retained; Part 1 explanations and updated page numbers are added. Use either language as one complete presentation.

Teammates’ original English body slides are retained on pages 3–6 and 11–19 in both decks. Only their chapter covers (pages 2 and 10) are redesigned. Part 1 is assigned to its original presenter; other speaker assignments follow the previous script and can be coordinated within the team.

## 01  HD Map Change Detection, Maintenance and Planning
Cui Yingting  |  16 s  |  00:00–00:16

Good morning. We are Group 5. Our project studies HD map change detection, maintenance and planning under uncertain observations. We connect road observations to two decisions: how the vehicle should respond now, and whether the map should change.

## 02  01 Introduction and Related Work
Part 1 presenter / Part 1 主讲成员  |  4 s  |  00:16–00:20

We begin with the motivation and related work.

## 03  1.1 Motivation
Part 1 presenter / Part 1 主讲成员  |  16 s  |  00:20–00:36

HD maps describe lanes and their connections, but observations may disagree with them. The disagreement can come from a real road change or an observation error. Our central question is how to judge map reliability under this uncertainty.

## 04  1.2 Project Overview
Part 1 presenter / Part 1 主讲成员  |  17 s  |  00:36–00:53

Our workflow connects data annotation, reliability assessment, map updating and behavior planning. Their outputs move from road features and tracks to decisions, updated maps and driving actions. The current scope is local lane markings and an offline action demonstration.

## 05  1.3 Related Work
Part 1 presenter / Part 1 主讲成员  |  17 s  |  00:53–01:10

The related work covers four needs: map-change datasets, observation uncertainty, structured map edits, and planning. Together, these studies motivate a reproducible workflow linking detection evidence to map decisions and vehicle behavior. We focus on integrating these steps in one project.

## 06  References
Part 1 presenter / Part 1 主讲成员  |  4 s  |  01:10–01:14

These are the four references supporting this background.

## 07  Three Modules, Four Questions
Cui Yingting  |  18 s  |  01:14–01:32

We have three modules. Perception and Reliability asks what changed and how reliable the evidence is. Planning asks how the vehicle should respond. Map Maintenance asks whether to update the map. Huang Ziming also consolidates evaluation evidence from the whole team.

## 08  Problem, Data and Scope
Cui Yingting  |  17 s  |  01:32–01:49

Road changes can be confused with occlusion or alignment errors. We use nuScenes for current occupancy and observation consistency. Structural-change evaluation requires verified map versions and labels. Our minimum viable workflow is offline, and controlled perturbations are reported separately.

## 09  Perception and Reliability
Cui Yingting  |  3 s  |  01:49–01:52

First, Perception and Reliability.

## 10  Overall Architecture and Implementation Details
Cui Yingting  |  3 s  |  01:52–01:55

Here is our overall architecture.

## 11  Minimum Viable Version: Detection and Reliability
Cui Yingting  |  15 s  |  01:55–02:10

Perception generates change candidates. Reliability analysis evaluates their support, then supplies planning and maintenance. For the structural-change branch, we must verify map roles and labels before treating a sample as evidence of a permanent change.

## 12  Minimum Viable Version: Route and Behavior
Cui Yingting  |  15 s  |  02:10–02:25

Planning uses route information and vehicle observations. Our initial target is basic route search with conservative behavior. Other vehicles provide context, but a vehicle moving near a candidate does not establish why it moved.

## 13  Module Specifications: Overall Workflow
Cui Yingting  |  13 s  |  02:25–02:38

The modules share scene identifiers, time windows and map versions. These references connect observations, reliability decisions and downstream outputs, and prevent us from combining evidence from incompatible scenes or map versions.

## 14  Perception: Inputs and Alignment
Cui Yingting  |  14 s  |  02:38–02:52

Inputs include camera images, LiDAR, ego poses, calibration and the prior map. Alignment comes first because coordinate errors can resemble road changes. Detection and tracking then support multi-frame verification, behavior analysis and evaluation.

## 15  Perception: Detection and Tracking
Cui Yingting  |  17 s  |  02:52–03:09

Perception covers road elements, frame-level change candidates and vehicle tracks. The current pipeline reads processed candidates and annotation-supported vehicle records. Those records support downstream analysis, but do not establish the accuracy of a newly trained detector. Wang will explain verification.

## 16  Perception: Caution and Association
Wang Zimo  |  19 s  |  03:09–03:28

We retain uncertainty from sensors, estimated changes and behavior labels. Our current association rules check spatial proximity across samples. An associated pair satisfies those rules, but does not prove that a map change caused the vehicle behavior. Uncertain cases remain available for review.

## 17  Reliability Assessment
Mei Ziheng  |  14 s  |  03:28–03:42

Reliability combines verification with observation quality. Behavior association adds context, while geometry and visibility still need checking. We also distinguish reliability from persistence: an object can be clearly observed and still be temporary.

## 18  Extension
Mei Ziheng  |  13 s  |  03:42–03:55

The extensions are closed-loop simulation, dynamic interactions and continuous map updates. They are future goals. Continuous updating needs independent observations from different visits or vehicles before we can justify long-term changes.

## 19  Fall Back
Mei Ziheng  |  18 s  |  03:55–04:13

Our fallbacks include nuScenes annotations, controlled perturbations and rule-based reliability. Planning can retain basic route search and stop when no feasible route exists. Map demonstrations can use a temporary layer and offline replay. Evaluation will identify which fallback produced each result.

## 20  Local HD Map Reliability Assessment
Mei Ziheng  |  20 s  |  04:13–04:33

My proposed reliability module first checks coverage, occlusion and alignment. It then compares geometric residuals against an unchanged baseline. The output distinguishes no significant mismatch, suspected change, further verification and insufficient evidence. Thresholds come from development data. This qualified decision supports downstream planning and maintenance.

## 21  Current Evidence in scene-0980
Wang Zimo  |  19 s  |  04:33–04:52

Scene-0980 contains twenty-nine candidates, thirteen vehicle tracks and one hundred and seventy-five association pairs. Five pairs are associated, eighty-one are not, and eighty-nine are uncertain. These are output counts. All candidates remain unverified as map changes, and the twenty-second window cannot establish long-term persistence.

## 22  Planning
Pan Mingyang  |  3 s  |  04:52–04:55

Next is Planning.

## 23  Reliability-Aware Local Planning
Pan Mingyang  |  24 s  |  04:55–05:19

Our proposed planner uses A-star on a directed lane graph, with nonnegative penalties for uncertain sections. An edge is blocked only when independent evidence shows it is impassable. Given verified occupancy and the ego state, the minimum viable behavior is to continue on a feasible route, or stop and wait. Dynamic interactions are a later extension.

## 24  Planning Comparisons and Handoff
Pan Mingyang  |  16 s  |  05:19–05:35

We compare prior-map and reliability-aware planning under the same scene, start and goal. We record route differences, feasibility and behavior, and pass them to Huang with scene and version references. Controlled perturbations remain a separate comparison.

## 25  Map Maintenance
Huang Ziming  |  10 s  |  05:35–05:45

I am Huang Ziming. My part decides which map layer should receive the evidence, and keeps that decision traceable.

## 26  Map Updating and Evidence Aggregation
Huang Ziming  |  25 s  |  05:45–06:10

I collect candidate geometry, coverage and reliability decisions, plus planning outputs and evaluation records from the other members. After matching the scene, time window and map version, I decide whether to record a temporary event, update the base map, or retain an unresolved case. I also produce visualization and the final evidence package.

## 27  Temporary, Structural and Unresolved Changes
Huang Ziming  |  30 s  |  06:10–06:40

There are three outcomes. Short-term events enter the temporary event layer. Structural changes with sufficient long-term support can update the base map. Unresolved cases stay available for verification. Verified current occupancy can enter the event layer even while its long-term status remains uncertain. This represents the current situation without prematurely changing road geometry.

## 28  Persistence and Independent Evidence
Huang Ziming  |  25 s  |  06:40–07:05

Persistence records include first and last observations, valid batches and independent visits. Geometry must agree across observations. Existing elements use IDs, while new elements need distance, direction and overlap checks. Fifteen adjacent frames from one drive are correlated observations, not fifteen independent visits. Conflicting evidence keeps the case unresolved.

## 29  Temporary Event Layer
Huang Ziming  |  25 s  |  07:05–07:30

The temporary layer records location, affected elements, observation times, validity and supporting evidence. New observations can extend or close an event after verification. Expiry needs careful interpretation: an old record does not prove the obstacle has disappeared. We distinguish stale observations from verified clearance so planning can use the record correctly.

## 30  Versioned Base-Map Updates
Huang Ziming  |  25 s  |  07:30–07:55

A base-map edit needs reliable structural geometry and independent persistence evidence. We may insert, modify or deactivate an element. Each edit retains the previous version, the exact change and its supporting evidence, allowing review and rollback. A high score helps prioritize inspection, but geometry and long-term support still determine whether an edit is justified.

## 31  A Candidate with Strong Short-Window Evidence
Huang Ziming  |  30 s  |  07:55–08:25

This repository candidate has fifteen supporting frames over six point eight nine nine seconds, and a score of one. Its map-change status is still unverified. I would retain the evidence and verify occupancy before an event-layer entry. A base-map edit still needs independent long-term evidence and usable structural geometry. This shows why maintenance requires more than a score.

## 32  Map Visualization and Decision Review
Huang Ziming  |  25 s  |  08:25–08:50

These are conceptual illustrations. The first is the prior map. The second overlays a temporary event while keeping road geometry. The third illustrates a supported structural update with old geometry for comparison. Actual views should share coordinates and link each change to evidence and a version. Integrated before-and-after map editing remains a next step.

## 33  Evaluation Protocol and Evidence Package
Huang Ziming  |  30 s  |  08:50–09:20

I consolidate evaluation records from all modules. Perception and reliability need independent labels. Planning needs matched comparison conditions. Maintenance needs known changes or controlled cases to assess correct updates, false edits, timing and rollback. We also retain failures and decision history. Real observations and controlled perturbations remain separate, with traceable sources for every result.

## 34  Current Progress and Next Integration Steps
Huang Ziming  |  20 s  |  09:20–09:40

The repository currently supports scoring, behavior analysis, association and evidence visualization. Thirty rule and consistency checks pass, while accuracy metrics still need independent labels. Next, we integrate reliability and planning, implement map-layer decisions, and add cross-visit evidence to evaluate long-term changes.

## 35  Thank you
Huang Ziming  |  5 s  |  09:40–09:45

Thank you. We welcome your questions.

## 36  Team Roles
Optional appendix

This appendix summarizes the responsibilities in our proposal. Cui Yingting handles perception and change candidates. Wang Zimo handles multi-frame verification and behavior analysis. Mei Ziheng handles local map reliability. Pan Mingyang handles route and behavior planning. Huang Ziming handles map updates, visualization and the final evaluation and evidence package.

## Sources and evidence
New Part 1: AIE1903_Part01_Intro_Related_Work.pptx. Part 2: Part02-Our-Work.pptx. Existing content and scripts retain the previous proposal and repository basis (commit 6c5d134380725999425e0d6626773fef2aef3e56). Evidence scores are rule-based, not calibrated probabilities; 30 checks are rule/consistency checks, not measured accuracy. Map illustrations are conceptual.