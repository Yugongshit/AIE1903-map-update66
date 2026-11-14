# 阶段二：施工实体与观测清除

当前是阶段二的多施工点基线：随机生成多个独立物理碰撞体，分别观测、分别清障。多路线、路口让行、行人及持续事件见 [阶段二模式](stage2_continuous_traffic.md)。仍未实现事故损伤形变、任意城市起终点、完整路口法规或同学的 Planning 算法。

## 操作

更新 main 并重启本地服务。在 nuScenes 的“交通实验”中新建房间，交通区域选“回放起点附近”，模型选 IDM，事件选 **随机多处施工 → 分别清障**。建议 8 辆车、快慢 ±30%、施工点数量 2–5、清障区间 45–120 仿真秒。开始后可观察 P1 或 ego，也可点击“查看下一处路障”轮流定位到仍在场的实体；这只调整本人的视角，不改变车辆或其观测。

橙色盒体及“仿真施工路障”标签表示新生成物体，不是 nuScenes 原始路障。右侧事件状态是观众看到的物理世界真值；在线地图面板是当前观察车根据自身观测得到的状态。两者允许不同步。原有“定时停车”事件仍可选，也可选“无预设事件”。

## 世界如何变化

数量按独立种子流在整数区间内均匀抽取，默认 2–5 处，允许 1 ≤ 最少 ≤ 最多 ≤ 8；相等可固定数量作对照。每处是 1 × 2.5 × 1 米固定路障。位置用另一个随机流选择：第一处优先在 P1 前方 10–20 米内查找安全候选，其余候选覆盖当前源道路闭环，随机打乱后筛选。不是全路网均匀分布，也不是学习到的施工热点模型。

生成时避开明显弯角；带 0.5 米余量的路障与带余量车身/源物体间再留至少 4 米，路障彼此沿道路至少间隔 25 米、平面足迹间隔至少 20 米。使用固定的有限候选集合，不通过移动既有车或虚构道路补足数量。安全位置不足时减少生成，观众面板显示抽样数量、实际数量与容量不足原因；不会强行重叠出生。当前安全约束是低速单路线近似，不等于完整交通工程选址验证。

每处都有 `construction-C1`、`construction-C2` 等唯一编号。界面显示在场/已清除数量并按编号标注；清除一处不会同时删除其余施工点。源地图与源记录不修改。

路障在场景初始化时就存在，没有行驶中突然出生。复用已验证的尺寸一致碰撞盒，Bullet 质量为 0、逆质量为 0，表达不可移动的固定障碍。车辆由物理动作减速，背景控制器的近场保护处理障碍，不给 ego 添加内置规划器。

初始化时每处使用场景种子加事件编号的独立随机流，从配置区间内抽取各自清障时刻，到该时刻后从物理引擎删除实体。默认三角分布范围 45–120 仿真秒、峰值 70 秒，作为短时占道演示先验；不代表真实施工时间分布。范围可调，但必须满足 5 ≤ 最早 < 最晚 ≤ 3600 秒。暂停不消耗仿真时间；同种子重建恢复初始路障并复现相同抽样。点击“换一个随机种子”后创建新房间才会生成新实验。清障采用独立随机流，改变区间不会改变车辆出生位置或驾驶风格。该过程是**离散清障近似**，不模拟工人、搬运、车辆损伤或清障机械；不能将其称为真实施工过程。运行中不搬动车辆，不靠删除车或改车位姿让队列恢复。

## 观测与地图状态

范围内的路障/锥桶与车辆一样，经连续至少 2 秒、至少 5 个不同物理步的静止观测后形成占用证据。障碍事件类型是 `stationary_obstacle_occupancy`；车辆仍为 `stationary_vehicle_occupancy`。只表示足迹占用，`lane_closed=false`，永久结构写入 0。仅从观测不能推断整个道路封闭、施工原因或清障时间。

对已确认的障碍足迹，观测模块支持当前空闲区域检查。只有整个旧足迹都在观察车 50 米范围内，并且没有任何当前实体车身/障碍与之相交，才返回 `observed_free=true`。检查包括观察车自身，以及中心在 50 米外但车身伸入该区域的物体；尺寸缺失时不确认空闲。查询区域只能来自本车过去已经确认的障碍，不能从场景生成计划取得未来位置。

地图更新器再累积至少 2 秒连续空闲证据，才发布 `cleared`，原因为 `repeated_observed_free_previous_footprint`。仅从对象列表消失、超出视野或缺少观测，只会变为 `stale`。原区域被另一辆车占住时也不清除。该证据是**理想无噪声、无遮挡的几何扫描**，尚未实现相机/雷达感知模型；后续引入遮挡、漏检时，必须同时修改覆盖与清除判据。

地图更新器不读取施工持续时间、实体删除状态或事件计划。源固定物体与生成物体都可按当前观测产生占用，事件的 `object_origin` 保留来源；不能把这些理想仿真观测算作真实感知模型成果。

## 接口

房主的 `/api/interactive/open` 中可配置：

```json
{
  "scene_id": "nuscenes-scene0980",
  "version": "updated",
  "traffic": {
    "area": "near_recording",
    "following_model": "idm",
    "driver_style": "mixed",
    "vehicle_count": 8,
    "speed_spread": 0.3,
    "seed": 0,
    "construction": true,
    "construction_count_min": 2,
    "construction_count_max": 5,
    "construction_min_s": 45,
    "construction_max_s": 120,
    "stop_enabled": false
  }
}
```

