# 合成交通与在线地图状态

当前进度：阶段一闭环交通已完成；阶段二交通扩展完成第一步；阶段四提前打通临时状态更新到 Planning 接口的最小链路。阶段三感知误差、阶段四真实 Planning 接入、阶段五配对评估仍待完成。技术与训练路线见[研究设计](traffic_model_research.md)。

## 在哪里操作

GitHub Pages 是静态地图审阅，没有实时服务器。Mac 运行：

```bash
cd ~/Desktop/AIE1903-map-update66
conda activate aie1903-sim
python scripts/run_simulation_platform.py
```

浏览器打开 **http://127.0.0.1:8091/**，选择 **nuScenes scene-0980**，在右侧点 **交通实验 → 连接与房间设置**。小屏幕时侧栏在地图下方。选择默认“自动循环交通”，设置背景车数量、速度差异、种子和停车持续秒数，启动交互场景，再点击播放。车辆卡片点“观察这辆车”，上方“当前观察车的在线地图状态”显示这辆车的局部证据。查看 P8 需要先配置至少 8 辆车。

固定五个玩家席位已从界面隐藏；自动交通模式拒绝手动接管，保留外部 Planning 租约接口。人工选车驾驶、点击投放事件留到后续阶段。历史固定任务仍可选择，后台保留旧协议兼容。本模式目前只支持 nuScenes，Argoverse 会明确拒绝。

## 交通生成

从现有有向车道图筛选 200–600 米、远离源固定物体与 ego 的简单闭环，目前约 340 米。1–12 辆车按弧长间隔出生，检查碰撞盒、道路范围和至少 15 米间隔。种子决定起始相位和期望速度差异。每车有半圈目标和循环起点；使用纯追踪与近场制动、原生 Bullet 物理行驶，没有运行中位置赋值或速度归零作弊。目前还不是自由起终点路网交通，没有运行中补车、路口优先权、IDM、行人或施工生成。

P1 默认仿真第 30 秒开始制动，持续时间网页默认 60 秒（底层接口未传此参数时仍为 15 秒），然后恢复 AI。暂停不会消耗事件时间。制动命令不代表车辆立即停住。外部控制期间事件不覆盖外部动作；交还 AI 时若事件窗口尚未结束，剩余窗口继续执行。AI 从实际位置重新匹配闭环，偏离超过 2.5 米或反向超过 90°时停车并报告原因，不瞬移回路线。

ego 保持源记录初始位置，默认制动，可能看不到闭环交通。选 P1/P8 等背景车可观察近处交通。路线控制器不是同学的 Planning。

`POST /api/interactive/open` 可带：

```json
{"scene_id":"nuscenes-scene0980","version":"updated","traffic":{"seed":0,"vehicle_count":8,"speed_m_s":2.5,"speed_spread":0.3,"stop_start_s":30,"stop_duration_s":60}}
```

车辆数 1–12；速度 0.5–3 m/s；差异幅度 0–0.5，采样后速度仍限制在该范围；停车起始 5–3600 秒、持续 1–120 秒；seed 为 uint32。种子复现配置，物理轨迹还依赖软件、步长和控制输入。12 辆是支持的配置上限，不代表所有密度与行为已验证安全。

## 观测到地图状态

每个物理步为每辆车生成中心距离 50 米内的全向理想观测；字段白名单不包含他车动作、内部路线、目标或未来事件。尚无遮挡、漏检、噪声，物体 ID 来自仿真器。

每辆观察车独立积累证据：其他车速度不超过 0.25 m/s、位置持续稳定至少 2 秒且至少 5 次观测，发布 `stationary_vehicle_occupancy`。事件只表达该车身足迹占用，车道关联不明确时留空；`lane_closed=false`，不会将排队停车认作事故或整条道路关闭。超过 3 秒没有确认则标记 `stale`，不自动清除；只有观测该车速度至少 0.75 m/s 且离开原足迹才标记 `cleared`。这些阈值是可审计规则基线，不是校准后的概率。

静止检测器只读上述观测，不读 P1 停车安排或恢复时刻。永久道路结构写入仍为 0，Argoverse 的 93 项参考更新结论不变。静止车辆的临时占用与永久结构修改必须分层。

## Planning 接入与对照

