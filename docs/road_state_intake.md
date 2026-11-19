# 上游道路状态证据接入

当前实现为记录场景服务新增 POST /evidence 和 GET /road-states。它复用 state_events.py 的几何、证据冲突、解除和过期判定。道路附着状态独立发布在 road_state_version 中；基础地图版本仍由静态地图内容确定，动态参与者继续由物理世界提供。

## 输入准备

examples/road-state-intake/synthetic-event.json 是受控合成接线样例。字段中的来源与评分含义明确标注为测试。真实同学数据到来后，需要显式转换坐标系、时间基准和字段，然后提交；程序不会自动推测这些转换。

每次请求包含以下绑定字段。

| 字段 | 含义 |
|---|---|
| submission_id | 本会话内唯一提交ID，重试保持整个请求内容一致 |
| session_id、scene_id | 当前会话和场景 |
| map_version、state_version | 最新观测绑定的基础地图和状态版本 |
| observation_step | 当前仿真帧号 |
| time_basis | 固定为 simulation_time_ns |
| events | 本次提交的1至100个道路状态事件 |

每个事件提供稳定 event_id、source_object_id、change_type、coordinate_reference、score_semantics、unknown_fields 及 samples。map_layer.predicted_layer 必须为 map_state_event，reliability.status 必须为上游提供的 supported_conflict。此接口接收已支持的道路状态证据，其他变化应进入相应处理入口。

coordinate_reference 必须等于当前观测的 coordinate_frame，并标注 units 为 m。沿用现有状态模块的 geometry_city 字段名，但其实际坐标以 coordinate_reference 为准。支持原有二维 LineString、Polygon 和 GeometryCollection。

samples 包含按时间排列的观测。timestamp_ns 是相对本会话仿真起点的非负十进制纳秒字符串，必须小于或等于当前仿真时间。每条观测明确给出 source_ids、quality 和 observation。

- quality 使用现有模块的有限 [0,1] 质量分数，score_semantics 必须解释含义，输出明确标记 score_is_probability=false。
- supports_change 表示支持占道等道路状态，质量大于0时必须提供有效观测几何。
- supports_prior 表示恢复证据，仅在质量大于0且 fully_observed=true 时解除同一 event_id。
- unknown 表示未知。缺失信息与部分观测保持未知。
- ttl_s 或 expires_at_timestamp_ns 可以显式提供。服务没有默认过期时长。过期产生 stale，显式解除产生 cleared，两者结果分别保存。

map_aliases 可选。每项 map_element_id 必须指向当前基础地图中的真实要素。没有可靠关联时提交空列表。

unknown_fields 必须显式提供字符串列表，列出尚未确定的信息，例如真实持续时间。已发布的事件元数据与历史样本保持不可改写。更新同一事件时携带完整旧samples，并追加新观测。解除后有新支持观测时可再次激活。

## 原子提交与重试

服务在修改前验证整批事件。任何一条无效，整批拒绝。重复 event_id、未知字段、错误坐标、未来样本、历史改写、跨场景或旧版本提交均被拒绝。成功提交会更新 state_version，同帧已计算的旧动作随即失效，协调客户端应重新获取观测。

完全相同的 submission_id 与请求内容可重试，即使世界已经推进也返回最初收据。收据中的 observation 是当时接受后的快照，使用最新状态请重新GET。复用ID提交不同内容会失败。幂等收据仅在当前进程会话内有效，重启产生新会话。

证据意图和接受结果追加写入现有 events.jsonl。意图落盘失败时不发布状态；接受结果写入失败时停止该服务。恢复时可读取日志中的 evidence_accepted 及其前面的 evidence_intent 分析已接收内容；末尾仅有intent的操作属于结果未知，当前尚无自动跨进程恢复。

## 当前观测与历史

GET /observation 同时返回当前 road_states、road_state_version、基础地图版本、动态对象和帧绑定。GET /road-states 返回同一帧的道路状态层及绑定信息。GET /history 保存各帧当时已知的状态，迟到证据只影响接收后的视图。

状态层复用既有规则。观测当时为 observed_active，后续没有新观测时为 unknown；显式时效届满为 stale。上述状态均保留最后已知几何及其来源，供Planning按明确的有效性作决定。cleared事件从活动features移至cleared_events。相同道路上不同事件分别维护。

## 提交示例

以下代码复用 docs/recorded_service.md 中的 call 和 binding 函数。在刚创建的会话中使用合成样例，验证接口。

~~~python
from pathlib import Path
import json

event = json.loads(Path("examples/road-state-intake/synthetic-event.json").read_text())
current = call("/observation")
request = dict(
    binding(current),
    scene_id=current["scene_id"],
    submission_id="synthetic-intake-1",
    time_basis="simulation_time_ns",
    events=[event],
)
receipt = call("/evidence", request)
current = receipt["observation"]
layer = current["road_states"]
~~~

后续追加观测时，应使用实际证据对应的时间和几何。重复请求需保存原request，避免重试时重新绑定版本而改变内容。

## 验证

~~~bash
python -m unittest discover -s tests -p 'test_evidence_intake.py' -v
python -m unittest discover -s tests -p 'test_recorded_service.py' -v
~~~

测试覆盖整批失败、幂等重试、帧／坐标校验、证据来源、旧历史保护、未知与过期、显式解除、日志失败和HTTP入口。原生HTTP接线验证使用官方MetaDrive开发样例与合成道路状态证据，属于接口验证。

基础地图结构补丁在线发布及显式坐标／时间适配工具已实现，见 [开发准备与验收](huang_ziming_preparation.md)。真实上游字段和同学Planning算法仍需联调。
