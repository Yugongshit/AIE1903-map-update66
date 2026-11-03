# 实际交付核对与整理结果

> 更新：后续 GitHub 提交 `06c2cff` 已补齐本页所列的原始地图、时间与逐帧几何。当前可运行真实地图回放，见 [原始数据对接结果](raw_scene_integration.md)。下文保留为仅拆分交付文件的核对记录；不再需要重复上传这些原始 CSV。

## 恢复的目录和文件

47 个可下载附件已原样复制到 `external/perception_delivery/`，每个文件有 SHA-256、原始文件名和上传 file_id 记录，见 `manifest.json`。47 项哈希全部验证通过。另一个增强版 `.rrd` 超过 32 MiB 传输上限，在 manifest 中登记为不可用；无需它也能查看已交付的结构化候选。

目录依据原 README、源码读写路径、JSON 的 stage 字段、记录数与轨迹 ID 恢复。原源码保留，未对其规则或输出做隐式修改。

| 类别 | 实际内容 | 整理位置 |
| --- | --- | --- |
| 源码 | stage3、stage5、stage6、stage7、run_all、rerun_env、inspect_recording、visualize_results | `pipeline/` |
| 编译缓存 | 4 个 Python 3.12 pyc | `bytecode_archive/`，仅留档 |
| 候选证据 | 29 候选、332 帧证据、12 nearby 链接 | `outputs/stage3/` |
| 场景行为 | 13 个车辆 token、221 采样点、13 行为片段 | `outputs/stage5/` |
| 时空关联 | 175 对、1368 距离记录 | `outputs/stage6/` |
| 评估入口 | 报告与人工轨迹规则检查 | `outputs/stage7/` |
| 真值模板 | 29 / 13 / 175 行，truth 均为空 | `evaluation_labels/` |
| 可视化 | overlay RRD、说明、日志、validation | `outputs/visualization/` |

相同名字并不意味着重复：13 行的 behavior_events 属于实际场景，6 行的版本属于合成规则检查；221 行的 vehicle_features 是场景轨迹，94 行的是合成数据。合成版本中的 `source=NUSCENES_GT_DERIVED` 是 stage5 代码硬编码，**不能据此将其当真实数据**；根据 stage7 生成逻辑和轨迹 ID 分组保留。

## 对你模块最关键的结论

1. **实际交付不是早期 proposal 描述的完整感知接口。** 源码使用已有 nuScenes 标注重建车辆轨迹，stage3 融合既有候选；不是重新运行 YOLO 检测，也未交付 TBV prior/current 地图。坐标在原始 CSV 中命名为 global XY，地图 token / 城市和绝对时间需要再核验。
2. **29 个候选 = 23 ROAD_OCCUPANCY + 6 LANE_MARKING_GEOMETRY。** 评分等级为 20 高、3 中、6 证据不足；这仅是片段内证据排名。OCC 永远先作为临时占用候选；LD 也尚未分解为确定的新增/删除/几何修改。
3. **几何有数据，但不可直接写基础地图。** 所有 29 项 geometry 均为 MultiPoint。占用项是 `occupancy_member_centers`，不是障碍轮廓；LD 是逐帧曲线点拼接的 `observed_curve_points`，失去帧/曲线分组，不能把全部点按顺序连成一条新车道边界。
4. **可见性分母与 M3 缺失。** valid_visibility_frames 全空。usable_source_frames 仅表示来源几何条件合格，detected_curve_support_ratio 仅针对检测到的曲线。candidate_coverage.csv 表示可评估车辆行为对数，与 M3 的有效覆盖率无关。不能从这些字段填造 N_valid、覆盖率或 alignment_valid。
5. **没有真实复访。** 这是 scene-0980 一个约 20 秒的处理片段；视频文件播放时长不是长期观测跨度。多个帧、多个候选、多个车辆都不构成不同日期的复访。输入的 first/last_time_s 是片段相对秒，不能当 Unix 时间戳。
6. **关联只作解释与展示。** 175 对中 associated=5、not_associated=81、uncertain=89。中心到几何的距离不是车辆边界净空，不能用 associated 作为长期性/因果证据。
7. **评估尚无独立真值。** 空标签不能作为负例；原报告所有准确率类结果为空。scene-0980 是开发场景，不能报告为独立测试成绩。