- `/api/interactive/planning-map?session_id=...`：道路图与版本，不含全场景静态先验。
- `/api/interactive/planning-input?session_id=...&vehicle_id=P8&map_policy=observed`：同一步观测、该观察车的状态层、道路/状态版本。
- `map_policy=baseline`：同一物理世界与道路图，但不提供新增状态。查询切换不重建环境、不移动物体，也不主动改变外部 Planning 客户端的配置。
- 原 `/perception` 仍可单独取当前局部观测。网页自动交通导出改为 `planning-input`。

动作通过原 `/claim`、`/input` 接口提交；可携带 `planning_state_versions`，服务端验证版本存在且不来自未来，成功响应回显接收版本。接收回执不能证明算法实际利用了地图或动作已执行。示例客户端：

```bash
python scripts/interactive_planner_client.py --session-id ROOM_ID --vehicles P8 --live-map --map-policy observed
```

加入凭证按提示输入。`--live-map` 当前每进程只控制一辆车，不自动合并各车视野。`plan(observation, vehicle_id, map_context)` 获得当前观测和 `map_context.state_layer`；同学替换该函数或接入自己的客户端。示例仍只刹车，没有真实同学 Planning 的重规划效果。

原 `/state`、`/map`、`/observation` 保留调试权限，**未做盲测访问隔离**。正式实验还需封闭真值端点、记录规划器版本与控制历史。当前只完成输入策略切换，尚未完成多种子批量配对实验；不能据此声称地图更新改善了 Planning。

## 本轮验证

`validate_live_map.py` 在 8 车、速度差异 ±30%、停车 60 秒配置下运行 120 秒：P1 在 30.2 秒达到静止条件，首个观察车在 32.2 秒发布占用；P6/P7/P8 在 93.4 秒确认驶离原足迹；距离较远的 ego 没有获得这些事件，物理地图哈希不变，碰撞事件为 0。这只覆盖该配置。

```bash
python scripts/validate_live_map.py --out outputs/live-map-new
python scripts/check_live_planning_transport.py --out outputs/live-transport.json
```

真实 HTTP 检查使用暂停的原生房间和刹车示例，验证版本回执与输入策略不改变物理观测，不作为算法或运动响应证据。阶段一历史验证见下，当前回归证据另存 `demo/simulation/validation/live-map-stage2.json`。

## 验证

```bash
python -m unittest discover -s tests -v
python scripts/validate_synthetic_traffic.py --out outputs/cycle-check-new --seconds 600
python scripts/validate_synthetic_traffic.py --out outputs/cycle-handover-new --seconds 600 --stop-duration 60 --handover
```

第二项检查连续十分钟、实际停车和恢复、逐步位移、碰撞事件与完成圈数。第三项延长停车并在第 100–103 秒给 P2 玩家制动控制，随后交还 AI，以检查跟车制动与恢复。输出目录必须不存在。报告包含任务配置、检查点和轨迹摘要哈希；物理步长 0.1 秒，每步有五个 0.02 秒 Bullet 子步。PNG 渲染不驱动物理时间。

阶段一 AI 使用低速纯追踪与近场制动，尚无完整 IDM、路口优先权、交通灯和绕障规划；不保证任意玩家输入或任意参数都无碰撞。源锥桶与路障沿用固定尺寸碰撞盒；车辆无损伤模型。物体均需按来源区分：源记录、源固定场景先验、合成交通，不能将合成车辆算作传感器检测成果。

物理道路仍固定为 updated；旧图/更新图作为独立 Planning 输入的配对实验尚未接入。Argoverse 的 93 项参考更新与自主模型结构写入 0 的既有结论不变。

本轮验证记录见[阶段一证据](../demo/simulation/validation/synthetic-traffic-stage1.json)：默认场景与延长停车/接管场景各运行 600 秒，五车均完成三圈、碰撞事件为零；同种子默认运行的逐步位置/速度摘要一致。完整回归 603 项中 602 通过、1 项因原始 fixture 缺失跳过，最终受影响专项 56 项通过。该结果只覆盖列出的配置与工况，不代表任意交通行为都安全。

当前网页联调命令（先启动本机服务；检查自动交通，不再领取玩家席位）：

```bash
python scripts/check_synthetic_traffic.py --url http://127.0.0.1:8091 --out outputs/cycle-browser-new
```
