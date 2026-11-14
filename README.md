# 道路地图结构变更与状态事件

当前版本使用 **main** 分支，包含地图处理应用、成员原始交付及 Planning 代码。网页发布在 `gh-pages`。

**公网仿真工作台：** [直接打开地图审阅](https://yugongshit.github.io/AIE1903-map-update66/simulation/)，同伴无需安装依赖。五席位玩家／AI／外部 Planning 的实时交通通过独立 MetaDrive 服务器运行。[公网与服务器部署说明](docs/public_simulation.md)。

**地图更新的下一步：** [贡献与实验方案](docs/map_update_contribution_plan.md) 区分参考更新、自主检测和实际写回，提出局部补丁、状态通行约束及固定 Planning 对照实验。该方案尚未实现新算法，实时接口也不代表同学的 Planning 已接入。

**地图更新 → 场景验证 → Planning：** 新增本地场景平台。选中场景后，在同一视图中查看道路结构与附着状态，切换原图/更新图、选择改动、旋转到车后、拖动时间轴，并导出当前帧的规划输入。[平台操作与 Planning 接口](docs/simulation_platform.md)。

**已有 `aie1903-sim` 环境，直接启动：** 在你的 Mac 上执行，无需重建环境或另下地图压缩包。

```bash
cd ~/Desktop/AIE1903-map-update66
git pull --ff-only
conda activate aie1903-sim
python scripts/run_simulation_platform.py
```

浏览器自动打开 **http://127.0.0.1:8091/**。默认“地图审阅”显示源数据三维几何；点击“启动 MetaDrive”进入真实引擎画面，两种模式均提供视角与回放控制。原图/更新图使用同一场景的状态层，状态对象不会写入纯道路底图。

场景卡片明确标注数据来源：**nuScenes** 有 23 个状态事件、全程 74 个关联实例，当前算法结构修改为 0；**Argoverse** 有 93 项已交付参考更新，对应 170 项仿真要素修改，缺少可用三维状态轨迹。两层联调 DEMO 将一项受控道路修改与同一个 nuScenes 场景的真实状态组合，单独标明来源，不计作算法检出。

“验证”页分别展示地图写入检查与算法评估。目前 Argoverse 局部边界检出的精确率为 **64.3%**、召回率为 **29.0%**，14 个检出中只有 3 个局部偏移误差在 0.5 米以内；参考更新成功载入不能当作检测准确率。nuScenes 暂无足够的独立变化真值计算该指标。

原有独立仿真窗口仍可运行；首次安装和导出说明见[仿真桥接文档](docs/simulation_bridge.md)。

**此前静态网页的三维地图：** 默认以街道尺度显示可旋转、缩放的三维视图，保留黄色状态、红色结构变化和共享相机的滑动对照；支持俯视、手机手势及一键切回二维。道路采用原始 XY 几何；该最终快照的 35 个路障和 11 个锥桶按标注尺寸与朝向显示通用造型，3 个杂物保留真实轮廓。可直接点击“看路障／看锥桶”放大查看。[使用与实现说明](docs/3d_map_view_20261109.md)。

**2026-11-09：按实质结构变化决定是否改图。** 局部曲线距离不再直接等同于标线移动。先核对物理对象和修改操作，再检查几何变化的幅度与连续影响范围；新增车道、连通关系、车道用途和受约束的标线类型变化分别处理。只使用本次提供的材料，所有候选给出已完成的动作，不创建复访或人工待办。`no_update` 表示本次不改底图，不是独立真值标签。

- [当前算法与实际结果](docs/material_structural_decisions_20261109.md)
- [中文地图对照](https://yugongshit.github.io/AIE1903-map-update66/algorithms/zh/index.html) · [English map comparison](https://yugongshit.github.io/AIE1903-map-update66/algorithms/en/index.html)
- [新对话接续](CONTINUE_HERE.md) · [历史资料归档](archives/session-20261108/README.md)

nuScenes 的 6 个标线候选本次均不执行结构更新，23 个黄色状态事件保留。Argoverse 已交付参考变化中，93 项满足实质变化标准，5 项细微几何差异不用于主图更新；完整 98 项原始参考回放仍可下载。参考回放与算法预测分别保存，不把已有真值回放说成模型准确率。

```bash
python scripts/run_change_pipeline.py --out outputs/material-run
python scripts/build_pipeline_studio.py --result outputs/material-run/result.json --out outputs/material-site
python -m unittest discover -s tests -v
python scripts/check_pipeline_studio.py outputs/material-site
python scripts/check_composite_map.py outputs/material-site
```

运行目录须为新的目录；打开构建结果中的 `zh/index.html` 或 `en/index.html`。依赖和旧工具的用法见下面保留的历史说明。

成员原始资料在 `AIE1903 原始资料/`；Planning 在其中的 `Planning_M4_scene0980_handoff/`。集成算法在 `map_update/`，运行、构建与浏览器检查在 `scripts/`。这些原型使用已交付观测，不能描述为重新训练了感知网络或已经完成实车闭环规划。

**持续交通与在线地图：** 工作台右侧“交通实验”支持 nuScenes 1–12 辆自动背景车、快慢配置、停车事件及每车观测产生的临时占道状态；固定人工驾驶席位已隐藏。状态可通过版本化接口交给 Planning，真实同学算法尚未接入。[操作与阶段边界](docs/synthetic_traffic.md) · [交通模型与研究路线](docs/traffic_model_research.md)。

## 历史版本说明

以下保留各阶段的运行说明。旧日期、旧测试数量和旧交付状态以接续说明中的当前记录为准。历史 Proposal、PPT、讲稿、生成脚本和原始附件已一并归档，旧工作区快照不会覆盖当前源代码。

**当前是可运行的研究原型。** 已阅读五份 proposal，并将拆分上传的实际交付整理至 `external/perception_delivery/`。后续从 GitHub 提交 `06c2cff` 同步了 `AIE1903 原始资料/`，现已接入原始 nuScenes 地图、绝对时间和逐帧几何。`examples/` 全为受控合成数据，与 scene-0980 实际交付分开，不能用于报告真实检测精度或证明永久性。

最新资料核查：提交 `ba59989` 已补入梅子衡的 M3 可靠性模块。本次 13 个输出文件复现后与交付字节一致，真实场景接入已经完成：7 个复核对象均证据不足、23 个占用候选保存到事件层、基础地图更新 0。Planning 不阻塞地图维护开发。架构、成员补交清单和教授问答见 [M3 接入与答辩说明](docs/m3_integration_and_architecture.md)。

小组联调清单、已复现的运行问题及演示讲解见 [交付与运行检查](docs/team_handoff_and_runtime_review.md)。双模式演示可以用 `python3 scripts/build_project_demo.py --out outputs/project-demo-new` 生成；打开其中的 `演示.html`，分别查看真实场景，以及明确标注的模拟复访、地图更新和回退。

## 在线复核工作台（2026-11-04）

最新入口已升级为 **场景与地图维护工作室**：前视几何重建、真实行驶记录、原始感知视频、事件层对照、受控标线写入 / 版本 / 回退，以及可下载 Word 的周末问题清单。真实基础图修改仍为 0，规划尚未接入；照片素材受读取限制，不能将重建图称为实景照片。详见 [新版展示与真实边界](docs/visual_studio.md)。原证据工作台保留在 `/review/index.html`。

直接打开 https://yugongshit.github.io/AIE1903-map-update66/ 。可自行筛选对象、拖时间轴、缩放地图、同步对照、量距，并记录和导出个人复核草稿。无需下载文件或安装软件。详见 [工作台操作与实现](docs/workbench.md)。

**当前仅运行 nuScenes / scene-0980，Argoverse 场景待审核。** 输入、输出、评估来源与人工复核记录分别标识，不合并结果。说明见 [数据集分离](docs/datasets.md)。

本地构建：`python3 scripts/run_dataset.py --dataset nuscenes`。输出位于 `outputs/nuscenes/scene-0980/<运行名>/`；Argoverse 入口暂时拒绝运行。

## Evaluation and Evidence 可视化

```bash
python3 scripts/build_evidence_demo.py --out outputs/evidence-demo-new
```

打开输出目录中的 `index.html`：真实场景证据、三种方法评估对比、完整证据链三个页面。支持逐帧播放、候选联动、图层切换、评估分组、自动演示和按当前页面导出。页面无需第三方前端依赖或在线地图。详见 [可视化实现与评价口径](docs/evidence_dashboard.md)。

## 最新：M3 可靠性与地图维护联调

```bash
python3 -m map_update integrate-m3 \
  --raw-root 'AIE1903 原始资料' \
  --handoff 'AIE1903 原始资料/M3_scene0980_proposal_supplement 2/results/real_scene_handoff.json' \
  --out outputs/m3-integrated-new
```

打开输出目录中的 `index.html`。新增 `event_layer.json`、`reliability.json` 和 `maintenance.json`，保留参考地图及真实逐帧观测；T004 后段局部匹配不会覆盖 LD003 前段。事件层是保存策略，不证明障碍短期消失，也不推断封路。未知覆盖率和对齐状态保持未知。

2026-11-08 自动处理另输出 `automatic_decisions.json`、`recorded_event_layer.json` 和 `observed_difference_layer.json`。旧文件保留来源契约；新文件表达本次处理完成及实际保存几何，不添加人工任务。

当前适配器严格对应该次“覆盖率未知”的 M3 交付。后续有效覆盖率需连同来源重新评估并扩展接入契约，不能手改 null 成 1 来绕过检查。真实长期写回仍需要覆盖/对齐、独立复访、目标修改几何与地图历史语义；准确性评价还需要独立真值。

复跑 M3 需要 NumPy / Matplotlib，脚本只读取五个真实 CSV、地图和已有候选分数，不重跑相机推理，也不修改原资料：

```bash
python3 scripts/verify_m3.py \
  --raw-root 'AIE1903 原始资料' \
  --m3-root 'AIE1903 原始资料/M3_scene0980_proposal_supplement 2' \
  --out outputs/m3-reproduced-new
```

## 原始场景模式（不加载 M3）

```bash
python3 -m map_update inspect-scene \
  --raw-root 'AIE1903 原始资料' --out outputs/scene0980-map-new
```

打开生成目录里的 `index.html`：157 个局部地图要素、41 帧时间线、57 条有序观测标线和 311 条逐帧占用记录。可显示/隐藏地图、感知标线、物体定向轮廓、车辆和自车，查看候选证据，导出场景记录。

此命令输出 `reference_map.json`、`replay.json`、`candidates.json`、`scene.json`、`summary.json` 和验证报告。参考地图保留来源版本与内容哈希，**未伪装成经过核验的历史地图**。23 个 OCC 已恢复关联车道/连接区 ID；6 个 LD 的确切地图修改目标仍未知。此模式不加载 M3，实际基础地图写回为 0；需要联合结果请使用上面的 `integrate-m3`。

详见 [原始数据对接结果](docs/raw_scene_integration.md)。

## 先看真实交付

```bash
python3 -m map_update inspect-delivery \
  --root external/perception_delivery --out outputs/scene0980-new
```

这是保留的**仅交付结果模式**：不读取后续上传的原始地图。生成 `index.html`、`intake.json` 和检查报告，29 个候选全部保留，几何按原始 MultiPoint 绘制，不从中心点伪造轮廓。要查看完整地图和逐帧几何，请用上面的 `inspect-scene`。

目录恢复、真实字段与缺失文件清单见 [交付整理说明](docs/delivery_findings.md)。

## 直接运行

Python 3.10+，核心程序只用标准库，无需下载模型、数据库或第三方 Python 依赖。在仓库根目录执行：

```bash
python3 -m map_update demo --out outputs/demo-new
python3 -m unittest discover -s tests -v
```

打开生成的 `outputs/demo-new/index.html`：原始地图 / 当前地图 / 观测与候选 / 临时事件四层对比，证据时间线、事件详情、缩放平移及当前版本 JSON 导出。HTML 自包含，无需在线地图或 CDN。浏览器禁用本地文件时，可用 `python3 -m http.server 8000 --directory outputs/demo-new --bind 127.0.0.1` 提供本地静态文件。

程序拒绝覆盖已存在的输出目录；重复运行时换一个 `--out` 路径。

## 使用自己的输入

```bash
python3 -m map_update run \
  --map examples/base_map.json \
  --observations examples/observations.json \
  --policy examples/policy.json \
  --out outputs/replay-new

# 回退会产生一个新版本，原结果和历史不被删除
python3 -m map_update rollback \
  --bundle outputs/replay-new/bundle.json \
  --version v0 --out outputs/rollback-new
```

`run --as-of-ns <十进制纳秒时间戳>` 可截断回放：之后的证据不参与当前判断。整个输入按时间排序回放，相同 `record_id` 的完全相同记录幂等去重，冲突内容报错。

每次 `run` 从指定 baseline 完整回放证据，**不是**向已有 bundle 增量追加。若改用新版本作 baseline，需要让上游按新地图重新匹配/核验；不能把旧版本记录的版本号直接改掉。

输出目录：

| 文件 | 用途 |
| --- | --- |
| `map.json` | 当前版本基础地图；保留未变化要素，删除操作仅停用 |
| `events.json` | 持续性状态、原因、独立访问数、时长、来源记录 ID |
| `planning.json` | 当前地图版本引用、临时事件、待核实事件；不推断封路 |
| `audit.json` | 各输入记录的质量门控接受/拒绝原因；不等于写回批准 |
| `versions.json` | 每个版本快照、父版本、更新前后对象及支持证据 |
| `bundle.json` | 完整回放结果，支持回退与可视化 |
| `index.html` | 无外部依赖的交互式二维地图 |

## 判断原则

- `score` 只保留作上游证据展示，不当作长期变化概率。截图中的加权分数与早期 proposal 的 `N_support/N_valid` **不是同一个量**，不能从分数反推出支持帧数。
- 连续帧按 `visit_id` 合并；不同 visit 还需要时间间隔。跨车辆、同一时段的多份观测不自动等于长期复访。
- 基础状态包括 `insufficient_evidence`（证据不足）、`temporary`（临时处理）、`long_term_candidate`（通过持续性门槛但写回检查受阻）、`applied`、`resolved`、`review_required`。
- 道路占用永远进入事件层；`temporary` 是存储/处理策略，不断言障碍必定很快消失。标线差异 `line_discrepancy` 必须先明确成几何/属性/新增/删除操作。
- 缺少 M3、对齐无效、覆盖不足、有效帧不足时不增加支持证据。缺测不等于恢复；恢复需要明确的充分观测及 `No Significant Mismatch`。
- 写回 MVP 只支持 `lane_marking` 折线的新增、几何修改、标线属性修改与停用。几何修改要求跨访问几何稳定并覆盖原要素，不能用一小段观测替换完整边界。车道用途、拓扑、可通行性不在自动更新范围。
- 采用 City 平面坐标（米），不是 WGS84 经纬度；不同城市或地图版本输入直接拒绝。

默认参数：至少 3 次访问、相邻计数访问间隔 1 h、跨度 24 h、覆盖率 ≥ 0.7、有效帧 ≥ 3、支持比例 ≥ 0.7、跨访问折线距离 ≤ 0.35 m。**这些是保守演示初值，尚未调参或验证，不是永久变化判据。** 同一次经过即使超过 24 h，也只记一次；人为拆分 visit ID 不合规。

## 对接和后续工作

先看 [材料解读与实施计划](docs/project_plan.md)，再看 [输入接口及成员对接](docs/input_contract.md)。源码入口是 [engine.py](map_update/engine.py)。

已经覆盖 64 项单元/集成测试（含 12 项真实 M3 接入测试和 7 项评估口径测试）：单次多帧、伪复访、质量不足、重复记录、坐标和版本不匹配、几何稳定性、部分几何、占用恢复、删除、新增关联、冲突更新、回退、M3 时间窗口匹配，以及真实地图、纳秒时间、定向物体轮廓、逐帧折线和缺失值语义保护。可选真实浏览器检查需要 Playwright 和 Chromium（不属于核心运行依赖）：

```bash
python3 scripts/browser_smoke.py outputs/demo-new/index.html
python3 scripts/browser_smoke.py outputs/scene0980-new/index.html --mode intake
python3 scripts/browser_smoke.py outputs/scene0980-map-new/index.html --mode scene
```

上游 stage3/5/6 已用原始输入完整复现，17 个输出文件的 JSON 值/CSV 字段与交付完全一致，stage7 的 30 项检查通过。Windows 与 Linux 的 JSON 序列化换行差异单独报告，不声称所有文件字节相同：

```bash
python3 scripts/verify_upstream.py \
  --processed 'AIE1903 原始资料/processed' --out outputs/upstream-full-new
```

原交付接入做了 235 项完整性与一致性检查，原始地图接入另做了 1724 项哈希、连接、时序和几何检查；这些结果不表示检测精度，独立标注表目前为空。

已完成本次真实 M3 结果接入。尚未完成：有来源的有效覆盖率与物理对齐评估、真实跨访问证据联调、LD 修改目标及地图 prior/current 语义核验、阈值校准和独立测试集 P/R/F1 / 几何误差 / 误更新率评估。当前存储为离线版本化 JSON，不支持多车辆并发写事务或生产部署。
