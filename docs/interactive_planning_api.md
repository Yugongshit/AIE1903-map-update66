# 交互房间：接入外部 Planning

交互房间提供一个由服务端持续推进的 MetaDrive 场景。`ego` 是自车，画面标签为 P0；另外五辆车使用 `P1` 至 `P5`。玩家或外部程序可以认领车辆，同一辆车同一时刻只有一个控制租约。浏览器围观、刷新画面和读取观测都不会推进仿真时钟。

外部 Planning 在同学自己的电脑或进程中运行：读取当前物理状态与地图，计算动作，再通过 HTTP 发送 JSON。服务端不接收 Python 源码、模型下载地址或远程回调。这里提供的是接入协议和示例，示例中的 `plan()` 只刹车，不代替你们的规划算法。

## 运行示例客户端

先由房主在平台中启动交互房间，等待场景和车辆加载完成。取得平台地址、当前 `session_id` 和加入票据 `join_token`，然后在仓库根目录运行：

```bash
python scripts/interactive_planner_client.py \
  --url http://127.0.0.1:8091 \
  --session-id YOUR_SESSION_ID \
  --vehicles ego
```

客户端会隐藏输入加入票据，也可从环境变量 `INTERACTIVE_JOIN_TOKEN` 读取。不要把票据放进命令行参数或 URL 查询字符串。公网地址使用已配置的 HTTPS 平台入口；`127.0.0.1` 只指向运行客户端的这台电脑。

一次认领五辆车时使用 `--vehicles P1 P2 P3 P4 P5`；只要其中一辆已被认领，整批认领失败，不会留下部分车辆已被控制的情况。`--seconds 10` 可在十秒后释放；不指定时按 `Ctrl+C` 结束。

修改 [interactive_planner_client.py](../scripts/interactive_planner_client.py) 中的函数：

```python
def plan(observation, vehicle_id, map_context):
    own = observation["vehicles"][vehicle_id]
    others = observation["current_objects"]
    # 在本地调用你们的规划/控制模块，使用 own、others 和 map_context。
    # 返回归一化的 [steering, throttle_or_brake]。
    return [0.0, -1.0]  # 接线示例默认刹车。
```

`current_objects` 按观测请求中的主车辆排除自身。批量控制时应按 `vehicle_id` 再排除正在计算动作的车辆；六辆受控车的同一步状态也完整列在 `vehicles` 中。示例每次只读取一份观测，并用同一个 `observation_step` 原子提交全部受控车辆的动作，避免将不同时间的五份观测混用。

客户端每次重新读取当前观测后发送一个新序号的动作，默认每 0.05 秒一轮，同时发送独立心跳。房间暂停时，重新读取的观测可以具有相同 `step`；持续提交新动作可保持控制租约，心跳本身不会刷新旧动作。这与重试失败请求不同：请求失败、房间重建或时钟倒退时客户端停止，不会盲目重发一个可能已经被执行的动作。计算和网络耗时超出服务端动作有效期时，服务端会执行失联保护；提高客户端轮询频率不会提高仿真的实际运行速度。

## 地址与权限

所有接口位于 `/api/interactive` 下。读取接口无控制权限要求；控制接口使用 JSON 请求体，票据不能放在 URL 中。

| 接口 | 用途与主要字段 |
| --- | --- |
| `GET /state` | 返回 `{room, engine}`，查看当前 `session_id`、`epoch`、车辆模式和引擎状态；不含任何票据。默认不请求图像渲染。 |
| `GET /observation?session_id=...&vehicle_id=ego` | 读取该房间当前物理步的车辆与物体状态。 |
| `GET /map?session_id=...` | 一次获取已验证的源道路几何、地图版本及明确标记的静态场景先验。 |
| `POST /claim` | 使用 `session_id`、`epoch`、`join_token`、`kind`、`client_label` 及 `vehicle_id` 或 `vehicle_ids` 认领车辆；返回 `lease_token`。外部程序使用 `kind: "external"`。 |
| `POST /input` | 使用 `session_id`、`epoch`、`lease_token`、递增的 `seq`、`observation_step` 和 `actions` 提交动作。 |
| `POST /heartbeat` | 使用 `session_id`、`epoch`、`lease_token` 续租；不会使旧动作重新变新。 |
| `POST /release` | 使用同样的租约身份释放控制。 |

