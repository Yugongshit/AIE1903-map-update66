# 地图更新在前，仿真与 Planning 在后

MetaDrive 是开源驾驶仿真软件库，可通过 Python 运行三维渲染、车辆物理和控制。
ScenarioNet 是场景转换与管理工具，`converted/` 是转换后的场景数据库目录。

## 已安装过依赖：从 GitHub 同步后直接启动

在本机项目根目录执行（例如 Mac 的 `~/Desktop/AIE1903-map-update66`）：

```bash
git pull --ff-only
conda activate aie1903-sim
python -m pip install "numpy==2.2.6" "shapely>=2.1,<3"
python scripts/run_updated_simulation.py --database-path demo/simulation/highlighted --mode manual --render 3D --no-traffic --steps 6000
```

无需重新创建已有环境或另下压缩包。两份完整数据库随 Git 提交：
`demo/simulation/highlighted` 为带 DEMO 标记的受控改图，
`demo/simulation/persistent` 为真实算法输出（0 项结构修改），两者均保留持久障碍。
激活后 `python -c "import sys, metadrive; print(sys.executable); print(metadrive.__file__)"`
可以核对使用的环境。新绘制需要 Shapely 2.1+；示例 pickle 使用 NumPy 2.2.6 导出，
依赖版本与验证环境保持一致。若提示模型资源缺失，运行 `python -m metadrive.pull_asset`。

Mac 本机使用图形窗口，不加 Linux 离屏截图用的 `--offscreen`。点击窗口后
W 加速、S 制动、A/D 转向、Esc 退出；Q 为跟车视角，B 为俯视相机。
俯视相机模式的 WASD 移动相机，按 Q 返回驾驶。`--no-traffic` 关闭移动交通，
保留已经出现的路障/锥桶。当前云终端的 `.venv-sim` 和 `/workspace` 路径不适用于本机。

## 处理流程

新的接入顺序是：

```text
观测 / 已确认的地图编辑
        ↓
地图更新模块：结构补丁 + 有时间和来源的状态事件
        ↓
export_simulation_map.py → 新的 ScenarioNet 场景数据库
        ↓
MetaDrive 加载更新后的道路和车道图
        ↓
手动驾驶 / 使用新目标的规划控制器 → 物理仿真 → 实际轨迹日志
```

原始 `converted/scene-0980` 保留为对照。导出不会覆盖输入、不会把已有
轨迹当作新的规划结果。原 M4 交付保留为离线静态基线；新的接口让规划器
读取仿真实际加载的地图，而不是原交付中固定的 `inputs/base_map.json`。

## 为什么原来的场景旁边是山

MetaDrive 默认地形从自己的 `assets/textures/terrain/heightfield.png` 加载，默认
`height_scale=50`。这是引擎背景，不是 nuScenes 实测城市地形。
按用户要求撤销“把整个背景换成平地”的方案，默认关闭通用地形，
只绘制源地图的道路、人行道和横道多边形。转换器遗漏的连接区道路面从同城
原地图按确切 token 补齐，并记录文件哈希；修改过的要素不会用旧面覆盖。
缺少数据的周边留空，不能把空白解读成真实地面没有建筑。
当前 converter 读取道路、车道、标线、横道、路缘和交通参与者，没有重建
建筑立面、高度或纹理；平面地图能构成道路三维仿真，但不能凭空恢复真实楼宇。
建筑需要另行提供有坐标对齐依据的轮廓、高度、点云或模型。
仓库原始地图没有建筑/地形层；nuScenes 车道中心线的第三列是航向角，不是
高程。已加入 `--environment-geometry <JSON>` 接口，可加载有来源、同城同场景的
建筑轮廓与明确高度，或带 XYZ 高程的地形面。此扩展目前仅用于绘制，不生成
建筑/地形碰撞体；无数据时不编造建筑。格式见 `simulation_environment.py`。

## 安装

在有图形桌面的电脑上使用 Python 3.10 或 3.11。ScenarioNet 当前源码拒绝
Python 3.12 及以上。在仓库根目录执行：

```bash
conda create -n aie1903-sim python=3.10 -y
conda activate aie1903-sim
python -m pip install -r requirements-simulation.txt
python -m metadrive.pull_asset
```

最后一条从 MetaDrive 官方发布获取匹配版本的模型资源。当前云环境验证用
`.venv-sim/bin/python`，桌面图形窗口和无窗口物理验证分别记录，不相互代替。

## 先执行地图模块，再导出仿真地图

每次选择新的输出目录：

```bash
python scripts/run_change_pipeline.py --out outputs/sim-source
python scripts/export_simulation_map.py --database-path "AIE1903 原始资料/simulation/converted/scene-0980" --pipeline-result outputs/sim-source/result.json --out outputs/sim-updated
```

