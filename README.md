# 道路地图结构变更与状态事件

**地图前后对照与 Perception 说明已更新：** 使用可拖动分割线比较原图和更新后视图：拉到最右边是全原图，最左边是全更新后。黄色表示状态事件，红色表示已确认结构变更，绿色表示未确认结构候选；当前真实红色变更为 0。nuScenes 显示 157 个底图要素、23 个状态记录和 6 个结构假设；预测结果保持原样。页面可展开查看 nuScenes 14 类官方物体标注及 Argoverse 粗车辆/车道线输出。见 [标注来源与地图对照说明](docs/perception_and_composite_map.md) · [滑动对照验证](docs/swipe-map-validation-20261108.json)。

本仓库包括地图维护原型和 Evaluation & Evidence 工作台。用户黄子明负责最终评估与证据汇总；地图维护原型接收感知与可靠性结果，区分底图结构变更和地图状态事件，维护观测证据、事件有效性与版本历史。

## 新对话接续入口（2026-11-08）

**最新：结构/状态分类已重写并运行。** 按用户修正的定义，区分“底图结构变更”和“地图状态事件”，不再用持续时间选择图层。nuScenes 23 个占用事件进入状态层，6 个标线结构假设未证实；Argoverse 新支持的 1 个结构候选经真值核对为几何误报。实际底图写入 0，不生成等待人工复核的队列。没有独立结构/状态分类标签，旧 23/29 长短期分数不再使用。新实现与原因见 [结构/状态算法说明](docs/map_structure_state.md)，[中文结果](https://yugongshit.github.io/AIE1903-map-update66/algorithms/zh/index.html) · [英文结果](https://yugongshit.github.io/AIE1903-map-update66/algorithms/en/index.html)。下方旧 M3、自动记录与长短期状态属于历史记录，以此条为准。

**先读 [CONTINUE_HERE.md](CONTINUE_HERE.md)。** 当前开发分支是 `feat/structure-state-routing`，包含此前 `fix/automatic-map-decisions` 的网站与自动记录修正，以及用户在 `main` 提交 `9c6f86e` 的 Argoverse 交付。

**原始 Argoverse 基线复现：** 582 帧、10,278 条冻结观测 → 4 条逐帧差异候选 → 1 个 uncertain 融合组 → 0 个确认变化。已有 M8 地图真值用于自动评价：候选命中 0/1，全供给地图 98 个变化中确认检出 0。该数值是整图任务完成度，不是可见区域准确率。方法、原因和运行命令见 [复跑诊断](docs/argoverse_rerun_20261108.md) 与 [独立数据集入口](docs/datasets.md)。原始基线未执行持续性分类；最新版已按用户修正的结构/状态定义运行，见上方新入口。

**自动处理更新：** 当前 29 个候选全部处理完成，23 个占用事件和 6 个标线差异分别保存，不再生成“等待人工复核”的任务。基础地图结构写入仍为 0；持续性未知与已经观测到物体分开表达。另修复同一车道不同占用事件被合并清除的问题。方法、输出与边界见 [自动处理说明](docs/automatic_processing.md)。

**Planning 后续交付已收到：** `main` 的 `17ee2c1` 新增 44 个文件，包含三种策略各 41 帧的规划结果。当前已做基本结构核对，尚未复现或接入网站；见 [接收记录](docs/planning_intake_20261108.md)。

[英文网站](https://yugongshit.github.io/AIE1903-map-update66/en/index.html#decisions) · [中文网站](https://yugongshit.github.io/AIE1903-map-update66/zh/index.html#decisions) · [当前实现说明](docs/decision-studio.md) · [1,449 个工作文件的归档与恢复](archives/session-20261108/README.md) · [给新对话的短指令](docs/next-conversation-prompt.txt)

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
