# M5 非自车车辆定位与跟踪初版

输入冻结M2的4976个YOLOP车辆框、M1 City点云、相机位姿与标定及原始地面高度栅格。不读取ArgoTweak标签、变化GT或nuScenes数据。仅覆盖本场景582帧及三路前视。M2漏检的车辆不会被M5补回。

## 检测与三维定位

复用YOLOP预训练检测，类别仅为vehicle，不伪造car/truck/bus细分类。每帧用相机时间位姿将同一LiDAR参考时刻的City点投影入框；只补偿自车运动，不补偿车辆在相机与LiDAR时间偏差中的运动。

点云筛选为距自车XY 2.5至60m、相对高度栅格高0.25至3.5m的点。框内水平内缩10%，顶部内缩20%、底部5%，减少背景和地面。0.3m体素化后按0.8m邻域连接聚类，至少8个原始点，检查点簇可见表面尺寸；从合理点簇中选最近前景，仍可能有背景或遮挡物污染。

position_city定义为关联可见LiDAR点的坐标中位数，不是完整车辆3D框中心，也不是车底中心。保存源点索引，队友可核查定位依据。它可能随视角/遮挡变化而移动，不能把所有差分都解释为真实车辆运动。缺少可用点簇的车辆框保留two_d_only，位置为null。

同一帧不同相机的定位，只有共享点比例≥0.25或中心距离≤0.75m，且与组内每个成员一致时才归并。合并后的坐标取所有不重复源点的中位数，不重复计算同一个点。不同相机去重不是完整的多视图身份识别，同相机重复检测仍可能产生重复轨迹。

## 跟踪

- 2D：逐相机IoU+Hungarian，IoU至少0.2、最多0.4秒缺测。track_id_2d仅在同一相机内连续，不跨clip；没有可靠3D时仍保留这个ID。
- 3D：City XY常速度Kalman，按真实时间间隔更新，Hungarian配合马氏距离门控及空间门控。Z沿用最近观测值，不估计垂直速度。与历史2D ID一致可辅助关联，但不强制固定身份。
- 3次测量后track_state=confirmed，仅表示轨迹建立条件通过，绝不代表身份准确、检测正确或变化已确认。
- 缺测最多0.5秒输出预测样本，observed=false、measurement_id和measurement_position_city为null；超过0.8秒终止。跨clip不延续ID。
- 速度可用标记仅在当前有测量、轨迹至少1秒且至少5次命中、位置协方差最大特征值≤4平方米、速度≤35m/s时为true。这是工程候选门槛，不是独立准确性证明。低速或速度尚不稳定时heading_rad=null。

## 固定接口

schema_version=1.0.0，run_id=m5_vehicle_v1。时间均为纳秒字符串，位置City米制、速度米/秒，图像框对应原图。ID是本次run内身份，换算法应新建run_id，不能当跨版本永久身份。

| 文件 | 内容 |
|---|---|
| detections.jsonl | 每个输入框一行，源detection_id和source_run_id、track_id_2d、track_id（3D或null）、localization_status及失败原因、位置、关联源点索引 |
| measurements.jsonl | 去重后的LiDAR测量位置、来源检测ID、相机、源点索引和3D track_id |
| track_samples.jsonl | 每轨迹每时间样本；滤波/预测位置、测量位置、速度、协方差、observed、measurement_id、轨迹状态和质量标记 |
| tracks.jsonl | 每条3D轨迹摘要，包括起止时间、命中数、是否建立、终止原因 |
| frame_reports.jsonl | 每参考帧的检测、定位、去重测量和样本计数 |
| rules.json/provenance.json | 阈值、来源摘要与未使用GT的说明 |
| validation.json/summary.json | 一致性核验与统计；不代表精度 |
| frames.jsonl/views.jsonl/calibration.json | 复制M2的时间和来源接口，原始RGB与点云为外部引用 |
| qa/ | 抽样检查图：绿框为可定位，橙框为仅2D，青点为关联LiDAR点 |

track_samples中的position_city是滤波状态（预测帧为纯预测），measurement_position_city才是当帧可见点簇测量。下游M6优先使用observed=true且velocity_usable_for_behavior_candidate=true的样本，并核查轨迹完整性，不能把预测补点当成新观测。measurement_id关联测量，再由detection_ids追到M2车辆框；source_city_points_ref相对于Argoverse工作区，source_point_indices指向对应M1 NPZ的xyz_city数组。

## 限制

有车辆误检、漏检、背景污染、ID切换和轨迹碎片的风险；尤其侧前视、远处小框和遮挡。当前没有独立3D/连续身份标注，不报告3D误差、IDF1或ID Switches，不把轨迹总数当作独立真实车辆数。使用可见表面中心会影响速度估计，行为结论必须谨慎。

## 本轮结果和抽查

4976个输入框中4005个有点簇定位、971个仅保留2D；去重后3407次测量。126条3D轨迹片段中96条满足建立条件，不等于126辆或96辆真实车辆。4469个状态样本含3407次测量更新和1062个纯预测；2707个样本满足当前速度使用门槛。

39条片段的首末测量间隔至少2秒，最长约46.40秒；轨迹片段时长中位数约1秒，碎片化明显。已查看首帧前中、试跑侧前和全量第387帧前中检查图：部分点簇与车辆表面吻合，但有标志牌/建筑区域误检仍被当作车辆，并可能被赋予3D位置。轨迹总览也存在可疑跳动。因此本版适合下游流程开发和候选行为分析，不能直接作为可靠身份或驾驶行为真值。

复现python Argoverse工作区/m5_vehicles.py，核验和打包python Argoverse工作区/check_m5.py。原M1至M4交付不改写。原始RGB、LiDAR与City点云不放入轻量ZIP，需要时另行取得来源文件。