房主还持有独立的 `host_token`，用于管理房间；房主票据也可认领车辆。普通玩家和外部算法使用 `join_token` 即可，不需要管理员部署凭证。`join_token` 只允许尝试认领空闲车辆，不允许抢占他人的有效租约。认领后的动作使用更窄的 `lease_token`。

外部 Planning 读取物理状态即可运行，无需获取 PNG。服务端默认只推进物理状态；网页需要引擎画面时才通过 `GET /state?render=1` 表示接下来两秒内需要渲染。示例客户端不会发送这个渲染请求，避免图像生成拖慢规划接入。

创建房间的 `/open` 接口属于部署管理操作，需要平台配置的管理员凭证；外部 Planning 客户端不创建或替换房间。房间重建会改变会话或代次，旧的加入上下文、车辆租约和动作都不能继续控制新场景。

## 当前状态与坐标

`/observation` 的顶层字段如下：

| 字段 | 含义 |
| --- | --- |
| `session_id`、`epoch` | 房间与仿真代次；提交动作时必须原样携带。 |
| `state_id` | 每次物理结果发布的唯一标识，用于区分状态快照。 |
| `frame_id` | 对应 PNG 的标识；没有渲染图像时可缺省或为 `null`，不影响物理状态有效性。 |
| `step`、`sim_time_s` | 当前物理步和仿真时间；`step` 不是源数据未来轨迹索引。 |
| `vehicle_id`、`own_vehicle` | 本次请求的主车辆与当前状态。 |
| `vehicles` | 以 `ego`、`P1`…`P5` 为键的同一步物理车辆状态。 |
| `current_objects` | 当前物理世界中的静态物体及其他车辆，没有未来轨迹序列。 |
| `map_version`、`coordinate_reference` | 地图指纹、源坐标系和米制单位。 |

车辆状态中的 `position_city_m` / `position_scenario_m` 是源城市 XY 米制位置，`heading_rad` 是弧度朝向，`speed_m_s` / `velocity_m_s` 是当前速度。规划应使用源城市坐标与 `/map` 的几何，不要再次减去网页原点，也不要把引擎内部的 `position_runtime_m` 混入源地图。

这些观测是仿真引擎当前物理状态的读取，不是相机检测或带遮挡误差的感知输出。静态场景使用固定的全场景重建先验，可能包含原记录较晚才观测到的路障；这与历史自车“当时已经知道什么”是不同的数据范围。接口不会提供原自车或其他车辆的未来记录轨迹。

房间报错或正在关闭时，`/observation` 返回非成功 HTTP 状态，不会将旧缓存位姿作为当前观测继续提供。示例客户端遇到该响应会停止控制并尝试释放租约。

## 五车信息与 Planning 的接入边界

外部模块现在可以读取新增五车的当前位置、朝向、速度、尺寸和车辆 ID，并用 `/map` 获取更新后的道路及独立的附着状态先验。接口只提供当前状态，没有车辆位姿历史；需要运动历史的模块应在客户端按车辆 ID 累积过去的观测，同一物理 `step` 不重复计入，房间 `session_id` 或 `epoch` 改变时清空历史。

AI 模式的 `route` 是内置控制器当前的几何路线计划，`goal_scenario_m` 是其巡航目标；`route_scope: "controller_plan_not_prediction"` 表明它既不是带时间的运动预测，也不是未来实际轨迹。手动或外部控制车辆没有已知的驾驶意图，`route` 和 `goal_scenario_m` 为 `null`，`route_scope` 为 `"unavailable"`。实际走过的轨迹只能由后续物理观测积累，不能用计划路线替代。

仓库中 `Planning_M4_scene0980_handoff` 的交付代码仍读取冻结的 CSV 帧与物体记录，尚未调用这些接口，因此还不会因新增五车而调整规划。示例客户端的 `plan()` 也仍只返回刹车。现有原生验证中的测试控制器会根据 P1 的当前纵向间距让自车行驶或制动，这证明观测到动作再到物理反馈的链路可用；它不是 M4，也不能作为动态预测或路线重规划的效果证据。

Planning 同学接入时，应将冻结帧输入替换为当前 `/observation` 和 `/map`，在自己的模块中完成预测与规划，再把输出交给本地控制器转换成 `/input` 的动作。持续读取后续真实状态确认执行结果，才形成该 Planning 模块的闭环。地图更新的效果还需在相同物理场景、车辆初态、目标和同一个规划器下比较旧图与更新图；仅能读取五车或测试控制器成功刹车，不能说明更新地图带来了改善。

