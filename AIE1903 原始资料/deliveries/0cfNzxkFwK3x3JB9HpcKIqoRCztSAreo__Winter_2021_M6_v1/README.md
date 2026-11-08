# M6 规则式车辆行为候选 v1

只读取冻结M5的track_samples和tracks，不读取GT、nuScenes数据或地图变化结果。M6与M3/M4并行：道路变化不作为识别车辆行为的条件，也不据此解释驾驶意图。

## 观测与特征

不使用1062个纯预测样本计算行为。M5轨迹建立早期、速度使用标记未通过的样本不得用于行为分类。对实际测量位置，以当前时刻前后0.5秒做离线局部线性回归，至少5个测量、覆盖至少0.4秒；剔除大残差后重新拟合。观测窗口内相邻测量间隔超过0.35秒、拟合RMS超过0.8m、速度超过35m/s或与Kalman速度差超过3m/s，均不作为有效行为特征。

这使用未来0.5秒的测量，不是实时算法。位置仍是车辆可见表面点簇中心，可能随视角变化产生假运动。局部拟合速度不等于独立验证的真实速度。无可靠车道/参考路线，reference_lane_id和lateral_offset_m为null。

## 行为规则

- stationary：速度<0.5m/s。持续至少2秒且在同一无长间断的可用轨迹中有至少1秒moving背景，输出stop候选；否则是stationary_presence，不当作驾驶行为。
- moving：速度≥1m/s。连续片段至少1秒，按至少0.75m的位移选方向点，展开航向角，避免±180度跳变。
- straight：净航向变化≤15度且累计变化≤35度。
- turn：净航向变化≥30度，累计变化≤180度，且不优先满足掉头形状；只描述轨迹形态，未独立核实道路方向。
- u_turn_candidate：净航向变化150至210度、首末位移/路径长度≤0.65、累计变化≤270度；还需片段前后各至少0.5秒连续可用背景。缺少完整进出背景则uncertain。
- transition：0.5至1m/s，不强行分为停车或移动。
- detour：没有可靠参考路线与完整绕行证据，not_evaluable，不输出绕行候选。

跨越0.35秒以上的缺测或无效样本缺口即切断。同状态连续样本形成一个episode；这不是精细的逐转弯分割算法。边界截断、初始化和轨迹碎片均保留标记。低速不计算差分航向。固定阈值为开发初值，未用本场景GT校准。

## 固定格式

schema_version=1.0.0，run_id=m6_behavior_v1，source_run_id=m5_vehicle_v1。时间为纳秒字符串，City位置为米，速度为米/秒，heading_rad为弧度。

- vehicle_features.jsonl：每个M5样本一行，保留track_id、measurement_id、observed、behavior_usable、motion_state及排除原因。position_city_fitted为离线拟合位置；不是原始测量点。预测样本保留记录但不可用于行为。
- behavior_events.jsonl：每段事件含event_id、track_id、behavior_type、status、start_ns/end_ns、source_measurement_ids、source_sample_timestamps_ns、event_geometry_city、指标和完整性状态。几何由有效实际测量时刻的局部拟合位置构成，不添加预测时刻。
- behavior_events.csv：便于查看的事件摘要，不含完整几何和来源数组。
- track_assessments.jsonl：126条轨迹都保留一条评估，列出有效样本量、episode ID和不可评估原因，避免短轨迹静默消失。
- rules.json、provenance.json、summary.json、validation.json：规则、来源摘要、统计和检查。
- qa/及qa_index.json：候选事件的开始/中间/结束原图及源检测框检查，非人工GT。

status=candidate的记录才是当前规则识别出的行为候选；stationary_presence使用observed_presence且is_behavior_event=false；uncertain也使用is_behavior_event=false。下游关联至少同时检查status与is_behavior_event，不能只看表名或轨迹ID。所有候选仍受上游身份和定位误差影响。

事件不合并为真实驾驶意图，也不保证一次驾驶动作只对应一条episode。M7截取轨迹时应按(track_id, start_ns, end_ns)和测量ID对齐，不使用整条轨迹代替事件窗口。没有行为GT，不报告Macro-F1或识别准确率。

## 本轮初版统计

从4469个M5样本中排除全部1062个预测样本，2508个样本可用于行为特征；83条轨迹没有可用行为样本。166段episode中有4段直行候选、3段停车候选、16段静止存在、143段不确定；没有合格转向、掉头或绕行输出，不代表场景中一定不存在这些行为。

3段停车候选都来自vehicle3d_00040，可能是同一次等待受噪声分成多个状态片段；不能当作3辆车或3次独立停车。数据主要可用于候选关联与流程开发，不能直接当作标注真值。

人工查看3段事件的首/中/尾源图后，vehicle3d_00005的直行候选疑似为路边停放白色SUV的点簇中心漂移；其余抽查只能提供可解释性，不能替代独立运动核验。review_notes.json单独记录人工观察，不修改可复现的规则输出、不充当行为GT。后续M7应保留这些风险，特别是suspected_false_motion不能作为可信行为依据。

运行m6_behaviors.py，再运行check_m6.py即可复现和打包。ZIP不含原始视频、图像或点云，来源文件通过M5/M2引用；无需原始图像也可读取事件City几何和速度特征。
