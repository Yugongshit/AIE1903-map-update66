# 地图更新在前，仿真与 Planning 在后

MetaDrive 是通过 Python 运行的开源驾驶仿真软件库，提供三维渲染、车辆物理和控制。
ScenarioNet 负责场景转换与管理，`converted/` 是转换后的场景数据库目录。

当前接入保留地图模块的两类输出：**结构修改写入道路底图；状态事件作为带来源、时间和状态的附着层**。
Planning 可以同时读取更新后的道路、当前附着状态和已加载的物理障碍。

## 已有环境：同步后直接打开

在 Mac 的项目根目录执行，例如 `~/Desktop/AIE1903-map-update66`：

```bash
git pull --ff-only
conda activate aie1903-sim
python scripts/run_updated_simulation.py --database-path demo/simulation/argoverse/updated --mode replay --render 3D
```

这是已修改的 Argoverse 道路。完整数据库随仓库提交，无需另下压缩包或重建已有环境。
默认 `replay` 沿记录轨迹自动行驶，无需键盘开车。关闭窗口后，可运行修改前的同场景对照：

```bash
python scripts/run_updated_simulation.py --database-path demo/simulation/argoverse/original --mode replay --render 3D
```

查看 nuScenes 道路上的障碍状态：

```bash
python scripts/run_updated_simulation.py --database-path demo/simulation/nuscenes-states --mode replay --render 3D
```

Mac 本地窗口不加 `--offscreen`。Esc 退出，Q 返回跟车视角，B 使用俯视相机。
二维视图用 `--render 2D`。原 `scenarionet.sim` 入口不会加载本项目新增的状态标注与持久障碍适配。

## 两类变化实际加载了什么

| 输出 | 仿真中的作用 | 当前随仓库提供的数据 |
| --- | --- | --- |
| 道路结构 | 修改车道面、中心线、标线、连接关系及通行属性，成为仿真和规划读取的道路图 | Argoverse 的 93 项已交付参考更新：27 项新增、66 项替换，涉及 84 项车道、9 项横道 |
| 附着状态 | 将事件与已有对象按确切实例 ID 对齐，显示观测位置、时间、活动/未知等状态；不写进纯道路底图 | nuScenes 的 23 个算法状态事件，311 次支持变化的观测，全程关联 74 个不同实例 |

这两个数据库属于不同城市坐标和场景，分别运行。Argoverse 使用同场景的 582 帧实测自车位姿，
约 58.1 秒；现有车辆检测没有可用的三维轨迹，因此该场景只回放自车。
nuScenes 保留原有车辆、行人和自行车的动态轨迹。

### 道路修改的来源

Argoverse 的 93 项来自 pipeline 的 `material_reference_replay`，有完整前后地图、精确要素 ID 和补丁日志，
来源为 `delivered_ground_truth` / `reference_update`。它们已经写入仿真数据库，**不再只展示一条人工移动标线**。
原图为 219 条车道、27 个横道；更新后为 240 条车道、33 个横道。
一项源车道更新可对应车道面和左右标线等多个仿真要素，因此“93 项源更新”与界面的仿真要素数量不同。
本次实际对应 **170 项仿真要素修改：84 项车道、77 项标线、9 项横道**。

参考更新与模型检出分别记录。目前检测管线的 nuScenes 6 个、Argoverse 54 个结构候选，
最终动作仍为 `no_update`；局部观测曲线尚未形成完整、确定的替换操作。
例如 nuScenes 的局部差异候选同时仍能观测到原参考线，不能据此直接断言原线移动。
这不是等待批准：导出器会直接执行满足对象身份、修改操作和完整几何条件的算法结构输出。
当前的 93 项应称为“已交付参考更新进入仿真”，不能称为算法检测出的 93 项变化。

Argoverse 车道中心线由源左右边界等弧长采样推导，并记录该来源。
BUS/BIKE 用途与 `vehicle_access` 保留在仿真地图中；普通车辆规划不会把公交/自行车专用道当作普通车道。
源裁剪区域之外的连接单独记录，不凭空补路。
自车模型默认长/宽/高为 4.5/1.9/1.5 米，这是仿真车辆配置，源材料未提供实测车身尺寸。

### 状态事件与物理障碍

23 个事件关联的 74 个历史实例为 45 个路障、26 个锥桶、3 个杂物。
其中 71 个按源实例 ID 关联原有仿真轨迹，不重复生成模型；3 个杂物保留源轮廓并标明 `FOOTPRINT ONLY`。
仅看最终一帧会剩下 49 个关联实例，因此状态时间线保留完整历史观测。
普通背景行人、车辆不会自动被标为“地图变化”；后续若算法产出它们的状态事件，可通过同一附着层表达。

