# Group 5 · 10-Minute Presentation Script

The deck has three parts. Part 2, Our Work, contains all three technical modules. Within Module 1, present perception, multi-frame verification and behavior analysis, then reliability assessment. Slides 1–29 follow the supplied 10-minute schedule. Slides 30–39 retain supplementary detail for discussion. Presenter names are omitted.

## 01  HD Map Change Detection, Maintenance and Planning
00:00–00:08  /  8 s

Good morning. We are Group 5. Our project studies HD map change detection, maintenance and planning under uncertain observations.

## 02  Presentation Outline
00:08–00:16  /  8 s

We have three parts: introduction and related work, our work, and the closing summary. The three technical modules all belong to Part 2.

## 03  01 Introduction and Related Work
00:16–00:20  /  4 s

First, the motivation and related work.

## 04  1.1 Motivation
00:20–00:55  /  35 s

HD maps describe lanes and road connections. However, an observation may disagree with the stored map for different reasons. A lane layout or marking may really have changed, but an existing marking may also be hidden by a vehicle. These cases can look similar in one image. Our question is therefore how reliable the map is when observations disagree, and what evidence is needed before taking action.

## 05  1.2 Project Overview
00:55–01:30  /  35 s

The project connects data annotation, reliability assessment, map updating and behavior planning. Annotation provides road features and vehicle tracks. Reliability assessment adds a decision and supporting evidence. Map updating produces an updated map and a visual comparison, while planning provides a route and the next driving action. These outputs give the team a shared workflow. Our current scope is local lane markings and an offline action demonstration.

## 06  1.3 Related Work
01:30–01:57  /  27 s

The related work supports four parts of this workflow. Trust, but Verify provides a task and data for comparing maps with observations. The uncertainty study informs reliability assessment. ArgoTweak provides structured priors for individual map edits. The planning survey separates route, behavior and motion planning. Our focus is to connect these ideas in one integrated, reproducible project.

## 07  References
01:57–02:00  /  3 s

These are the references for this background.

## 08  Our Work
02:00–02:05  /  5 s

Part 2 now explains our work.

## 09  Problem, Data and Scope
02:05–02:30  /  25 s

Our task is to distinguish meaningful road changes from apparent differences caused by occlusion or alignment errors. nuScenes supports current occupancy and observation consistency. Structural-change datasets require us to verify map roles and labels. We aim for an offline workflow with traceable evidence, conservative planning and versioned map decisions. Controlled perturbations are reported separately.

## 10  Our Work: Three Modules, Four Questions
02:30–03:00  /  30 s

Within Our Work, we have three modules and four questions. Perception and Reliability asks what changed and how reliable the evidence is. Inside this module, we discuss perception first, then multi-frame verification and behavior analysis, and finally reliability assessment. Planning asks how the vehicle should respond. Map Maintenance asks whether the map should be updated. The shared evidence links these decisions.

## 11  Minimum Viable Version: Detection and Reliability
03:00–03:20  /  20 s

For the minimum viable version, perception identifies possible road changes. Reliability assessment evaluates whether the evidence supports those candidates. Detection therefore supplies something to inspect, while the reliability stage determines how much confidence the next stage should place in it. A detected difference alone does not establish a permanent map change.

## 12  Minimum Viable Version: Route and Behavior
03:20–03:40  /  20 s

The second part of the minimum version concerns route and behavior. Planning uses route information together with vehicle observations and reliability decisions. The initial goal is a feasible local route and conservative behavior. More complex interactions and closed-loop execution remain extensions beyond this offline demonstration.

## 13  Module Specifications: Overall Workflow
03:40–04:00  /  20 s

This workflow shows how the modules exchange their outputs. Scene identifiers, time windows and map versions keep the evidence aligned. The downstream modules need both a result and its supporting records. These common references let us inspect a planning or map decision and trace it back to the observations.

## 14  Perception and Reliability
04:00–04:04  /  4 s

We begin Module 1 with perception.

## 15  Perception: Inputs and Alignment
04:04–04:28  /  24 s

