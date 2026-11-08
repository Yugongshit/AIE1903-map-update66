# M2下游交付格式 v1.0.0

本交付是全片段三路前视的M2观测初版，适合队友开发多帧融合、候选组织、可视化及车辆后处理。它不是已确认地图变化、车辆轨迹或最终效果评估。识别覆盖车道边界候选；横道、实虚线和可信可见性仍未完成。先验地图中存在横道，不表示已经识别当前图像中的横道。

## 文件布局

```text
scene_M2_v1/
  README.md
  schema.json
  run.json
  summary.json
  validation.json
  file_manifest.json
  calibration.json
  prior_map.json
  prior_provenance.json
  read_delivery.py
  tables/
    frames.jsonl
    views.jsonl
    observations.jsonl
    matches.jsonl
    detections.jsonl
  masks/          原图尺寸二值PNG
  overlays/       抽样曲线与车辆框检查图
```

工作目录中的cache仅供断点续跑，不加入交付ZIP。ZIP不包含原始RGB、原始LiDAR、全部City点云或GT；其路径保留为外部来源引用。队友不拿原始数据也能读几何、标定、预测掩码和匹配表；如果需要图像或点云，需另行取得对应原始场景及M1点云文件，并配置来源根目录。

## 共同约定

所有JSON/JSONL为UTF-8；一行JSONL就是一条记录，不依赖行顺序关联。schema_version为1.0.0，run_id标记算法版本。缺失数值用null，未知类别用unknown，空集合用[]；禁止NaN/Infinity。

时间戳为十进制纳秒字符串，避免电子表格或JavaScript丢失精度。frame_id就是LiDAR参考时间戳；只有(scene_id, frame_id)组合才在跨场景时唯一。图像使用image_timestamp_ns，不把最近帧的图像时间伪装成LiDAR时间。offset_ms=(图像时间-LiDAR时间)/1e6。

坐标：geometry_city是本场景City坐标系的[x,y,z]，单位米；不同场景的City坐标不保证同一参考原点。pixel_curve_xy及bbox_xyxy_px对应原始图像，原点左上、x右y下，单位像素。image_size_wh=[宽,高]，不是[高,宽]。位姿是4×4齐次矩阵，city_from_ego表示自车坐标到City坐标。calibration中的ego_from_camera为相机到自车。

view_id由scene_id、frame_id和camera组成。observation_id/detection_id仅在(scene_id, run_id)下使用，不是跨帧track_id；同一条真实标线可能在多帧、多个相机产生多条记录。更换算法后可以重新编号，因此下游关联键必须同时包含run_id；地图对象ID则保留输入先验的原ID。不同标线共享的地图边界可有多个近似匹配，不能按ID数量当作道路变化事件数。

## frames.jsonl 每参考帧一行

| 字段 | 含义 |
|---|---|
| scene_id, clip_id, frame_id, timestamp_ns | 场景、片段及参考时刻 |
| city_from_ego_at_lidar | LiDAR参考时刻位姿 |
| view_ids | 本帧已处理的三路相机ID |
| source_lidar_ref, source_city_points_ref | 相对于Argoverse工作区的外部来源路径 |

## views.jsonl 每帧每相机一行

包含view_id、frame_id、scene_id、camera、image_timestamp_ns、offset_ms、source_image_ref、image_size_wh、city_from_ego_at_image。masks含road、lane_raw、lane_vehicle_filtered三个相对于交付包根目录的PNG路径；0背景、255预测前景。road是模型可行驶区域提示；lane_raw是原始lane头预测；lane_vehicle_filtered仅屏蔽预测车辆框，不按road硬裁剪。

observation_count/detection_count用于核对下游完整性。status=processed表示推理完成，候选数为0并不等于明确没有道路要素。visibility=unknown；不能从空掩码、空曲线或车辆框推导“充分可见”。overlay为抽样图路径，无抽样则为null。

## observations.jsonl 每条图像曲线一行

| 字段 | 类型/约定 |
|---|---|
| observation_id, view_id, frame_id, scene_id, clip_id, camera | 关联键 |
| element_type | lane_boundary_candidate；不是已验证车道线 |
| pixel_curve_xy | 至少3个原图像素[x,y]点组成的折线 |
| geometry_city | 对应地面City三维折线；投影失败为null |
| geometry_pixel_indices | geometry_city各点在pixel_curve_xy中的索引；点数必须一致 |
| length_m | 保留地面曲线的XY弧长；失败为null |
| projection_status | projected或projection_failed |
| model_score_mean | YOLOP前景通道分数均值，不是校准概率或变化置信度 |
| color | white/yellow/unknown；局部颜色启发式候选 |
| marking_pattern | 当前均为unknown，没有实虚线分类 |
| visibility | 当前均为unknown |
| quality_flags | 模型候选、路缘混淆风险、未跨帧融合、地面投影不完整等 |

曲线来自掩码骨架的分支路径，交叉点会拆段。地面求交只保留最长连续有效部分，不能把它当作完整地图对象。投影失败记录仍保留像素曲线，避免把失败混同成没有检测。

本版projection_failed也包括“有交点但无法组成满足质量条件的地面折线”：至少3个连续点、相邻点XY间距不超过3m、XY总弧长0.75至60m。它表示没有可用地面曲线，不专指位姿或标定文件出错。像素骨架短于12个模型尺度像素的片段不输出曲线，但原始掩码仍完整保留。

## matches.jsonl 每条观测恰好一行

status为matched、ambiguous、unmatched、projection_failed。candidates保留至多3个地图边界匹配，包含map_element_id、boundary_side(left/right)、mean_distance_m、direction_difference_deg和sample_fraction_within_3m。

约束：平均距离≤3m、无向角差≤25度、至少一半采样点距地图边界≤3m。候选按距离排序；前两候选差距<0.25m则ambiguous。仅matched填写best_map_element_id，其余填null。阈值是开发预设，未独立校准。匹配只是几何接近，既不证明标线正确，也不证明没有变化。

change_type固定为null，交给后续M3处理。unmatched不直接表示新增；没有观测到某个地图对象不直接表示删除。

## detections.jsonl 每个车辆框一行

保存detection_id、view_id、frame_id、scene_id、camera、image_timestamp_ns、bbox_xyxy_px=[x1,y1,x2,y2]、score、class_name=vehicle。YOLOP此权重的类别较粗，不自行拆成car/truck/bus。track_id和geometry_city均为null：这不是三维轨迹，队友需额外完成跟踪/点云关联才能用于速度、行为和时空关联。

## 版本升级规则

更换模型/权重/阈值时更换run_id并写新目录，保留旧版。保持时间、坐标、字段语义；新增可选字段提升次版本。改变必需字段、单位、坐标或枚举含义时提升主版本，并提供迁移说明。下游使用字段名和关联键，不能依赖CSV行号、文件顺序或硬编码观测数量。

schema.json描述必需字段和关键类型；validation.json记录本次实测一致性，不能用来声称模型准确率。GT属于单独评估交付，本包不附入GT以避免误接入观测处理链。
