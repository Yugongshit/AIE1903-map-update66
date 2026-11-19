# 黄子明部分的开发准备与验收

本轮面向等待同学数据期间可以独立完成的开发工作。提供可运行的地图维护接口、记录场景工作台、逐车控制接口、未来轨迹契约、外部算法进程桥接、配对实验和数据清单工具。真实上游字段、规划算法及控制器实现由后续联调提供。

| 工作 | 已准备的入口 | 验证范围 |
|---|---|---|
| 上游身份、来源、质量含义及未知项 | 道路状态 /evidence，结构 /structure-evidence | 原子校验、时间／版本绑定、拒绝历史改写 |
| 坐标与时钟适配 | prepare_evidence_request.py | 显式旋转／平移／缩放、XYZ投影授权、整数纳秒原点换算 |
| 道路结构新增／替换／删除 | /map-patch | 要素前置哈希、几何及拓扑校验，非合成发布须匹配已支持的上游结构证据 |
| 版本、来源和回滚 | /map-versions、/map-rollback | 回滚产生新版本，保留历史；可重试的发布收据 |
| 道路状态和动态对象 | /road-states、/observation、/history | 三类数据分别维护，同一帧版本绑定 |
| 场景选择与独立原记录分支 | 工作台 /，/catalog、/open | 从可信本地数据库选择场景、重建指定原记录帧；旧会话令牌失效 |
| 当前有效原车接管 | /takeover | 原实体和位姿连续，单车、四车及全车接管检查 |
| 逐车权限及同步多车 | /actor-observation、/actor-history、/actor-action | 全局／半径内理想状态；收齐本帧全部动作后统一推进 |
| 未来轨迹与任务 | /plans、/tasks | 当前状态起点、时序、坐标、控制权、记录范围校验；源车道拓扑可达性 |
| 精确车道几何检查 | source_lane_coverage、coverage_policy | 使用显式源车道多边形检查位置及完整车身；缺少多边形时明确不可用 |
| 动作超时和恢复 | /resume、/controls | 超时冻结；同一实体恢复等待新动作；管理端可找回当前令牌 |
| 实验持久记录与重启 | events.jsonl、--recover | 校验源记录哈希及每次已记录物理结果，以新会话重放恢复 |
| 地图输入配对实验 | run_recorded_pair.py | 同一原始物理地图、起点、参与者模式、步长和worker命令；只改变规划地图输入 |
| 结果记录与统计 | /report、comparison.json | 实际路程、轨迹跟踪采样误差、逐帧接触行数、回放误差、地图与计划记录 |
| 开发／验证／测试分组 | prepare_dataset_manifest.py | 明确分组、文件哈希、重复场景拒绝、跨集合分组泄漏检查 |
| 可见的统一入口 | recorded_workbench.html | 源地图、实时对象、控制模式、版本、道路状态、规划意图、导入与导出 |

**运行工作台**

使用已有仿真环境。RECORDED_SERVICE_KEY 通过环境提供，浏览器使用相同的管理密钥。页面和 API 由同一服务提供。

~~~bash
cd /workspace/AIE1903-map-update66
source .venv/bin/activate
export MPLCONFIGDIR="$PWD/.runtime/matplotlib"
export XDG_CACHE_HOME="$PWD/.runtime/cache"
run_dir=$(mktemp -d "$PWD/outputs/recorded-workbench.XXXXXX")
python scripts/run_recorded_service.py   --database 'AIE1903 原始资料/simulation/metadrive/metadrive/assets/waymo'   --out "$run_dir/session" --port 8094 --action-timeout 30
~~~

本机浏览器打开该服务的根路径。远程使用 HTTPS 反向代理或 SSH 隧道。数据库由启动命令选定，HTTP不会接收任意pickle路径。页面显示源道路XY投影和当前对象尺寸框，保留旋转、俯视、缩放与平移；当前观测为状态级输入。点击连续步进时，所有已接管车辆使用页面所示固定测试动作，接入外部worker时停止页面步进。

POST /open 传当前 session_id、目录 index 和原记录起始 step。该操作关闭当前原生世界并创建新会话。它用于原记录独立对照，不能恢复当前反事实世界到任意过去帧。要恢复已执行的反事实结果，使用下述日志重放。

**结构证据、地图发布与回滚**

所有管理请求使用服务密钥；所有修改请求沿用 session_id、map_version、state_version、observation_step 绑定。/map-patch 和 /map-rollback 还需要 request_id。完全相同请求可重试，复用ID改变内容会被拒绝。

POST /structure-evidence 需要 request_id 及 records。每条记录包含以下字段。

~~~json
{
  "evidence_id": "upstream-change-001",
  "source_object_id": "source-marking-001",
  "change_type": "add_marking",
  "observed_time_ns": "0",
  "source_ids": ["upstream-frame-001"],
  "score": 0.8,
  "score_semantics": "upstream uncalibrated quality score",
  "unknown_fields": [],
  "decision": "supported",
  "coordinate_frame": "MetaDrive centralized scenario XY, metres",
  "operations": []
}
~~~

