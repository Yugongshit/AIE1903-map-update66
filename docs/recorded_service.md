# 记录场景同步服务

2026-11-19 开发入口。当前接通可信 ScenarioNet 数据库、原记录回放、外部动作、当前状态及有限历史。Planning 同学计划输出未来轨迹，尚未开始实现。未来轨迹需由同学的跟踪控制器转成现有转向／油门动作，接口测试使用人工指定动作。

一个进程拥有一个 MetaDrive 世界及一个场景。HTTP 请求和物理步进串行执行。当前采用一个可信协调客户端收齐所有受控车动作的模式，观测权限为理想全局当前状态。未来为每辆车提供独立权限和观测裁剪时需扩展协调层。

## 启动

在已有仿真环境中运行以下命令。RECORDED_SERVICE_KEY 由部署者通过环境注入，并通过受信任方式提供给协调客户端。服务对所有接口验证此密钥。

~~~bash
cd /workspace/AIE1903-map-update66
source .venv/bin/activate
export MPLCONFIGDIR="$PWD/.runtime/matplotlib"
export XDG_CACHE_HOME="$PWD/.runtime/cache"
run_dir=$(mktemp -d "$PWD/outputs/recorded-api.XXXXXX")
python scripts/run_recorded_service.py \
  --database 'AIE1903 原始资料/simulation/metadrive/metadrive/assets/waymo' \
  --out "$run_dir/run" --port 8094 --action-timeout 30 --history-frames 30
~~~

上例使用 MetaDrive 官方开发样例。宽恒云可指定已有的 /ai/map_update/waymo/converted/v1.3.1-dev-first3，并使用其仿真 Python 和源码路径。当前 Codex 验证范围仅覆盖官方样例。

默认监听 loopback。远程访问应由 HTTPS 反向代理或 SSH 隧道承载，Bearer 密钥与车辆令牌均具有控制权限。数据库仅由启动命令选择，服务读取可信 pickle。输出目录必须尚不存在。进程重启会创建新会话并从所选记录起点初始化。

## 当前 HTTP 契约

所有请求携带 Authorization 头，值为 Bearer 加空格及 RECORDED_SERVICE_KEY。JSON 响应禁用缓存。权限失败返回403，旧状态和无效输入返回409，未知端点返回404，内部错误返回500。

| 请求 | 行为 |
|---|---|
| GET /observation | 当前场景、会话、帧、记录时间、仿真时间、地图版本、状态版本、当前对象与信号 |
| GET /road-states | 当前道路附着状态、来源、有效性及独立版本 |
| POST /evidence | 原子接收道路状态证据并支持完全相同请求的幂等重试 |
| GET /map | 首次获取静态地图，后续按 map_version 复用，坐标系同当前观测 |
| GET /history | 最近已发生的有限状态窗口，最大帧为 through_step |
| POST /takeover | 为 actor_ids 中的源车辆取得控制权，返回逐车 control_tokens 和新 observation |
| POST /advance | 校验所有受控车的动作后统一推进一个0.1秒物理步 |

接管与动作 POST 均要求 session_id、map_version、state_version、observation_step，从最新 observation 原样复制。接管改变控制模式，即使帧号相同也生成新 state_version。旧快照不能继续提交。GET 不延长动作期限，也不推进物理。

接管额外字段为 actor_ids 字符串列表。动作额外字段为 actions 和 control_tokens，两者的键必须恰好覆盖全部已接管车辆。actions 中每项沿用 [steering, throttle]，均为 [-1, 1] 内有限数值，负 throttle 表示制动。原记录阶段使用两个空字典推进。

~~~python
import json
import os
import urllib.request

base = "http://127.0.0.1:8094"

def call(path, body=None):
    request = urllib.request.Request(
        base + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + os.environ["RECORDED_SERVICE_KEY"],
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)

def binding(observation):
    return dict(
        session_id=observation["session_id"],
        map_version=observation["map_version"],
        state_version=observation["state_version"],
        observation_step=observation["step"],
    )

observation = call("/observation")
# 从当前有效车辆中明确选择源 ID，此处仅展示官方样例中的车辆332。
claim = call("/takeover", dict(binding(observation), actor_ids=["332"]))
observation = claim["observation"]
observation = call("/advance", dict(
    binding(observation),
    actions={"332": [0.0, -1.0]},
    control_tokens=claim["control_tokens"],
))
~~~

当前对象附带源ID、原生实体ID、位置、速度、航向、有效性、当前物理尺寸与控制模式。尺寸未知时为 null。历史只保存已观测帧。未来记录、其他车辆未提交动作、接触评价和原记录误差均留在回放／评价端。

## 超时、失败与实验记录

首次接管后开始计时，成功推进后为下一帧重新计时。超过 action_timeout 后冻结整个会话，保留 external 控制模式与最后实际状态。当前版本恢复方式是新建独立会话。有效性缺口、记录终点、越过现有地图XY包围范围也停止推进。

重复提交已完成的动作会因帧／版本不符被拒绝。网络丢失响应时先GET当前观测确认结果。接管响应丢失会丢失该次令牌，当前须新建会话，后续需加入可恢复的幂等请求凭据。

events.jsonl 持久写入启动快照、接管意图和结果、动作意图与实际结果，逐条 flush/fsync。令牌不写入日志。写入失败或无法确认的原生异常会冻结服务。崩溃后只有intent的最后一次操作应视为结果未知。日志用于分析已执行事实，尚不支持恢复Bullet检查点。

## 三类数据与上游接入约定

道路附着状态与上游证据接收已接入当前HTTP服务，详见 [道路状态接入文档](road_state_intake.md)。以下结构补丁和规划轨迹字段保留为后续约定。

- 道路结构／规则补丁绑定 patch_id、base_map_version、对象ID、修改操作及前后几何，成功应用后发布新 map_version。
- 道路附着状态绑定 event_id、道路或区域几何、valid_from、expires_at、解除依据及 evidence_ids，更新后发布独立 road_state_version。
- 上游证据绑定 evidence_id、source_object_id、scene_id、观测时间范围、坐标系、几何、变化类别、来源、目标地图版本、score 与 score_semantics，以及显式 unknown_fields。未校准分数应说明量纲和含义。
- 动态参与者随已执行物理帧更新，历史窗口终点绑定当前帧。参与者形成持续占道时，需要独立证据生成道路状态事件。

规划轨迹的拟定接入字段为 session_id、actor_id、observation_step、map_version、state_version、plan_id、坐标系、采样时间和未来轨迹点。轨迹属于规划意图，跟踪控制器产生动作后，由仿真执行结果更新实际状态。轨迹格式、跟踪控制器责任和目标接口待与同学落实，当前未提供轨迹自动跟踪。

## 验证与后续

~~~bash
python -m unittest discover -s tests -p 'test_recorded*.py' -v
~~~

覆盖旧版本、旧帧、令牌冲突、完整动作批次、超时、有限历史、日志写入失败、部分物理失败和真实HTTP入口。原生检查还使用官方样例回放至第10帧，接管332与333，核验原实体和位姿连续性，推进至15帧并检查历史与持久日志。

后续接入真实上游数据、同学的轨迹与跟踪控制器、实时场景工作台、结构补丁在线发布和地图输入配对实验。当前网页仍为此前离线审阅与旧平台，新服务提供API开发入口。