本次真实 nuScenes 结果为 23 个状态事件和 0 项结构修改。因此这条命令会
保留道路几何，携带地图模块的状态与来源；不能为了演示而假称已检测到改路。
默认 `--obstacle-mode persistent`：路障和锥桶第一次有效出现后，位置、朝向和
尺寸固定在该次观测值，之后持续存在。首次出现前不提前生成，沿用原实例 ID，
避免同一物体重复创建。普通车辆、行人和自行车仍保留原动态轨迹。
这是用户选择的仿真场景构建策略；原始观测与地图状态事件不改写。
用 `--obstacle-mode recorded` 可导出原始轨迹有效性供对照。

`obstacle_manifest.json` 记录每个对象的首次出现帧、位置、尺寸和来源；运行器
跳过这些明确对象原先的20帧过滤，设置静态碰撞体，并按源尺寸缩放通用模型。
车辆驶离视野、后续没有检测或录制窗口结束，都不会自动删除已保留的障碍。

已确认且满足完整写入合同的标线几何/属性修改、新增和删除，可直接从 pipeline
结果自动适配。导出器重新检查事件的最终动作、证据门控、原几何和确切目标。
完整车道/拓扑编辑及外部编辑通过 `--patch <JSON>` 输入。补丁绑定 dataset、scene_id、map、米制
坐标、原场景 SHA-256；替换/删除还绑定旧要素哈希。只按确切 feature ID
修改；不靠最近距离猜目标。车道编辑需完整的中心线、道路多边形和出入口关系，
引用变更在一个补丁中一起提交。仅移动画出的标线不会自动推导新的车道中心线。

Argoverse 的参考变化不能套用到 nuScenes scene-0980；需要其自身转换后的
场景和坐标/身份适配。本次桥接以仓库已有的 nuScenes 场景为验证对象。

## 在更新后的环境里驾驶

```bash
python scripts/run_updated_simulation.py --database-path outputs/sim-updated --mode manual --render 3D
```

W/A/S/D 控制车辆。二维视图使用 `--render 2D`。此模式不使用
`ReplayEgoCarPolicy`，也不再因为偏离记录轨迹就按旧终点结束。
它仍使用 MetaDrive 的内部导航组件维持引擎兼容，但旧路线不作为新规划目标。

仅作原始轨迹对照时用 `--mode replay`。回放结果不能当作新规划成果。

## 自己指定目标并接规划器

`--goal X Y` 使用原场景城市 XY（米），不是经纬度或屏幕坐标。运行时
MetaDrive 会平移坐标原点；控制器状态同时提供原坐标、平移量和实际加载的地图。

规划器接口为 `controller(state) -> [steering, throttle_or_brake]`，两项范围
均为 `[-1, 1]`。例如：

```bash
python scripts/run_updated_simulation.py --database-path outputs/sim-updated --mode controller --controller your_planner:control --goal 420 1210 --render 3D --report outputs/my-driving-run.json
```

其中 `your_planner.py` 是你自己的 Python 模块，暴露 `control(state)` 函数。
`state` 包含车辆实时位置/航向/速度、更新后的 `map_features_runtime`、
`goal_scenario_m` 和 `runtime_origin_in_scenario_m`；以代码中
`controller_state` 为完整接口定义。不要修改传入的共享地图对象。
`persistent_obstacles_runtime` 提供当前已经出现的持久障碍位姿与尺寸，供规划器使用。

仓库提供一个基于车道后继图搜索和低速路径跟踪的研究基线。以下命令在导出
地图上选择城市坐标 `(430, 1235)` 为目标，关闭背景交通以单独检验控制器：

```bash
python scripts/run_updated_simulation.py --database-path outputs/sim-updated --mode controller --controller map_update.simulation_planner:controller --goal 430 1235 --goal-radius 0.5 --no-traffic --render 3D --report outputs/planned-run.json
```

`--no-traffic` 关闭移动交通与信号灯，仍保留持久化地图障碍。
移除此参数会加载原记录中的动态交通参与者；此基线本身不避让它们。
它用于检查
“更新地图 → 新目标 → 控制动作 → 仿真反馈”这条链路，不能声称完成了动态
避障、交通规则规划或实车可执行性验证。

报告记录实际行驶轨迹、动作、碰撞/到达结果与载入地图信息。
为了避免把缺失的后续交通当成空路，有背景交通时运行受原记录时窗限制。
`--no-traffic` 只用于隔离控制实验，必须在结果中明确标识。

## 更新部分怎样标出来