这是字段示意，operations必须填写非空、有效操作后才能提交。decision接受supported、unsupported、unknown，服务保留上游判定。非合成地图发布必须引用同一地图版本上已接收的supported记录，操作内容须与该证据中的操作一致。质量分数只按显式含义记录，服务不将它当正确概率。

POST /map-patch 包含operations与provenance。可运行的合成补丁在 examples/recorded-preparation/synthetic-map-patch.json。每个操作有operation、feature_id、source_event_id；replace/delete还需before_sha256，add/replace需完整feature。车道操作须提供中心线、多边形和拓扑。支持的要素范围沿用simulation_bridge.py，未知类型明确拒绝。

provenance包含source、reason和布尔synthetic。测试补丁明确synthetic=true。补丁仅改变Planning获得的地图，physical_map_version保持原始物理道路指纹。历史道路状态保留观测几何，已删除的关联道路ID会在unresolved_map_aliases中显式列出。

POST /map-rollback 使用target_version和provenance。任何地图修改都会生成新state_version，旧计划和待收集动作随之失效。GET /map返回当前地图、内容哈希和物理地图指纹；GET /map-versions返回完整修订链。

**坐标与时间转换**

prepare_evidence_request.py接收源events数组、显式变换JSON和当前observation文件，输出request.json及adaptation.json。变换格式如下。

~~~json
{
  "source_frame": "source_city_frame",
  "target_frame": "MetaDrive centralized scenario XY, metres",
  "rotation_rad": 0,
  "translation_m": [0, 0],
  "scale": 1,
  "source_time_origin_ns": "0",
  "target_time_origin_ns": "0",
  "drop_z": false
}
~~~

数字仅展示格式，真实转换值须根据数据坐标定义填写。换算为旋转后缩放加平移，时间采用整数纳秒减源原点加目标原点。XYZ输入必须显式允许drop_z。原始文件哈希加入来源记录，原始输入保持完整。

~~~bash
python scripts/prepare_evidence_request.py   --input source-events.json --transform transform.json   --observation current-observation.json   --submission-id intake-001 --out outputs/intake-001
~~~

**车辆控制、观测权限与轨迹**

POST /takeover可指定observation_scope为 {"kind":"global"} 或 {"kind":"radius","radius_m":50}。半径观测按该车当前中心裁剪参与者，历史按各历史帧自己的中心裁剪。没有位置信息的信号及全局道路状态不进入半径视图，静态地图可由该车读取。两种模式均为理想状态接口，观测模式随响应明确给出。

coverage_policy可选map_envelope或source_lane_polygons。后者在接管时要求完整车身位于有效源车道多边形内，并在每次物理推进后检查；离开时冻结服务。没有源多边形时严格模式拒绝接管。默认延续原有地图包围范围检查。source_lane_coverage提供可用性及完整车身覆盖诊断。

逐车客户端使用自己的控制令牌作为Bearer身份，访问以下接口。

| 接口 | 内容 |
|---|---|
| GET /actor-observation?actor_id=... | 自身权限范围内的当前状态与自己的目标 |
| GET /actor-history?actor_id=... | 自身权限范围内的已发生历史 |
| GET /actor-map?actor_id=... | 当前静态规划地图 |
| POST /plans | 该车未来轨迹意图 |
| POST /tasks | 该车道路目标与可达性结果 |
| POST /actor-action | 该车当前帧动作，收齐全部受控车动作后统一推进 |

上述POST仍需原有帧版本绑定及control_token。/actor-action额外提供actor_id、action，可选plan_id。响应advanced=false时表示本车动作已收到，客户端继续读取自己的观测等待帧变化。其他车辆的未提交动作不会出现在响应中。地图／道路状态变化会清空旧帧动作邮箱，客户端须重新计算。

/plans中的plan包含plan_id、actor_id、coordinate_frame、points。每个点包含time_offset_s、position_m、heading_rad和speed_m_s。时间从0开始严格递增，不能超过记录剩余时长。首点与当前车的位置、航向、速度分别允许5厘米、0.05弧度、0.1m/s的数值接线误差。这些是可调整的接口校验默认值。轨迹提交保存意图，不推进物理。动作若引用plan_id，必须对应当前版本下该车已接收的轨迹。

/tasks接收actor_id与goal_m，根据显式源车道多边形定位起终点，再沿有向exit_lanes检查可达性。结果为reachable、unreachable或outside_available_lane_geometry。该结果供规划器使用，服务不生成完整驾驶轨迹。目标附带地图版本，地图变化后会标记失配。

**外部算法进程接线**

PlannerProcess使用有读写超时的JSON-lines协议。每行请求包含当前observation、静态map、tasks及controlled_actor_ids。worker每次必须返回plans、actions、implementation及原样回传的state_version。过期或其他观测版本的响应会被拒绝。plans为每车一条未来轨迹，actions为跟踪控制器给出的逐车动作。implementation用于标识实际实现。每车目标和计划ID独立，协调进程统一提交这一帧。