状态按原始 41 个关键帧与转换后的 201 帧对齐，每段源关键帧对应 5 个仿真步。
插值步使用最近已到达关键帧的状态，并显示观测时间与观测年龄；未来观测不会提前出现。
缺少新观测时保留最后位置并显示未知状态，不能把“未看到”写成“已清除”。
明确清除事件撤下其状态标注。

物理障碍的持续存在是另一项已选择的仿真策略：61 个路障、42 个锥桶在各自首次有效观测后，
固定当时的位置、朝向、尺寸并持续保留，首次出现前不提前生成。
这 103 个物理对象与 74 个状态事件关联实例的统计范围不同。
状态标注变为未知或被隐藏，不会让已保留的障碍模型消失，也不会把动态行人冻结在原地。

## 怎样看出修改前后

道路结构使用右侧固定的 **BEFORE / AFTER** 对照，两格共享坐标范围与比例。
世界坐标中的青色实线表示更新后/新增几何，橙色虚线表示原几何，红色虚线表示删除几何。
`U01`、`U02` 等编号对应实际 `map_patch_log.json` 中的仿真要素。
所有修改几何持续着色；世界标签只显示当前选中项，避免大量编号遮挡道路。
也可直接打开[开场道路的前后对照](../demo/simulation/argoverse/comparison.svg)，
或[最初网页链接中新增车道 ls-168084972 的对照](../demo/simulation/argoverse/linked-road-addition.svg)。
实际仿真截图：[道路更新](../demo/simulation/argoverse/preview.png)、[附着状态](../demo/simulation/nuscenes-states/preview.png)。
纯属性变化会显示属性前后值；只有全部对应顶点发生相同位移时，才显示统一位移数值。
这些高亮色是查看辅助，不替代道路标线的原始材质和类型。

附着状态使用左侧卡片及对象轮廓：黄色表示当前观测活动，紫色虚线表示未知/陈旧。
卡片显示选中事件、状态和观测时间；有仿真模型的对象按确切身份关联，缺模型的杂物只画源轮廓。
状态轮廓表示源观测位置，已持久化模型的位置可能来自更早的首次观测，两者含义不同。

三维窗口的操作如下；二维窗口支持 J/K、N/M 和 V，聚焦和跟车相机操作用于三维窗口。

| 按键 | 作用 |
| --- | --- |
| J / K | 上一个 / 下一个道路修改 |
| U | 聚焦选中的道路修改 |
| N / M | 上一个 / 下一个状态事件 |
| I | 聚焦选中的状态事件 |
| V | 显示 / 隐藏状态轮廓和身份标签；摘要卡显示 ON/OFF，物理对象仍存在 |
| Q | 返回跟车视角 |

选中项和相机操作不改变车辆回放。`--hide-updates` 隐藏道路修改标注；`--hide-states` 隐藏状态标注。
二者均不撤销已加载的数据。右侧显示“无道路修改”时，仍可通过左侧查看状态事件。
旧 `demo/simulation/highlighted` 保留为一米标线移动的 `DEMO`，仅用于受控接入检查。

## 重新运行地图模块并导出

以下输出目录都应为新目录：

```bash
python scripts/run_change_pipeline.py --out outputs/sim-source
python scripts/export_simulation_map.py --database-path "AIE1903 原始资料/simulation/converted/scene-0980" --pipeline-result outputs/sim-source/result.json --state-source-root "AIE1903 原始资料" --out outputs/nuscenes-states
python scripts/export_argoverse_simulation.py --pipeline-result outputs/sim-source/result.json --m2-package "AIE1903 原始资料/deliveries/0cfNzxkFwK3x3JB9HpcKIqoRCztSAreo__Winter_2021_M2_v1" --out outputs/argoverse-simulation
```

最后一条生成 `original/` 和 `updated/` 两份同场景数据库。
nuScenes 的 `--state-source-root` 提供 `processed/frames.csv` 与 `objects.csv`，导出完整可移植的 `state_source.json`。
运行生成后的数据库不再依赖这些 CSV 的本机路径。

导出目录包括场景 pickle、`planning_map.json`、`map_before.json`、`map_after.json`、
`map_patch_log.json`、状态/障碍资料和 `simulation_manifest.json`。
Argoverse 还保留 93 项源参考修改的 `reference_patch_log.json`。
场景、补丁日志、状态时间线及障碍策略用哈希绑定，运行前校验对应关系。