`construction` 默认 false；启用后默认随机 2–5 处，旧单点对照需显式将 `construction_count_min` / `construction_count_max` 都设为 1。清障区间默认 45–120 仿真秒；`stop_enabled` 默认 true，兼容旧定时停车实验。API 可以显式叠加两类事件，网页一次选择一种。旧 `construction_duration_s=D` 仅兼容迁移为 `[max(5,0.75D),2D]` 的随机区间，不再表示固定时刻；不能与新区间字段同时提交。`/state` 的 `synthetic_events` 提供全部施工点的当前生命周期状态，`construction_generation` 提供抽样/实际数量及容量不足原因，用于观众诊断，不提供给受限 Planning 输入。生成对象的 `synthetic=true`、`source_scope=synthetic_construction_event` 明确标记来源。

`/planning-input` 仍原子返回当前观测、状态层和版本，增加的 `free_space_checks` 只含过去已知区域的当前观测结果，不含计划清障时刻。外部客户端仍需实际接入并消费 `state_layer`，示例仍只刹车。旧调试端点的访问隔离尚未完成。

## 房间恢复

“退出观看”不会关闭共享世界。点击“启动或恢复场景”会先检查当前房间：若已存在，则恢复房间及本标签页保存的凭证，明确提示当前填写的新参数尚未应用；房主需要先“关闭房间”再新建。直接打开首页时也会恢复本标签页已保存凭证的当前房间。新浏览器没有凭证时只能观看，不自动取得房主权限。

## 历史固定清障验证（修复前）

```bash
python -m unittest discover -s tests -v
python scripts/validate_construction.py --out outputs/construction-new
python scripts/validate_construction.py --out outputs/construction-density-new --count 12 --seed 7
python scripts/validate_construction.py --out outputs/construction-contact-new --collision-probe
python scripts/check_synthetic_traffic.py --url http://127.0.0.1:8093 --construction --out outputs/construction-browser-new
```

正常运行检查真实碰撞体、静止确认、清障后空闲确认、车辆恢复、地图不变与逐步位移。撞击测试故意由测试输入持续驱动车辆碰撞该路障，并在首次接触后继续施加 5 秒油门，检查路障未移动、车辆未穿过；它不是正常 AI 行驶效果，也没有损伤模型。

8 车、种子 0 的 150 秒运行中，首批观察车在 2.1 秒确认占用；60 秒执行清障，60.1 秒发布首个不含实体的物理帧；附近观察车 65.6 秒确认空闲。P2 驶远后直到 138.6 秒重新观测才清除。正常运行 0 碰撞，P1 受阻后恢复，物理地图不变。其他配置和浏览器结果见 [本轮证据](../demo/simulation/validation/construction-stage2.json)。

12 车、种子 7 同样运行 150 秒，0 碰撞且受阻车辆恢复，附近观察者在 73.8 秒确认清除；清障后经过的车身也会阻挡空闲确认，不能照计时器清除。撞击专项首次接触在 5.5 秒，继续驱动至 10.5 秒，固定路障位移为 0，车辆未穿过。636 项回归中 635 通过、1 项因既有原始 fixture 缺失跳过；最终交互专项 52 项、交通专项 10 项通过。

最终浏览器 24 项检查通过，包含实体显示、观测确认、空闲清除、输入策略、查看路障和重置重建。

## 历史单点随机清障验证

随机清障与房间恢复的最新证据保存于 `demo/simulation/validation/random-clearance-room-recovery.json`。原生测试使用相同的 45–120 秒区间、不同种子，并核对真正移除实体的物理时间和地图确认时间。规划输入仍不包含抽样时刻或生成参数；抽样结果只写入事后评价报告。

完整自动生成的目标与先后次序见 [后续生成器设计](traffic_generation_roadmap.md)，本轮没有实现全量 AI 随机配置。

本轮 640 项回归中 639 通过、1 原始 fixture 缺失跳过；浏览器 31 项流程检查及 1 项隐藏参数检查通过。两个种子分别抽到 66.6、107.5 秒，实际事件在对应仿真时刻执行，首个无障碍帧分别在 66.7、107.6 秒；ego 的地图清除确认分别在 72.7、115.3 秒，仍由当前观测决定。


## 多施工点验证

```bash
python scripts/validate_multi_construction.py --out outputs/multi-new --seed 0
python scripts/validate_multi_construction.py --out outputs/multi-stress-new --seed 7 --cars 12 --sites 8
python scripts/check_synthetic_traffic.py --url http://127.0.0.1:8093 --construction --room-recovery --out outputs/multi-browser-new
```

原生验证逐个核对实体质量与位移、清障时刻、至少一车的占用与清除证据、碰撞、恢复行驶，以及物理地图是否保持不变。浏览器检查多处实体与编号、数量显示、轮流定位、独立清障、Planning 输入隔离与重置复现。`validate_construction.py` 保留为显式单点对照工具。

本页单闭环对照范围是**开场生成一批、各自清障**。阶段二混合模式另行支持运行中安全补充、行人与抽象失能响应；事故损伤形变和全路网事件生成仍未实现。默认短时间多处占道用于压力测试，不能宣称对应现实施工密度。完整 AI 联合生成与真实数据校准继续按路线文档推进。

本轮结果：8 车/2 处随机路障、12 车/8 处固定数量压力配置各运行 240 秒，均 0 碰撞、路障位移 0、直接受阻车辆全部恢复，每处均有至少一车观测到占用及后续清除。644 项回归中 643 通过、1 既有 fixture 缺失跳过；浏览器 38 项检查通过，另外验证了 8 处同屏显示。详见 [验证记录](../demo/simulation/validation/multi-construction-stage2.json) 与 [8 处路障截图](../demo/simulation/validation/multi-construction-eight-sites.png)。这些是所列低速配置的结果，不构成任意密度和地图下无碰撞的保证。