The perception inputs include camera images, LiDAR, ego poses, calibration and the prior HD map. Alignment places these observations in a common coordinate frame. This matters because a coordinate error can appear to be a road change. We retain scene and map references so later verification can compare the right observations with the right map.

## 16  Perception: Detection and Tracking
04:28–05:00  /  32 s

Perception covers road elements, frame-level change candidates, and vehicle detection and tracking. Road elements provide the geometry to compare with the map. Change candidates identify possible mismatches. Vehicle tracks provide context about movement and occupancy. We keep candidate geometry, element identifiers, observation times and coverage information. These are inputs to verification and reliability assessment, rather than a final instruction to edit the map.

## 17  Perception: Caution and Association
05:00–05:27  /  27 s

The next step is multi-frame verification and behavior analysis. Sensor errors and estimated changes can propagate into later decisions, so uncertainty must remain visible. Behavior near a candidate can provide context, but a spatial association does not establish causation. We therefore retain supporting observations and uncertain cases instead of turning every nearby vehicle action into evidence of a map change.

## 18  Current Evidence in scene-0980
05:27–06:00  /  33 s

The current repository evidence comes from scene-0980. It contains twenty-nine change candidates and thirteen vehicle tracks. Across one hundred and seventy-five candidate–behavior pairs, five are associated under the implemented rules, eighty-one are not, and eighty-nine remain uncertain. All twenty-nine candidates are still unverified as map changes. The short observation window supports consistency and association analysis, while accuracy still requires independent labels.

## 19  Reliability Assessment
06:00–06:25  /  25 s

Reliability assessment uses the verified observations together with observation quality. We need to distinguish a plausible road mismatch from a case that is simply poorly observed. Vehicle behavior can support review, but it is not decisive on its own. The module should provide a decision, the supporting evidence and any remaining uncertainty for planning and map maintenance.

## 20  Local HD Map Reliability Assessment
06:25–07:00  /  35 s

Our proposed reliability module has three steps. First, the quality gate checks visibility, occlusion, coverage and coordinate alignment. Second, the mismatch test compares geometric residuals with an unchanged baseline, using thresholds selected on development data. Third, it produces an explicit decision: no significant mismatch, suspected map change, further verification needed, or insufficient evidence. An evidence score helps prioritize review, but it does not establish how long a change will last.

## 21  Planning
07:00–07:04  /  4 s

Module 2 is local planning.

## 22  Reliability-Aware Local Planning
07:04–07:35  /  31 s

The proposed planner uses a directed lane graph, local reliability decisions, verified occupancy and the ego state. A-star searches for a route with nonnegative penalties for uncertainty. We block an edge only when independent evidence shows it is impassable. The planner returns a feasible route and a conservative keep or stop decision. If no feasible route remains, the fallback is to stop or wait.

## 23  Planning Comparisons and Handoff
07:35–08:00  /  25 s

We compare prior-map planning and reliability-aware planning under the same scene, start and goal. We record route differences, feasibility and behavior, and pass these records forward with scene, time and map-version references. Known controlled perturbations form a separate comparison. This makes the effect of the reliability information easier to evaluate and trace.

## 24  Map Maintenance
08:00–08:04  /  4 s

Module 3 decides how to maintain the map.

## 25  Temporary, Structural and Unresolved Changes
08:04–08:33  /  29 s

There are three outcomes. Short-term events enter a temporary event layer. Structural changes with sufficient independent, long-term support can update the versioned base map. Unresolved cases stay available for verification. Verified current occupancy can enter the temporary layer even when its long-term status is uncertain. This represents the current situation without prematurely changing road geometry.

## 26  Map Visualization and Decision Review
08:33–09:00  /  27 s

These conceptual views show the prior base map, a temporary event overlay and a supported structural update. We use the same scene, extent and coordinate frame, linking each change to its evidence and map version. The repository already supports evidence visualization, while integrated before-and-after map editing remains planned. Detailed persistence rules, event records, rollback and evaluation are retained in the appendix.

## 27  Summary and Next Steps
09:00–09:04  /  4 s

Finally, our progress and next steps.