本次验证：全套 444 项测试，443 项通过、1 项因既有 fixture 缺失跳过。
Argoverse 原图和更新图均完整回放 581 步，两版自车轨迹一致；从实际引擎读回
170 项修改，几何、连接关系、用途和通行权限均与导出一致。
nuScenes 已运行二维和三维状态视图；第 137 步显示 16 个已出现事件、61 个实例，
其中 58 个绑定已有物体、3 个只有源轮廓。N/M/I/V 的引擎事件测试确认不会改变车辆位置、时间步或回放策略。
完整 200 步回放显示全部 23 个事件、74 个历史实例，71 个关联已有物体；103 个持久障碍全部保留且无预期对象缺失。

完整车道/拓扑及外部显式修改也可通过 nuScenes 导出器的 `--patch <JSON>` 输入。
补丁绑定场景身份、坐标和原场景哈希；替换/删除绑定旧要素哈希。
车道编辑须提供中心线、道路多边形和出入口关系；单独移动一条标线不会推断整条道路的新拓扑。
`--obstacle-mode recorded` 可导出原始障碍轨迹有效性，默认 `persistent` 保留首次观测后的静态障碍。

## 接入自己的 Planning

流程是“地图模块 → 新道路图及附着状态 → 规划 → 控制 → 仿真反馈”。
默认回放只用于检查环境和可视化；自主规划需要显式使用 `--mode controller`。
接口为 `controller(state) -> [steering, throttle_or_brake]`，两项均在 `[-1, 1]`。

`state` 提供更新后的 `map_features_runtime`、`map_state_snapshot`、
`persistent_obstacles_runtime`、车辆位置/航向/速度，以及目标和坐标原点。
道路与状态分开传入；规划器可结合二者做决策，不必把障碍写成永久车道结构。
`--goal X Y` 使用对应场景的原始城市 XY 米制坐标。

例如，使用自己的 `your_planner.py` 中的 `control` 函数：

```bash
python scripts/run_updated_simulation.py --database-path demo/simulation/nuscenes-states --mode controller --controller your_planner:control --goal 420 1210 --render 3D --report outputs/my-driving-run.json
```

项目自带 `map_update.simulation_planner:controller`，作为有向车道图搜索与低速跟踪示例，
读取更新后的通行属性，尚不负责动态避障或信号灯决策。
`--no-traffic` 关闭移动交通与信号灯，持久障碍仍存在；报告保留该配置及实际轨迹、动作和碰撞结果。
有记录交通时运行受记录时窗限制；超出源记录的状态不会冒充新的观测。

需要键盘驾驶时才使用 `--mode manual`，W/S 加速与制动，A/D 转向。
回放不会因为启用地图或状态可视化而切换成手动驾驶。

## 道路之外的真实环境

MetaDrive 默认山体来自其通用地形资源，并非这些场景的测量结果。
当前只画有来源的道路、人行道、横道等几何；缺失的建筑和地形保持未提供。
源 XY 道路的显示高度是渲染约定，不能当作实测高程；nuScenes 中心线第三列为航向角。

`--environment-geometry <JSON>` 可加载坐标匹配、有来源的建筑轮廓与明确高度，或 XYZ 地形面。
这部分当前用于绘制，不生成建筑/地形碰撞体。格式见 `map_update/simulation_environment.py`。
要恢复真实街景，仍需同场景点云、建筑模型或其他实际环境资料。

## 安装

已有 `aie1903-sim` 环境不必重新创建。缺少新增绘制依赖时，在激活的环境中执行：

```bash
python -m pip install "numpy==2.2.6" "shapely>=2.1,<3"
```

首次安装使用 Python 3.10 或 3.11；ScenarioNet 当前源码不支持 Python 3.12 及以上：

```bash
conda create -n aie1903-sim python=3.10 -y
conda activate aie1903-sim
python -m pip install -r requirements-simulation.txt
python -m metadrive.pull_asset
```

最后一条获取 MetaDrive 官方模型资源。可用
`python -c "import sys, metadrive; print(sys.executable); print(metadrive.__file__)"` 核对当前解释器和安装位置。

## 云端截图与检查

有 EGL 的 Linux 环境可离屏保存实际三维画面，截图和报告均使用新路径：

```bash
python scripts/run_updated_simulation.py --database-path demo/simulation/nuscenes-states --mode replay --render 3D --offscreen --steps 137 --snapshot outputs/states-frame137.png --report outputs/states-frame137.json
python -m unittest discover -s tests -p 'test_simulation*.py' -v
```

`--offscreen` 使用兼容软件渲染的抗锯齿配置，不支持键盘手动驾驶。
报告包含实际载入地图、补丁高亮、当前状态快照、状态时间线摘要和轨迹，便于核对画面与数据。