默认显示实际执行的 `map_patch_log.json`，编号 `U01`、`U02` 等与报告中的
要素 ID 一一对应：青色实线为更新后/新增几何，橙色虚线为更新前几何，
红色虚线为删除几何。三维图有位置标签和屏幕图例，二维图也显示同样的标识。
`--hide-updates` 仅隐藏注释，不撤销地图修改。
受控实验显示 `DEMO U01` 与 DEMO 图例；零结构修改显示 `NO MAP CHANGES APPLIED`。
23个状态事件不能全部冒充底图修改。注释不参与碰撞或规划。
更新日志的哈希绑定到导出清单和场景文件；加载时校验，避免用旧注释标记新地图。
旧版导出缺少此绑定时需重新运行导出命令。

## 显式受控改图示例

此例把自车附近一条标线沿城市 X 平移 1 米，只检验数据导出和引擎加载。
输出标记 `synthetic=true`，不是从观测算法得到的结构变化，也不用于检测指标。

```bash
python scripts/build_simulation_demo_patch.py --scenario "AIE1903 原始资料/simulation/converted/scene-0980/scene-0980_0/sd_nuscenes_scene0980_v1.0-trainval_scene-0980.pkl" --out outputs/sim-demo-patch.json
python scripts/export_simulation_map.py --database-path "AIE1903 原始资料/simulation/converted/scene-0980" --pipeline-result outputs/sim-source/result.json --patch outputs/sim-demo-patch.json --out outputs/sim-demo
python scripts/run_updated_simulation.py --database-path outputs/sim-demo --mode manual --render 3D
```

ScenarioNet 的场景文件为 pickle；只加载可信来源的本地数据库。导出器保留
原数据并生成独立数据库、规划地图和审计信息，拒绝覆盖已有输出目录。

## 云端离屏截图

有 EGL 软件渲染支持的 Linux 环境可用以下入口保存实际仿真画面：

```bash
python scripts/run_updated_simulation.py --database-path outputs/sim-demo --mode controller --action 0 0 --render 3D --offscreen --steps 4 --snapshot outputs/sim-3d.png --report outputs/sim-3d.json
```

离屏模式将 MetaDrive 默认硬编码的 16 倍 MSAA 关闭，以兼容软件帧缓冲；
只改变抗锯齿设置，不改变道路几何。兼容适配在引擎关闭后恢复，不修改上游源码。
`--offscreen` 不支持键盘手动驾驶；桌面交互启动不加这个参数。

## 最新实际验证

- `.venv-sim/bin/python -m unittest discover -s tests -v`：378 项，377 通过，
  1 项因原有历史场景 fixture 缺失跳过。普通 Python 的仿真测试也通过；
  没有安装可选 Shapely 时明确跳过 8 项几何测试。
- 真实 pipeline 输出可导出为 702 个地图要素、23 个状态事件；46 个实例
  匹配到原轨迹，3 个杂物实例未在转换数据中找到，审计中保留缺口。
- 明确标记 synthetic 的标线 +1 米修改已从实际引擎 `map_data` 读回核对，
  原始场景文件哈希不变；不是只改网页画线。
- 61 个路障和 42 个锥桶固定在首次观测位姿；实际运行 205 步，超过原 201 帧，
  已出现对象数量只增不减，103 个对象最终全部保留，位置不漂移，无预期对象缺失。
- 三维绘制 508 个源地图多边形，其中 194 个连接区道路面来自原地图精确匹配。
  未生成山、草地或虚构建筑。二维和 EGL 三维入口均实际运行通过。
- 更新标注显示受控修改的青色新位置、橙色虚线旧位置及 `DEMO U01` 标签。
- 此前无持久障碍的规划接口验证中，新目标 `(430,1235)`、到达半径 0.5 米，
  96 步后距离目标 0.4163 米。此历史结果不能证明当前规划基线会避让持久障碍。

开发验证结果在 `outputs/simulation-bridge/`（该目录不进源码 Git）：
`source-map-highlighted-final.png` 为当前三维截图，配套 JSON 保存加载审计；
`persistent-final-205.json` 保存真实场景的 205 步障碍验证，
`source-map-2d-final.json` 保存二维验证。
`persistent-final/` 是真实零结构更新、带持久障碍的数据库；`highlighted-final/`
是带 `DEMO` 标识的受控修改数据库，不代表真实检测成果。

可运行的两份数据库已完整复制到 Git 跟踪的 `demo/simulation/`，从仓库根目录打开：

```bash
python scripts/run_updated_simulation.py --database-path demo/simulation/highlighted --mode manual --render 3D --no-traffic --steps 6000
```

当前云会话未提供远程桌面能力，且 `DISPLAY` 未设置；请使用上面的离屏截图
命令查看三维画面。手动模式需要实际图形窗口，本地使用已激活仿真环境的 `python`。

要查看真实算法结果，将数据库路径换为 `demo/simulation/persistent`。
这里使用新的运行入口；直接执行上游 `scenarionet.sim` 不会加载这些显示和持久化适配。
