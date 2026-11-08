# Perception 标注来源与地图前后对照（2026-11-08）

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

## 4. 用可拖动分割线比较原图与更新后视图

主画面使用同一坐标和缩放级别显示原图与更新后视图：**左侧是原图，右侧是更新后视图**。白色分割线可以拖动：

- **拉到最右边：整幅显示原图。** 只显示未修改的参考底图，不显示事件或候选。
- **拉到最左边：整幅显示更新后视图。** 显示基础图及当前状态、结构候选或已确认变更。
- 放在中间：同一位置逐步揭示前后差异。缩放和平移使用共同视角，不会因两幅图错位制造假变化。

颜色按用户修正后的定义：

| 颜色 | 含义 | 当前真实数据 |
| --- | --- | --- |
| 黄色 | 地图状态事件，例如路障、锥桶、杂物占用 | nuScenes 23 个保存的事件 |
| 红色 | 已确认的结构变更 | 当前 0 个，不为了演示制造红色变更 |
| 绿色 | 尚未确认的结构候选 | nuScenes 6 个；Argoverse 71 个假设，其中 1 个有局部几何支持 |

灰色底图来自相同的原始参考地图：nuScenes 157 个地图要素；Argoverse M2 历史图 219 个车道填充、343 条物理边界、27 个人行横道。没有使用 M8 当前真值图伪装成更新结果。

“已确认”需要已执行的结构补丁，或明确通过完整结构修改条件；仅 `supported_conflict` 或 `classification_applied` 不够。Argoverse 的局部几何候选经评价是误报，仍属于绿色未确认候选，不会因为分类器采用了“结构”类别就变成红色。

地图状态直接读取保存的事件图层。12 个状态在记录截止时有存在观测，11 个只保留更早的最后观测轮廓；后者用空心/虚线等样式说明，仍属于黄色状态事件，不被误称为“无法判断类别”或“已经解除”。

为便于直接理解，地图使用路障、锥桶、杂物、标线差异等文字名称，详细 ID 和评分放在后续详情。颜色说明、数量和拖动方向与地图放在一起；详细管线比较、原始证据和评估表可以展开查看。点击只在更新后区域可见的标记时，联动到对应事件；原图一侧不会选中隐藏的覆盖物。

更新后视图包含状态更新和未确认候选，并不宣称基础道路几何已经全部改写。源预测、事件记录、参考地图及评价结果保持原样；此轮改动属于可视化。

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

滑动对照版通过 3 项构建身份测试与 123 项浏览器检查（49 项管线回归、74 项地图交互与来源检查），结果见 [最新验证记录](swipe-map-validation-20261108.json)。本轮只调整展示和交互，未重跑推理；之前大地图版的 208 项核心测试与 90 项浏览器检查记录保留在 [历史验证记录](composite-map-validation-20261108.json)。
