P01 Front-camera images are not readable yet

Metadata, calibration and poses for 41 CAM_FRONT frames are integrated; no matching photos were found in the readable folder. The uploaded enhanced.rrd exceeded the 32 MiB transfer limit. The readable overlay.rrd contains incremental layers only.

P02 Reliability is integrated; seven units still lack write evidence

M3 was reproduced and integrated. All seven review units remain Insufficient Evidence, with unknown valid coverage and physical alignment.

P03 Six marking candidates lack verified edit targets

None of the six LD candidates has a verified map-edit target. The nearest map line does not automatically represent the same physical object.

P04 One short scene cannot validate long-term change

scene-0980 contains one visit of about 19.85 seconds and 41 frames. The historical / current role of the reference map is unverified. Duplicate folders are not independent revisits.

P05 No validated recommended-route output

The current integration has no executable planner, planning start / goal, planned route or feasibility checks. Existing ego trajectories are recorded paths.

P06 217 real-evaluation label rows are empty

Templates contain 29 candidates, 13 behaviors and 175 associations: 217 rows without independent ground truth.

P07 High conditional F1 hides many abstentions

Quality gating decides on 36 of 126 variants: 28.6% decision coverage, 94.1% conditional F1 and 22.2% recall over all positives. Variants derive from three windows in the same scene.

P08 Current aggregate scores use later frames

Multi-frame scores and behavior associations use the full window; the reconstruction also displays the subsequently recorded ego path.

P09 Front projection uses offset timestamps and a ground-plane assumption

CAM_FRONT differs from the reference frame by up to 37.609 ms, corresponding to about 0.228 m of ego displacement. Camera-exposure poses are used; the 2D map uses z=0 and object boxes retain annotation-frame timing.

R01 Original scripts and media packaging hinder reproduction

Original entry points contain old local paths; Windows .cmd files do not run directly on Mac. The repository excludes JPG, MP4 and RRD files. An isolated runner currently supplies explicit paths.

R02 The earlier website did not show all promised map-update steps

Slides 2–3 promised before-and-after maps, decision reasons, version history and rollback checks; the earlier website primarily showed candidates and evaluation.