# Perception 标注来源、类别与大地图图层（2026-11-08）

本说明把原始物体标签、曲线观测、变化候选和最终地图图层分开。已有类别不等于已经确认地图出错；状态事件已保存，也应能在同一张地图上看到。

## 1. nuScenes：物体类别沿用官方标注

这批 scene-0980 有 41 帧、2,278 条逐帧标注、154 个独立物体实例。物体类别和 instance_token 直接来自 nuScenes 官方 ground-truth annotations；不是本项目重新训练检测器或跟踪器识别出的类别。

每条物体记录包含类别、实例 ID、原始标注 ID、位置、长宽高、朝向、LiDAR/radar 点数和前后标注链接。我们把标注三维框投成实际地面轮廓，再检查与地图车道的空间重叠。

本场景实际出现下列 14 类；这是当前场景出现的类别，不是整个 nuScenes 数据集的类别全集。

| 原始类别代码 | 中文含义 | 独立实例 | 逐帧标注 |
| --- | --- | ---: | ---: |
| `movable_object.barrier` | 路障 | 61 | 890 |
| `human.pedestrian.adult` | 成人行人 | 21 | 235 |
| `movable_object.trafficcone` | 交通锥桶 | 42 | 561 |
| `vehicle.car` | 汽车 | 8 | 104 |
| `vehicle.truck` | 卡车 | 4 | 103 |
| `movable_object.debris` | 散落杂物 | 3 | 107 |
| `human.pedestrian.stroller` | 婴儿车 | 1 | 32 |
| `static_object.bicycle_rack` | 自行车架 | 1 | 27 |
| `human.pedestrian.construction_worker` | 施工人员 | 8 | 144 |
| `vehicle.construction` | 施工车辆 | 1 | 19 |
| `human.pedestrian.personal_mobility` | 个人代步工具使用者 | 1 | 18 |
| `vehicle.bus.rigid` | 非铰接公交车 | 1 | 14 |
| `vehicle.motorcycle` | 摩托车 | 1 | 16 |
| `movable_object.pushable_pullable` | 可推拉物体 | 1 | 8 |

## 2. 交给变化处理模块的只有两种候选

| 候选类别 | 数量 | 怎样生成 | 当前图层去向 |
| --- | ---: | --- | --- |
| `ROAD_OCCUPANCY` | 23 个事件 | 物体地面轮廓与已有车道/连接段重叠 | 地图状态事件层 |
| `LANE_MARKING_GEOMETRY` | 6 个候选 | 相机 YOLOP 车道线预测，经 BEV、ROI/RANSAC/曲线拟合与多帧地图对比，得到匹配不足的线差异 | 结构变化假设；当前未证实 |

23 个占用事件不是 23 个独立物体。把这些事件的实例 ID 去重，共涉及 74 个对象：45 个路障、26 个锥桶、3 个杂物。例如 O001 是 6 个路障与一处 lane_connector 关联形成的占用事件，有 15 帧记录。

六条标线候选为 S0980-LD-001 至 S0980-LD-006，对应轨迹 T002 至 T007，提供的支持帧数分别为 2、2、8、7、1、1。这里的支持指来源观测支持该候选，不是确认基础图一定错误。

所以，路障的 barrier 标签早已存在；“地图对应关系不足”或“片段末尾没再观测到”不能改写成“不知道它是不是路障”。占用重叠也不自动意味着整条道路封闭。

另有运动/行为派生标签：stationary、moving、insufficient_observations、uncertain；车辆行为还可细分 stop、stationary_presence、straight、turn、u_turn_candidate、uncertain。它们来自官方实例轨迹、位移/转角/时长阈值，是独立的规则派生步骤，不是本次结构/状态分类的时间依据。

## 3. Argoverse：YOLOP 有真实推理，但类别较粗

这批 M2 输入为 582 帧、3 个相机、1,746 个视图。交付脚本用 YOLOP ONNX 模型产生 road/lane 二值掩码和二维检测框；本次网站直接读取冻结交付，没有重新训练或再跑该网络。

| 输出 | 类别和数量 | 来源与限制 |
| --- | --- | --- |
| 车辆框 | 4,976 个逐视图框，全部 `vehicle` | 粗车辆类；不是 4,976 个独立实例，没有 car/truck/bus/barrier/worker 细分类 |
| 车道线曲线 | 10,278 条 `lane_boundary_candidate` 观测 | 从二值车道线掩码提取，6,770 条可投影到 City 坐标；不是 10,278 个已确认变化 |
| 曲线颜色 | unknown 7,918；white 2,073；yellow 287 | HSV 阈值规则，非网络直接输出的精细颜色分类 |
| 虚实线/可见性 | `marking_pattern`、`visibility` 均为 unknown | 当前输出没有识别虚实线和有效可见性 |

已有地图中的 NONE、SOLID_WHITE、DASHED_WHITE、DOUBLE_SOLID_YELLOW 等是地图原属性，不能当作 Perception 已识别的结果。地图里还有 219 条车道与 27 处人行横道；这些可以作为底图展示，但不等于本次网络重新检测了它们。