## 28  Current Progress and Next Integration Steps
09:04–09:55  /  51 s

The repository currently supports candidate scoring, vehicle behavior rules, association outputs and evidence visualization. Thirty rule and consistency checks pass, but these are not accuracy measurements. The next integration steps are to connect reliability decisions with planning, implement temporary events and persistence states, and support versioned map edits. Evaluation still needs independent labels, more scenes, cross-visit evidence and review of failure cases. Across the three modules, the common principle is to preserve uncertainty and keep every decision traceable. We use verified temporary information for the current situation, and require stronger evidence before changing the persistent map.

## 29  Thank you
09:55–10:00  /  5 s

Thank you. We welcome your questions.

## 30  Technical Details
Optional; outside the 10-minute run-down

The remaining pages preserve technical details for questions and discussion.

## 31  Extension
Optional; outside the 10-minute run-down

The extensions are closed-loop simulation, dynamic interactions and continuous map updates. They are future goals. Continuous updating needs independent observations from different visits or vehicles before we can justify long-term changes.

## 32  Fall Back
Optional; outside the 10-minute run-down

Our fallbacks include nuScenes annotations, controlled perturbations and rule-based reliability. Planning can retain basic route search and stop when no feasible route exists. Map demonstrations can use a temporary layer and offline replay. Evaluation will identify which fallback produced each result.

## 33  Map Updating and Evidence Aggregation
Optional; outside the 10-minute run-down

I collect candidate geometry, coverage and reliability decisions, plus planning outputs and evaluation records from the other members. After matching the scene, time window and map version, I decide whether to record a temporary event, update the base map, or retain an unresolved case. I also produce visualization and the final evidence package.

## 34  Persistence and Independent Evidence
Optional; outside the 10-minute run-down

Persistence records include first and last observations, valid batches and independent visits. Geometry must agree across observations. Existing elements use IDs, while new elements need distance, direction and overlap checks. Fifteen adjacent frames from one drive are correlated observations, not fifteen independent visits. Conflicting evidence keeps the case unresolved.

## 35  Temporary Event Layer
Optional; outside the 10-minute run-down

The temporary layer records location, affected elements, observation times, validity and supporting evidence. New observations can extend or close an event after verification. Expiry needs careful interpretation: an old record does not prove the obstacle has disappeared. We distinguish stale observations from verified clearance so planning can use the record correctly.

## 36  Versioned Base-Map Updates
Optional; outside the 10-minute run-down

A base-map edit needs reliable structural geometry and independent persistence evidence. We may insert, modify or deactivate an element. Each edit retains the previous version, the exact change and its supporting evidence, allowing review and rollback. A high score helps prioritize inspection, but geometry and long-term support still determine whether an edit is justified.

## 37  A Candidate with Strong Short-Window Evidence
Optional; outside the 10-minute run-down

This repository candidate has fifteen supporting frames over six point eight nine nine seconds, and a score of one. Its map-change status is still unverified. I would retain the evidence and verify occupancy before an event-layer entry. A base-map edit still needs independent long-term evidence and usable structural geometry. This shows why maintenance requires more than a score.

## 38  Evaluation Protocol and Evidence Package
Optional; outside the 10-minute run-down

I consolidate evaluation records from all modules. Perception and reliability need independent labels. Planning needs matched comparison conditions. Maintenance needs known changes or controlled cases to assess correct updates, false edits, timing and rollback. We also retain failures and decision history. Real observations and controlled perturbations remain separate, with traceable sources for every result.

## 39  Workflow Responsibilities
Optional; outside the 10-minute run-down

The workflow responsibilities cover sensor perception and map-change candidates, multi-frame verification and behavior analysis, local map reliability, route and behavior planning, and map updating with visualization and evaluation evidence.

## Source basis
Project proposal; AIE1903_Part01_Intro_Related_Work.pptx; Part02-Our-Work.pptx; and the previously reviewed repository snapshot 6c5d134380725999425e0d6626773fef2aef3e56. The new run-down determines the order and timing. Evidence scores remain rule-based, and the map illustrations remain conceptual.