因此接入输出保留 `map=null`、`map_updates=[]`、`reliability=null`、`valid_visibility_frames=null`。29 个候选均为持续性“证据不足”；OCC 暂存临时候选层，不宣称其已经被证实为短期变化。

## 代码和验证

```bash
# 实际数据接入、交叉验证及离线候选浏览
python3 -m map_update inspect-delivery \
  --root external/perception_delivery --out outputs/scene0980-new

# 原 stage7 在隔离副本重跑，避免覆盖原交付报告/标注
python3 scripts/verify_upstream.py --out outputs/upstream-check-new

# 你的持续性/更新引擎 + 真实接入测试
python3 -m unittest discover -s tests -v
```

本次实际执行结果：新模块 35 项测试通过；原 stage7 的 30 项检查通过；接入模块 235 项哈希/字段/计数/关联一致性检查通过。两个可视化页面都完成 Chromium 真实浏览器测试：筛选或时间线、图层、详情、JSON 导出、移动宽度无溢出，无 JavaScript 错误。

这些验证说明程序与交付文件在所检查范围内一致，不代表分数准确、地图正确或长期性分类已获实证。

实际数据浏览页不伪造底图，绘制汇总候选点和标注轨迹；分数与行为使用整段统计，明确标为离线回顾，不给规划在线消费。地图更新闭环仍由 `demo` 使用显式合成的跨访问样例演示。

## 继续对接所需文件

以下文件在上游源码中被引用，但这批拆分附件没有提供。最先补地图与结构化小文件，不必先传模型权重或六路视频：

| 需要的输入 | 用途 |
| --- | --- |
| 原始地图 JSON/GeoJSON（要素 ID、几何、地图 token/版本、prior/current 语义） | 初始化基础地图；核实 global XY 与 City 标识的对应关系 |
| `scene0980_unified_candidates_v1.csv` | 恢复 map_token、source_event_id、track_id、first/last_frame 等连接字段 |
| `frames.csv`，及场景绝对时间/访问标识说明 | 对齐 frame、elapsed_s、原始采集时间；保留一次经过的边界 |
| `occupancy_regions.json`、`lane_impact_frames_v2.csv`、`objects.csv` | 恢复占用成员和逐帧定向矩形；不能从中心点和面积反推完整轮廓 |
| `road_perception/scene0980_discrepancy_evidence_v1.json` | LD 与逐帧曲线/可行驶区域上下文连接 |
| `road_perception/scene0980_sensor_hdmap_line_metrics.csv`、`lane_track_auto_observations_000_040.csv` | 对齐检测曲线与 track/frame，核验候选时间窗口 |
| `road_perception/frame*_multi_lane_*.csv`（先给少量相关帧） | 恢复有序逐帧折线，保留曲线边界，不能从汇总 MultiPoint 猜分组 |
| 成员 3 的 JSON/CSV | 真实的覆盖率、对齐有效性、四类 reliability 决策及连接键 |
| 同一路段真实独立访问，或明确标注的仿真复访数据 | 持续性验证与阈值校准 |

现有 `input_hashes.json` 可用于对收到的六个对应原始输入文件校验来源；它只记录哈希，不含数据。`source_schema.txt` 仅为 Python 对象 repr，不能恢复源地图 schema。完整 Rerun 增强重建还需要原 `scene-0980_discrepancy_full_v2.rrd` 和 `rerun-sdk==0.22.1`；这不阻塞当前结构化接入和二维证据浏览。