## 动作与失联处理

一次批量动作的请求体形状为：

```json
{
  "session_id": "CURRENT_SESSION_ID",
  "epoch": 1,
  "lease_token": "VEHICLE_LEASE_TOKEN",
  "seq": 1,
  "observation_step": 12,
  "actions": {
    "P1": [0.0, -1.0],
    "P2": [0.0, -1.0]
  }
}
```

动作格式是 `[转向, 油门/制动]`，两项都必须是有限数，范围为 `[-1, 1]`。示例 `[0, -1]` 表示直行方向制动。规划器如输出轨迹或目标速度，应在本地增加控制器，将其转换成这个动作格式。

`seq` 在同一租约内严格递增。`observation_step` 不得来自未来，也不能落后当前物理步超过服务端允许的窗口；默认窗口为五步。旧会话、旧代次、过期租约、重复序号、未认领车辆以及越界动作会被拒绝，批量动作不会只执行其中一部分。

HTTP 动作请求成功表示服务端已接受并放入动作信箱，不表示车辆已经执行。认领、释放、模式切换和新动作在后续服务端物理步生效；已经开始执行的一步可能仍使用先前控制。请以随后读取的实际车辆状态确认执行结果。动作有效期由服务端从接收时刻计算，并在进入物理步前再次检查；排队延迟导致动作过期时会制动，客户端不需要与服务器同步时钟。

手动输入约 0.4 秒未更新时先制动，持续失联后释放并回到 P 车默认 AI。心跳只续租，不刷新动作时间。P1–P5 租约释放或失效后回到内置 AI；自车 `ego` 的外部控制失效后保持制动，不能靠继续播放原记录轨迹掩盖失联。车辆最终响应以服务端当前模式和失联状态为准。

发生连接中断时，客户端不必成功发送“最后一次刹车”才能触发保护：动作年龄、租约和观测步检查由服务端独立执行。内置 AI 只是场景中的默认行驶行为，不是同学的 Planning 模块；碰撞保护和动作校验也不能替代规划算法的有效性验证。

## 实际三维姿态与碰撞反馈

下面的车辆字段来自当前 Bullet 刚体，不能用平面车道坐标替代它们来绘制碰撞后的姿态。

| 字段 | 含义与单位 |
| --- | --- |
| `chassis_center_city_m` | 实际碰撞盒中心 `[x,y,z]`，米；XY 为源城市坐标，Z 为仿真世界高度。 |
| `position_3d_city_m` | 原生刚体原点，包含实际高度；可能与碰撞盒中心存在模型偏移。 |
| `orientation_basis_city` | 实际姿态的单位轴 `forward/left/up`；分别对应显示盒的长度 X、宽度 Y、高度 Z，可构造旋转矩阵或四元数。 |
| `pitch_rad`、`roll_rad` | 当前俯仰、横滚，弧度；车辆抬起或侧倾时不会被网页强制压回平面。 |
| `collision_active`、`current_contacts` | 本物理决策步结束时仍存在的车/源静态物体接触。每项含源 `object_id`、原生 ID、类型、`step`、接触距离 `distance_m` 和求解器法向冲量 `normal_impulse_ns`（N·s）。地面正常接触不计入事故。 |
| `last_collision` | 最近一次检测到的接触事件记录，分离后保留；含 `step`、仿真秒、前后速度（m/s）、当时接触及原生碰撞分类。它与“当前仍接触”是不同信息。 |
| `collision_event_count` | 本次运行检测到的接触事件数，重置场景后清零；持续接触不逐帧累加。 |

当前接触采样发生在每个决策步末尾，原生 `collision` 分类也可保留该决策内短暂碰撞的信号。它不是完整的物理子步事故日志，不能用末次求解器冲量当成整次撞击的总冲量或峰值；短暂接触结束后，对象 ID 也可能不可用。没有车辆变形、损伤、伤害或事故责任模型。

nuScenes 场景中来源明确的 61 个路障和 42 个锥筒采用源尺寸的固定碰撞盒，质量和逆质量均为零，表达不可移动的研究场景障碍。锥筒也采用固定近似，不模拟现实中的推动、倒伏。其他车辆仍使用正常有限质量的 Bullet 车辆模型。黄色状态区域、语义足迹和变化标注本身不自动生成实体碰撞体；仅有标注不能声称它会阻挡车辆。

