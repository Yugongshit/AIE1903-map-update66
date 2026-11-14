# 阶段二：施工实体与观测清除

本轮是阶段二的第三步：在已有自动交通中加入一个真实碰撞体及其生命周期。仍未实现行人、事故损伤、多路线起终点、完整路口规则或同学的 Planning 算法。

## 操作

更新 main 并重启本地服务。在 nuScenes 的“交通实验”中新建房间，交通区域选“回放起点附近”，模型选 IDM，事件选 **开场施工占道 → 定时清障**。建议 8 辆车、快慢 ±30%、持续 60 秒。开始后可观察 P1 或 ego，也可点击“查看施工路障”将画面定位到实体；这只调整本人的视角，不改变车辆或其观测。

橙色盒体及“仿真施工路障”标签表示新生成物体，不是 nuScenes 原始路障。右侧事件状态是观众看到的物理世界真值；在线地图面板是当前观察车根据自身观测得到的状态。两者允许不同步。原有“定时停车”事件仍可选，也可选“无预设事件”。

## 世界如何变化

在源道路闭环上，选择 P1 出生位置前方不超过 20 米、且位于下辆车之前的位置，生成一个 1 × 2.5 × 1 米的固定路障。检查带余量的车身/障碍包围盒，拒绝与任何源固定物体、ego 或已生成车重叠的配置。种子通过车辆出生相位决定位置，源地图与源记录不修改。

路障在场景初始化时就存在，没有行驶中突然出生。复用已验证的尺寸一致碰撞盒，Bullet 质量为 0、逆质量为 0，表达不可移动的固定障碍。车辆由物理动作减速，背景控制器的近场保护处理障碍，不给 ego 添加内置规划器。

指定的仿真时间到达后，从物理引擎删除该实体。暂停不消耗仿真时间；重建房间恢复初始路障。该过程是**离散清障近似**，不模拟工人、搬运、车辆损伤或清障机械；不能将其称为真实施工过程。运行中不搬动车辆，不靠删除车或改车位姿让队列恢复。

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
    "construction_duration_s": 60,
    "stop_enabled": false
  }
}
```

`construction` 默认 false，持续时间范围 5–120 秒；`stop_enabled` 默认 true，兼容旧定时停车实验。API 可以显式叠加两类事件，网页一次选择一种。`/state` 的 `synthetic_events` 提供当前生命周期状态用于观众诊断，不提供给受限 Planning 输入。生成对象的 `synthetic=true`、`source_scope=synthetic_construction_event` 明确标记来源。

`/planning-input` 仍原子返回当前观测、状态层和版本，增加的 `free_space_checks` 只含过去已知区域的当前观测结果，不含计划清障时刻。外部客户端仍需实际接入并消费 `state_layer`，示例仍只刹车。旧调试端点的访问隔离尚未完成。

## 验证

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
