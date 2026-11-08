# M7 地图差异与车辆行为时空关联 v1

输入冻结M4变化记录、M6行为事件及人工风险说明，并追溯M3逐帧变化几何、M5实际测量。未读取GT，也不重新推断地图变化或车辆行为。

## 关键时间约定

change_observed_start_ns/end_ns只是传感器观测到候选差异的窗口，不是道路变化发生时间。没有同时观测，不代表道路变化不存在或已经消失。由于本场景M4状态uncertain，不能假定其几何代表整个58秒一直存在的真实变化，所以正式关联采用严格同时观测证据，另存跨窗口空间筛查供检查。

行为事件按track_id、scene_id、clip_id及其测量ID和源时间戳截取轨迹，绝不拿整条车辆轨迹替代事件。只用M5真实LiDAR测量，不使用预测点，不跨clip、不插值。M6拟合轨迹用于识别行为，本阶段距离基于其对应的实际点簇测量位置。

## 空间和关联规则

正式证据只比较相同LiDAR参考时间戳下的车辆点与M3变化几何，多个相机候选取最近距离并保留源候选ID。距离为City XY点到折线距离，不是三维距离，也不是车辆边界净空。缓冲区域半径3m，buffered_region_distance=max(0,line_distance-3)。

associated要求：M4 confirmed、M6 candidate且是行为事件、无人工疑似假运动风险、同窗至少3次实测且至少1秒，连续近距离≤3m的证据至少3次且持续至少1秒，相邻支持间隔≤0.35秒。

not_associated也需上游通过且同时观测至少3次/1秒，所有同时观测距离>5m；只表示该观测窗口内未接近，不推广到未观测时段。其他情况uncertain。M4可疑路缘风险单列，不能把尚未确认的差异当作真实变化。

stationary_presence和uncertain行为episode不作为可分类行为；166段均保留行为可用性审计。人工suspected_false_motion阻止可信关联，但该配对仍保留以便追溯。

## 两种距离不可混用

associations.distance_to_change_m是实际同时观测证据的最短距离，无同时证据则null。spatial_screening_min_distance_m是把M4代表几何固定后，计算整个行为事件内车辆到该几何的最短距离，仅用于空间筛查。它不假定变化真实、永久或因果，不得拿此值填充正式关联距离。

## 文件与固定接口

schema_version=1.0.0，run_id=m7_association_v1；关联键为change_id、event_id、track_id，分别保留source_m4_run_id与source_m6_run_id。纳秒时间为字符串，City位置为米。后续换模型或改规则应新建run_id，不覆盖原始版本。

- associations.jsonl/CSV：变化—行为配对，含时间关系、正式距离、空间筛查距离、关联状态和原因。
- association_frame_evidence.jsonl：仅实际同帧证据；本轮为空，空文件不代表程序漏跑。
- spatial_screening.jsonl：事件窗口中逐测量点到M4代表几何的诊断距离，diagnostic_only=true。
- change_coverage.jsonl：每个变化是否有可评估同时行为；no_simultaneously_evaluable_behavior不能当not_associated。
- vehicle_context.jsonl：变化观测时刻实际测得的车辆轨迹及距离，即使没有合格行为事件也保留，避免混淆“没有车辆”和“没有可评估行为”。
- behavior_eligibility.jsonl：166段episode的可用性、排除原因与人工检查说明。
- source_changes.jsonl/source_behavior_candidates.jsonl：复制本轮参与配对的变化与行为，便于独立读取几何与窗口；不包含GT。
- timeline.png：橙色为变化观测窗，蓝色为行为候选窗口，时间以场景首个M5时刻为零。
- rules/provenance/summary/validation/file_manifest：规则、来源摘要、统计、检查和文件哈希。

## 本轮结果

1个uncertain变化候选与7段候选行为形成7个配对；其中一段行为另有疑似假运动风险。7个配对均uncertain：没有实际同时观测的合格行为样本，变化本身也未确认。402行空间筛查只是诊断。

变化观测窗口中仍有17次实际车辆测量，说明不是“附近没有车辆”，而是没有满足M6规则的同时行为事件。不能把本轮输出解释成“道路变化与车辆行为无关”。

运行m7_associate.py，再运行check_m7.py。检查包括关联正例/远距离负例的合成规则、缺少同时观测不得输出不关联、上游不确定传播、人工疑似假运动阻止关联，以及实际测量来源和距离复算。没有人工关联GT，不报告Precision/Recall/F1；全部associated也只可能表示时空接近，不能声称因果关系。
