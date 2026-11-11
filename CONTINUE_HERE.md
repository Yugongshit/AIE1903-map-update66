# 从这里继续（2026-11-09）

**2026-11-10 仿真接入调整：地图更新在前，Planning 在后。** 新增
`map_update/simulation_bridge.py` 导出更新后的 ScenarioNet 地图，
`scripts/run_updated_simulation.py` 提供独立自车控制、新目标和实际轨迹记录，
`map_update/simulation_planner.py` 提供读取更新车道图的低速规划示例。
用户最新修正：撤销整片平地背景，按源地图几何显示、未知周边留空；
真实楼宇数据未提供，不编造建筑。默认路障/锥桶首次出现后持久保留，更新区域
标青色、原位置橙色虚线、删除红色虚线；受控实验标 DEMO。
原 M4 离线交付保存，不等同于新闭环规划。
首版针对已有 nuScenes scene-0980 转换数据；Argoverse 仍需要自己的场景适配。
真实结果仍为 23 状态事件、0 结构修改，受控改图示例必须保留 synthetic 标记。
安装、命令和限制见 [仿真接入说明](docs/simulation_bridge.md)。
代码和两份完整导出数据库已纳入 Git；本地使用 `demo/simulation/highlighted`
或 `demo/simulation/persistent`，不依赖云端 `outputs` 或手动下载压缩包。
已有本地环境只需激活 `aie1903-sim`，对齐 NumPy 2.2.6 / Shapely 2.1+ 后运行新入口。
查看可视化默认 `replay`，按原始轨迹自动行驶并保留背景交通；不要把展示命令
改成 `manual` 或附带 `--no-traffic`。手动驾驶和新路线规划仅作为显式选项保留。
地图修改展示使用同范围、同尺度的固定 BEFORE / AFTER 局部对照，明确摘要、DEMO和零修改。
U 聚焦修改位置、Q 回跟车、J / K 切换选中修改；只改变查看方式，不改变自动回放。
当前 DEMO 仅为起点附近一条标线沿城市 X 平移 1 米；真实结构修改仍为 0。

**最新展示：三维地图。** 先读 [三维视图说明](docs/3d_map_view_20261109.md)。默认 3D，`?view=2d` 可固定二维。真实 XY 几何保持；最新展示精确关联 49 个源实例，35 路障和 11 锥桶按标注 L/W/H/yaw 显示通用造型，3 杂物保留轮廓，不能声称重建了真实表面；分类算法和冻结结果没有改动。最新构建在 `outputs/pipeline-v8/semantic-objects-20261109/`，保留中英双语与原图／更新后滑动对照。

当前代码以 **main** 为统一入口，已合并应用开发历史与原 main 的 Planning 44 个文件；网站在 `gh-pages`。不要用旧 `work`、旧演示分支覆盖当前源代码。

先读 [本次算法及结果](docs/material_structural_decisions_20261109.md)。用户黄子明要求：小幅曲线偏差不算结构更新；新增车道等实际结构变化要保留；所有判断仅使用本次交付，不安排复访或人工待办。当前讨论按“底图结构变更／地图状态事件”区分，不能恢复“持续时间决定图层”。

当前流程：已交付感知观测 → 局部可靠性诊断 → 语义图层 → 同一物理对象与修改操作 → 实质变化检查 → 本次最终动作。`final_structural_decision` 为当前动作；旧 `model_change_decision` 只是局部残差诊断。`no_update` 是已完成的不改图动作，不能用于冒充真实负标签或报告提高准确率。

nuScenes：29 个候选＝23 个状态事件＋6 个结构候选。23 个状态保持；6 个结构候选关闭为不更新，0 个待定。旧诊断的 1 个检出＋5 个不足仅在证据详情中保留。LD004 是其他曲线与附近参考线的距离，不是已证明的同一标线位移；同窗口原参考线仍被观测。旧“最终不足率≤5%”不是当前动作合同，不得以关闭记录冒充分类准确率。

Argoverse：完整 98 项交付参考变化保持在 `reference_replay`；新的 `material_reference_replay` 接受 93 项，关闭 5 项小幅纯几何差异。27 个新增对象全部保留（21 条车道＋6 个横道）。前后地图、补丁、回滚分别导出。参考真值只在模型预测冻结后读取，不能喂入模型预测或当作模型成绩；模型分支的最终动作与原始 14 个局部检出独立记录。

共同工程策略：同一对象完整几何双向采样，间距 0.5 m；偏移超过 0.5 m 且连续影响至少 3 m 才构成几何实质变化。新增、删除、标线属性和拓扑有各自的明确事实要求。这些是可配置工程参数，不是由正常误差标定得到的概率，也不是按数据集名称决定。

代码入口：

- `map_update/structural_decision.py`：实质变化和最终动作。
- `map_update/pipeline_v2.py`：观测、诊断、动作、冻结及事后评价集成。
- `map_update/reference_replay.py`：完整及实质参考回放、精确回滚。
- `map_update/pipeline_studio.*`：双语可交互地图；向右全原图、向左全更新后。
- `scripts/run_change_pipeline.py`、`scripts/build_pipeline_studio.py`：复现入口。
- `AIE1903 原始资料/Planning_M4_scene0980_handoff/`：潘铭洋原始规划交付；不要把合并代码说成已接入闭环规划。

结果目录：`outputs/pipeline-v6/material-structure-20261109/`；网站：`outputs/pipeline-v6/material-release-20261109/`。运行目录不入源代码 Git；可用 README 命令重新生成。验证记录与阈值说明在上述方法文档。

历史提案、下载包、讲稿和旧脚本见 [归档](archives/session-20261108/README.md)。最近 proposal 的文稿不能自动当作成员已经完成对应代码的证据；成员工作应以实际源代码和保存输出核验。
