# 输入接口 v1（原型内部规范，尚非真实交付格式）

核心程序读取两个 JSON：基础地图对象和观测记录数组。可复制 `examples/` 中的数据理解格式，但真实接入必须从实际输出映射，不能填造通过门槛的字段。

实际 stage3/5/6 文件已经接入 `inspect-delivery`。它的输出为 `perception-intake-1.0`，用于保留原始候选、缺失字段和展示；**不能直接交给 `run` 写回地图**。实际文件缺少可见性分母、地图对应关系和逐帧完整几何，故不自动生成虚假的规范记录。详见 [交付整理说明](delivery_findings.md)。

后续上传的原始地图和 CSV 已通过 `inspect-scene` 接入，输出 `perception-scene-1.0`。`reference_map` 保留 nuScenes_global 坐标、真实 token 和源文件版本；`replay` 提供精确纳秒时间与逐帧折线/矩形；OCC 已有车道区域连接，LD 仍无确认的修改对象。由于 M3、复访与 prior/current 语义未核验，这个浏览契约仍不自动转换为写回契约。详见 [最新接入结果](raw_scene_integration.md)。

## 地图

```json
{
  "schema_version": "1.0",
  "map_id": "road-scene-001",
  "version": "v0",
  "map_role": "verified_prior",
  "coordinate_reference": {"frame": "City", "city_id": "实际城市标识", "units": "m"},
  "provenance": "地图来源及 prior/current 语义核验说明",
  "elements": [{
    "element_id": "line-001",
    "element_type": "lane_marking",
    "geometry_city": {"type": "LineString", "coordinates": [[0, 0], [0, 20]]},
    "properties": {"marking_type": "solid_white"},
    "active": true
  }]
}
```

`map_role` 仅接受已核验的 `verified_prior` 或显式仿真 `controlled_synthetic_prior`。不能因文件名含 HD map 就把它当历史地图。地图可保留 Polygon 要素，但自动修改仅支持 `lane_marking` / LineString。所有几何当前只接收二维米制 [x,y]；有 [x,y,z] 的实际交付需要适配器明确投影取平面，不能猜测轴顺序。

## 每条变化观测

| 字段 | 约定 |
| --- | --- |
| `record_id` | 全局唯一的输入记录 ID；相同内容重复可去重，ID 相同内容不同报错 |
| `change_id` | 上游候选 ID；不要求跨 visit 恒定，但同 ID 不能换类型或目标 |
| `map_id`, `map_version`, `coordinate_reference` | 必须与输入 baseline 完全相符 |
| `visit_id` | 一次实际经过/独立采集访问，不是 frame ID；同一次 clip 切片不增加 visit |
| `timestamp_ns` | 实际该次记录的时间，十进制字符串优先；不接受浮点数 |
| `map_element_id` | 修改/属性修改/删除必须有目标 ID；新增必须为 null |
| `change_type` | `geometry_change`, `marking_change`, `add`, `delete`, `occupancy`, `line_discrepancy` |
| `geometry_city` | GeoJSON 风格 LineString / Polygon 或 null；只是结构相似，坐标不是经纬度 |
| `observation` | `supports_change`, `supports_prior`, `unknown`；恢复需要明确 supports_prior |
| `valid_frame_count`, `support_frame_count` | 同次观测的有效/支持帧数；整数；不能从加权 score 推导 |
| `evidence_score` | 可选 [0,1] 的原始分数，仅显示；建议另保留方法/版本字段 |
| `fully_observed` | 是否充分可见；删除的每次支持访问和恢复都必须为 true |
| `marking_type` | 属性变化时必需，跨支持访问一致，非 unknown |
| `semantic_hint` | 可选 `temporary`，阻止基础地图自动写回；其他值不构成永久性证明 |
| `source` | 来源文件/日志标识，保留可追溯信息，勿写入凭据 |
| `reliability` | M3 结果对象或 null，null 表示尚无法通过质量门控 |

`reliability` 字段示例：

```json
{
  "decision": "Suspected Map Change",
  "effective_coverage": 0.95,
  "alignment_valid": true,
  "reason": "M3 原始判定原因"
}
```

四个允许结论与成员 3 proposal 一致：`No Significant Mismatch`、`Suspected Map Change`、`Needs Further Verification`、`Insufficient Evidence`。正向证据需要 Suspected Map Change；明确恢复需要 No Significant Mismatch。零覆盖、无效对齐、缺失 M3 均不会被高感知分数覆盖。

## 接入包装器

`adapt-proposal` 接收下面三部分，不读取或猜测 TBV/ArgoTweak 数字编码：

```json
{
  "context": {
    "map_id": "road-scene-001",
    "map_version": "v0",
    "visit_id": "drive-001",
    "coordinate_reference": {"frame": "City", "city_id": "实际城市标识", "units": "m"},
    "source": "上游输出文件标识"
  },
  "records": [],
  "reliability_records": []
}
```

`records` 使用上表的记录字段，可以省略 context 已提供的五个字段；若两处都有但不一致则拒绝。`reliability_records` 每条包含：map_id、map_version、visit_id、change_id、map_element_id、coordinate_reference、window_start_ns、window_end_ns、decision、effective_coverage、alignment_valid、reason。

可靠性结果必须六个关联字段完全匹配，并且 `window_start_ns <= timestamp_ns <= window_end_ns`。恰好匹配一条才采用；无匹配时 reliability=null；多个窗口重叠匹配时拒绝。若 M3 只有 element 级结果、没有 change_id，下一步应增加经确认的 element/window 转换逻辑，不能随便广播给所有候选。

```bash
python3 -m map_update adapt-proposal \
  --input examples/proposal_payload.json --output outputs/normalized-new.json
python3 -m map_update run \
  --map examples/base_map.json --observations outputs/normalized-new.json \
  --out outputs/normalized-run-new
```

`examples/proposal_payload.json` 是可运行的合成连接示例，只含一个 visit，所以不会更新地图。包装器只负责连接/转交，`run` 再执行完整输入与 baseline 校验。stage3 结果自身不足以填齐此接口，剩余必要字段需要从原始地图、逐帧几何和 M3 输出映射。

## 对接清单

感知同学提供：原地图及其版本/语义，逐次 ChangeRecord、完整 City 几何、真实时间、有效与支持帧计数、同次访问标识、代码入口。LD/OCC 的原始数值标签和几何对象需要保留；associated 及车辆行为仅作显示，当前不进入永久性证据。

成员 3 提供：同一地图/要素/时间窗口的覆盖率、对齐有效性、四类结论和原因。统计检验 p 值及异常程度可以附带保留，但不当永久性概率。

规划同学接收：`map.json` 中 active 要素及 version，与 `planning.json` 中同 version 的事件。`stale=true` 不表示道路已清空；`long_term_candidate` 也没有修改基础地图。对规划时刻之后的数据一律不可用；调用 `--as-of-ns` 做时间截断。