M2 车辆框的 track_id 和 geometry_city 为 null；后续 M5 才另做 LiDAR 关联与 Kalman/Hungarian 跟踪。当前结构/状态运行只接入 Argoverse 标线差异，状态事件层为空；这不表示场景没有车辆，也不是按数据集名称强制分类。

## 4. 大地图怎样显示已存储的结果

大地图采用同一坐标变换叠加三层：

- **灰色底图**：nuScenes 的 157 个原始地图要素；Argoverse 的 M2 历史图，包括 219 个车道填充、343 条物理边界、27 个人行横道。没有把评价用当前真值图混进底图。
- **红色状态事件**：读取已保存的 state_event_layer，不重新编造点位。显示实际最后观测轮廓和可点击标记，关联事件 ID、道路及最后观测时间。
- **黄色结构变化**：显示实际观测到的结构变化候选；候选使用虚线，并明确当前真实结构补丁仍为 0。黄色不等于已经改写道路。

nuScenes 当前可见 23 个状态记录及 6 个结构候选。12 个状态在记录截止时有存在观测，11 个的末帧状态未观测；后者保持历史位置和未确认有效性的标记，不伪装成已经清除。Argoverse 的唯一得到局部支持的结构候选仍会显示，并明确标注其几何真值误报。

地图支持图层开关、缩放、拖动、全图视野、定位当前事件，以及点击标记联动证据详情。颜色表示图层类型，证据状态与实际写图状态另行说明。

## 5. 可追溯文件

- [机器可读的双语类别与计数](perception_labels_20261108.json)
- [结构/状态算法与状态生命周期](map_structure_state.md)
- [中文大地图](https://yugongshit.github.io/AIE1903-map-update66/algorithms/zh/index.html)
- [英文大地图](https://yugongshit.github.io/AIE1903-map-update66/algorithms/en/index.html)

主要原始证据：

- [AIE1903 原始资料/processed/objects.csv](<../AIE1903 原始资料/processed/objects.csv>)
- [AIE1903 原始资料/processed/semantic_report.json](<../AIE1903 原始资料/processed/semantic_report.json>)
- [AIE1903 原始资料/processed/scene0980_unified_candidates_v1.csv](<../AIE1903 原始资料/processed/scene0980_unified_candidates_v1.csv>)
- [AIE1903 原始资料/processed/occupancy_regions.json](<../AIE1903 原始资料/processed/occupancy_regions.json>)
- [AIE1903 原始资料/processed/tracking_report.json](<../AIE1903 原始资料/processed/tracking_report.json>)
- [AIE1903 原始资料/processed/road_perception/frame026_lane_fit_report.json](<../AIE1903 原始资料/processed/road_perception/frame026_lane_fit_report.json>)
- [AIE1903 原始资料/processed/road_perception/frame033_auto_lane_report.json](<../AIE1903 原始资料/processed/road_perception/frame033_auto_lane_report.json>)
- [AIE1903 原始资料/perception/pipeline/stage5.py](<../AIE1903 原始资料/perception/pipeline/stage5.py>)
- [AIE1903 原始资料/Python代码/m2_deliver.py](<../AIE1903 原始资料/Python代码/m2_deliver.py>)
- [AIE1903 原始资料/deliveries/0cfNzxkFwK3x3JB9HpcKIqoRCztSAreo__Winter_2021_M2_v1/run.json](<../AIE1903 原始资料/deliveries/0cfNzxkFwK3x3JB9HpcKIqoRCztSAreo__Winter_2021_M2_v1/run.json>)
- [AIE1903 原始资料/deliveries/0cfNzxkFwK3x3JB9HpcKIqoRCztSAreo__Winter_2021_M2_v1/summary.json](<../AIE1903 原始资料/deliveries/0cfNzxkFwK3x3JB9HpcKIqoRCztSAreo__Winter_2021_M2_v1/summary.json>)
- [AIE1903 原始资料/deliveries/0cfNzxkFwK3x3JB9HpcKIqoRCztSAreo__Winter_2021_M2_v1/tables/observations.jsonl](<../AIE1903 原始资料/deliveries/0cfNzxkFwK3x3JB9HpcKIqoRCztSAreo__Winter_2021_M2_v1/tables/observations.jsonl>)
- [AIE1903 原始资料/deliveries/0cfNzxkFwK3x3JB9HpcKIqoRCztSAreo__Winter_2021_M2_v1/tables/detections.jsonl](<../AIE1903 原始资料/deliveries/0cfNzxkFwK3x3JB9HpcKIqoRCztSAreo__Winter_2021_M2_v1/tables/detections.jsonl>)
- [AIE1903 原始资料/deliveries/0cfNzxkFwK3x3JB9HpcKIqoRCztSAreo__Winter_2021_M2_v1/prior_map.json](<../AIE1903 原始资料/deliveries/0cfNzxkFwK3x3JB9HpcKIqoRCztSAreo__Winter_2021_M2_v1/prior_map.json>)

本轮验证：208 项核心测试、90 项浏览器检查通过；[完整验证记录](composite-map-validation-20261108.json)。
