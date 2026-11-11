# 随仓库提供的仿真场景

本目录包含可直接使用的场景数据库、前后地图、变化日志和来源清单。已有仿真环境时，在项目根目录启动统一平台：

```bash
conda activate aie1903-sim
python scripts/run_simulation_platform.py
```

浏览器打开 **http://127.0.0.1:8091/**。选择场景后，同一界面显示道路结构与附着状态，提供原图/更新图比较、自由视角、时间轴、真实 MetaDrive 画面及 Planning 数据导出。操作说明见[场景平台文档](../../docs/simulation_platform.md)。

| 目录 | 用途 | 道路结构 | 附着状态 |
| --- | --- | --- | --- |
| `nuscenes-states/` | nuScenes scene-0980 真实输出 | 当前算法执行 0 项结构修改 | 23 个事件，全程 74 个关联实例 |
| `argoverse/original/` | 同场景 Argoverse 原道路图 | 219 条车道、27 个横道 | 无可用三维状态轨迹 |
| `argoverse/updated/` | Argoverse 已交付参考更新 | 93 项源更新对应 170 项仿真要素修改；240 条车道、33 个横道 | 无可用三维状态轨迹 |
| `nuscenes-combined/` | 同场景两层联调 DEMO | 一条标线沿城市 X 平移 1 米，明确标为受控修改 | 同一 nuScenes 场景的完整真实状态 |
| `validation/` | 冻结的算法评估摘要与来源 | 参考更新与模型检出分开计量 | 真值不足的范围明确标为不可评估 |
| `highlighted/` | 历史受控接入示例 | 一条标线平移 1 米，DEMO | 旧状态快照 |
| `persistent/` | 历史持久障碍示例 | 0 项结构修改 | 旧状态快照；新平台使用 `nuscenes-states/` |

nuScenes 与 Argoverse 属于不同场景与坐标范围，分别载入。平台的两层组合是“同一场景的道路 + 同一时刻的状态”；联调 DEMO 不计作真实结构检出。

nuScenes 的 103 个物理路障/锥桶在各自第一次有效观测后持续保留；74 个状态事件关联实例是另一统计范围。动态车辆、行人、自行车继续沿原轨迹运动，不因状态层可视化而冻结。缺少仿真模型的 3 个杂物保留源轮廓。

原有独立窗口仍可使用：

```bash
python scripts/run_updated_simulation.py --database-path demo/simulation/nuscenes-states --mode replay --render 3D
python scripts/run_updated_simulation.py --database-path demo/simulation/argoverse/updated --mode replay --render 3D
```

两条命令分别运行不同场景，关闭前一个窗口后再运行另一条。Mac 本地图形窗口不加 `--offscreen`。安装、重新导出与控制器接入见[仿真桥接说明](../../docs/simulation_bridge.md)。

每份数据库使用相对索引，运行不依赖开发环境的 `outputs/` 路径。来源清单中的绝对路径仅记录导出来源；场景与状态使用随仓库提供的文件。pickle 数据库应来自可信来源。

地图没有真实建筑高度或周边地形数据，因此缺失区域留空。持久障碍是显式仿真策略；参考更新与 DEMO 的正确载入均不代表算法检测准确率。