scripts/contract_smoke_worker.py只产生短时外推意图和固定制动，用于契约验收。其implementation明确为synthetic_contract_smoke_fixed_brake。同学的Planning与跟踪控制器通过替换worker命令接入。这个进程接口面向可信本地程序，不是执行不可信代码的隔离沙箱。

~~~bash
python scripts/run_recorded_planner_client.py   --url http://127.0.0.1:8094 --actors 332 333 --steps 5   --out outputs/planner-client-new   --worker .venv/bin/python scripts/contract_smoke_worker.py
~~~

客户端使用当前环境中的RECORDED_SERVICE_KEY认领车辆。需要任务时用--tasks提供从actor_id到goal XY的JSON映射。若所选车辆已有控制权，服务明确拒绝，操作员可先新建独立会话。计算或通信异常时客户端停止，服务器按超时策略冻结。

**配对实验与报告**

~~~bash
python scripts/run_recorded_pair.py   --database 'AIE1903 原始资料/simulation/metadrive/metadrive/assets/waymo'   --index 0 --start-step 10 --actors 332 333 --steps 5   --patch examples/recorded-preparation/synthetic-map-patch.json   --out outputs/map-pair-new   --worker .venv/bin/python scripts/contract_smoke_worker.py
~~~

两分支依次创建原生世界，使用同一源数据、起始帧、车辆集合、模式、步长和worker命令。启动时核验物理地图及所有当前参与者状态相同。updated分支仅改变规划地图。闭环演化由各自实际动作决定，后续轨迹可以不同。原始未来记录不进入worker请求。

comparison.json保留完成标志、双方地图版本、worker标识、跟踪误差采样和执行摘要。每分支单独保存日志。失败也保存completed=false及原因。跟踪误差只在轨迹明确包含下一物理时刻样本时统计，缺失时报告采样数0。逐帧接触行数独立报告，不换算成独立碰撞次数或归责。未提供真实算法与明确评价目标时，不输出规划成功率。

**重启与冻结恢复**

GET /controls由管理端取回当前会话令牌。POST /resume仅解除动作超时冻结，保留当前实体和状态，更新state_version并清空待提交动作。原生异常、记录终点或越界不能通过resume掩盖。

~~~bash
python scripts/run_recorded_service.py   --database 'AIE1903 原始资料/simulation/metadrive/metadrive/assets/waymo'   --recover outputs/previous-session/events.jsonl   --out outputs/recovered-session-new --port 8094
~~~

恢复过程检查源记录文件哈希、场景、物理地图和每次已记录物理结果，依次重放已完成的接管、地图、证据、任务、计划与动作。新会话重发控制令牌，旧令牌失效。无法精确复现、日志损坏或末尾存在结果未知的操作时拒绝恢复。此方法是经过结果核验的命令重放，不能保证跨引擎版本、硬件或任意不确定物理条件都可恢复。

**数据分组与验收**

prepare_dataset_manifest.py读取显式path、scene_id、group_id、split、source清单，冻结文件哈希。split只接受development、validation、test。同源片段及重叠／同地点分组需由数据负责人定义；工具验证同组不跨集合、无重复场景或重复文件内容。不会随机拆帧或自动声称独立测试。

~~~bash
python scripts/prepare_dataset_manifest.py --root DATASET --input split-rows.json --out manifest.json
python scripts/prepare_dataset_manifest.py --root DATASET --input manifest.json --verify
python scripts/check_recorded_preparation.py   --database 'AIE1903 原始资料/simulation/metadrive/metadrive/assets/waymo'   --out outputs/preparation-check-new --browser
python -m unittest discover -s tests -v
~~~

开发验收覆盖原生HTTP、四车动作屏障、全车接管边界、地图补丁与回滚、独立原记录分支、逐车权限、浏览器操作、原生日志恢复和外部worker闭环。官方样例第10帧的61辆可接管车中有车辆在下一帧失效，全车推进按规则冻结；四车批次和双车配对运行已实际推进。该结果不代表全车能连续运行完整记录。

后续依赖同学的内容包括真实字段与时空对齐参数、可信变化证据、Planning及跟踪控制器实现、真实目标与评价指标，以及独立验证／测试数据。传感器模拟和可响应背景策略仍是交接中的后续可选扩展，当前开发主线采用状态级观测与严格记录背景。

2026-11-19验收结果为完整763项回归中762通过、1项原始fixture缺失跳过，新增18项专项通过，原生HTTP与浏览器24项检查通过。另已实际运行双车地图配对实验、源文件哈希验证后的原生日志恢复、HTTP worker闭环、数据清单冻结／复核和时空适配CLI。使用MetaDrive官方开发样例及明确标记的合成证据，尚无真实同学算法的效果结论。