可用 `python scripts/validate_interactive_collisions.py --out outputs/collision-check --speed 3` 复现真实源路障撞击；`--replay-only` 验证原记录轨迹、固定源物体位置和源数据未改变。撞击验证仅设置测试车辆初始位置，运行中不改坐标，也不通过碰撞后强制刹车冒充物理阻挡。

## 合成交通的受限观测接口

持续交通实验新增 `/perception` 与 `/planning-map`，分别提供 50 米全向理想合成测量、无静态物体先验的道路图。它们不返回其他驾驶者内部意图；旧端点继续用于兼容与调试，尚未做访问隔离。新模式、事件时间和完整边界见[合成交通阶段一](synthetic_traffic.md)。

## 在线临时状态与自动交通（当前推荐）

新自动模式支持 1–12 辆 P 车，默认无人驾驶，拒绝 `kind=manual`；外部租约 `kind=external` 不变。不要依赖固定 P1–P5 数量，应读取房间车辆 ID。下面仅描述受限输入，前文旧 `/map`、`/observation` 为兼容调试协议。

`GET /planning-input?session_id=...&vehicle_id=P8&map_policy=observed` 原子返回当前 `/perception` 测量及 `state_layer`。`observed_through_step` 与顶层 `step` 相同；`road_version` 对应 `/planning-map` 的 `map_version`，`state_version` 标识该观察车的状态快照。事件含几何、来源、支持次数、证据时间、状态与可选车道 ID，不含生成器未来计划。`active` 是确认静止占用；`stale` 是未再确认，不能当作畅通；`cleared` 表示已观测原车驶离原足迹，不保证该区域绝无其他障碍。

选择 `map_policy=baseline` 返回相同物理观测与道路图但空状态层，不修改物理世界。网页选择只影响当前查看/导出，不远程更改已运行规划器；客户端通过 `--map-policy` 显式选择。

`POST /input` 可新增 `"planning_state_versions":{"P8":"STATE_VERSION"}`，键必须与 `actions` 完全一致，同时携带 `observation_step`。服务端拒绝未知、缓存过期或晚于观测步的版本；成功响应包含 `accepted_planning_versions`。每观察车保留最近 64 个变化版本，按新观测及时提交。回执表示接受请求，不证明执行或规划器确实使用数据。

示例 `python scripts/interactive_planner_client.py --session-id ROOM_ID --vehicles P8 --live-map --map-policy observed` 将状态层加入 `plan()` 第三个参数并随动作声明版本；每个此类客户端只控制一辆车。`plan()` 仍返回刹车。详见[当前操作和验证边界](synthetic_traffic.md)。

### 近场交通与 IDM

创建房间可在 `traffic` 中设置 `area=near_recording`、`following_model=idm`、`driver_style=mixed`。它们只影响实验生成/背景驾驶行为，不赋予 ego 内置规划。源 ego 保持位置并等待外部控制。网页默认使用这组配置；省略参数的旧客户端沿用独立街区和旧近场制动基线。

`/state` 的 `interactive.traffic_area` 提供所选区域与起点距离；背景车 `route.following` 包含 IDM 参数、当前前车间距、期望加速度和保护介入，供观众诊断。上述驾驶风格、内部路线和控制诊断不进入 `/perception` 或 `/planning-input`，规划器只能自行从当前观测推断运动。背景行为模型与 Planning 地图输入策略是两个不同实验因素，研究地图效果时不要同时更换跟车模型。

### 障碍物占用与空闲证据

`/planning-input` 状态层现支持 `stationary_obstacle_occupancy`。完整旧足迹经过连续至少 2 秒理想空闲扫描后才清除，原因是 `repeated_observed_free_previous_footprint`；目标消失且缺少覆盖证据仍为 `stale`。`free_space_checks` 是本车过去确认区域的当前几何扫描结果，不能当作普遍的无遮挡感知保证。路障清除安排只属于生成器，受限输入不输出施工时刻计划。静态源物体与生成物体按 `object_origin` / `source_scope` 区分，均不构成永久道路写入。配置、物理近似与验证见[施工事件说明](construction_events.md)。
