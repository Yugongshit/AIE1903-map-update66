# 原始资料已同步并接入

## 数据来源和目录选择

GitHub `main` 提交 `06c2cff`（添加项目原始资料）包含 498 个资料文件。云端 `work` 分支已快进到该提交，之前的代码保留；原始资料没有修改。同步前代码另备份至 `/workspace/scratch/map-update-before-sync-3sbi6wra/source-backup.tar.gz`。

主数据目录为 `AIE1903 原始资料/processed/`；地图为 `maps/expansion/singapore-onenorth.json`；场景、log 和时间元数据来自 `scene_info.json`、`v1.0-trainval/` 及 `processed/frames.csv`。

`delivery/scene0980_step1_delivery_v1/` 与带 ` 2` 后缀的文件夹各 83 个文件，路径和内容哈希完全相同。两份均原样保留，属于同一交付副本，不能作为第二次访问计数。

## 这次解决的对接问题

| 原先缺少 | 现在读取的来源 | 接入结果 |
| --- | --- | --- |
| 原始道路几何 | nuScenes expansion map 1.3 | 157 个局部地图对象，保留 token、原始几何和属性 |
| 城市与场景关系 | scene → log → location | `scene-0980` 对应 `singapore-onenorth` |
| 绝对时间 | frames.csv 的 timestamp_us | 整数乘 1000 转为纳秒字符串，避免浮点精度丢失 |
| 占用目标 | unified_candidates + occupancy_regions | 23 个 OCC 的 lane / lane_connector token 均在地图中找到 |
| 占用轮廓 | objects.csv 尺寸、航向和位置 + region member/frame | 使用同帧定向矩形；保留 311 条占用帧记录 |
| 有序观测标线 | frameNNN_multi_lane_NN.csv | 57 条折线保留其帧、曲线、track 和来源文件，不拼接跨帧点集 |
| 原始输入来源一致性 | 原交付 input_hashes.json | 六项对应原始输入 SHA-256 全部一致 |

局部地图包括 34 个 road_segment、43 个 lane、49 个 lane_connector、10 个人行横道、1 条 road_divider、20 条 lane_divider。按场景证据范围加 15 m 的包围盒筛选，保留原对象几何而不裁短要素。原交付提取的 8 条 divider 与原始地图对应对象的有序顶点完全一致。

地图版本标识由 SDK 地图 version `1.3` 与源文件内容哈希组成；它是内容身份，不是经核验的历史时间版本。保持 `map_role=reference_unverified_temporal_role`，不会通过 canonical `run` 的历史地图写回门控。

## 复现证据

已在隔离目录中用原始数据运行未修改的 stage3、stage5、stage6、stage7。仅通过函数参数/运行时变量指定输入输出路径，不改交付源码。

- stage3：29 候选、332 帧证据、12 nearby 链接；评分等级 20 高、3 中、6 不足。
- stage5：13 个车辆 token、221 个采样点、13 个行为片段。
- stage6：175 对关联，其中 associated=5、not_associated=81、uncertain=89。
- 17 个 stage3/5/6 输出文件的结构化内容全部与原交付一致。8 个字节相同；9 个 JSON 字节不同但解析值完全相同，属于序列化差异。
- stage7：30 项检查通过；真值标注仍为空，未产生准确率结果。
- 自有测试：44 项通过；新地图浏览页通过真实 Chromium 的逐帧切换、候选缺测、图层切换、详情、导出及移动宽度检查。

命令：

```bash
python3 -m map_update inspect-scene --raw-root 'AIE1903 原始资料' --out outputs/scene-map-new
python3 scripts/verify_upstream.py --processed 'AIE1903 原始资料/processed' --out outputs/reproduction-new
python3 -m unittest discover -s tests -v
python3 scripts/browser_smoke.py outputs/scene-map-new/index.html --mode scene
```

`inspect-scene` 输出 `reference_map.json`、`replay.json`、`candidates.json`、`scene.json`、`summary.json`、`validation.json` 和独立 HTML。当前实例实际成果位于 `outputs/scene0980-map-replay/`；复现报告位于 `outputs/full-reproduction-verified/`。

## 当前仍需区分的语义

1. **只有一次访问。** 41 帧、19.85016 秒，多个车辆或数据副本都不能充当复访。
2. **alignment_check 不是 M3。** 其中的坐标往返误差是代数自洽检查，传感器时间差是同步诊断；它们不证明物理投影精度、有效可见覆盖或真实地图变化。不能自动把 M3 alignment_valid 设为 true。
3. **OCC 的地图 ID 是被占用的车道区域。** 它不是待修改的 lane-divider 要素。阻挡和地图几何错误仍要分开处理。
4. **LD 仍无确定修改目标。** 原 unified_candidates 对 LD 的 map_layer/map_token 为空。metric 中最近的 divider 只能作为空间参考，不能自动当作同一物理标线。
5. **全窗口分数不能回灌在线规划。** 可视化的动态几何严格按帧；候选分组、分数和行为标签来自全片段离线分析，页面明确标注离线使用。

因此长期性状态仍为“证据不足”，本次真实地图更新为 0。新的数据接入解决了几何和来源问题，没有替代可靠性评估及持续性证据。

## 接下来可直接对接的工作

无需再上传地图或上述 CSV。M3 可利用当前 `reference_map`、每帧有序折线、时间与候选连接字段，补充 effective_coverage、alignment_valid、decision、reason；需注明实际检验针对的候选/要素与时间窗口。不要把现有 usable_source_frames 当可见性分母。

有真实复访后，以日志/采集访问身份建立 visit_id，保持同一地图坐标和版本关联。无真实复访时，继续用明确标记的受控重复访问样例检验状态机，真实与仿真结果分开。确认目标要素、地图时间语义、M3 和复访门槛后，才能将记录转换为 `run` 的 canonical 写回输入。
