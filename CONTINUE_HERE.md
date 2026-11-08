# 新对话从这里继续（2026-11-08）

本文件用于接续此前的 AIE1903 对话。网站最近一次功能升级为 2026-11-06；2026-11-08 做的是完整归档、恢复检查和接续说明，没有重新运行模型或生成新的真实实验结论。

## 1. 当前工作与入口

用户是黄子明，负责 **Evaluation and Evidence 的最终汇总**。展示需要串起成员的感知、多帧验证、可靠性和地图处理结果，能看清差异位置、判断依据和评估失败案例。用户已经完成 pre，目前继续完善项目和等待/接入成员的新交付，不需要重新寻找旧 demo 或自动重做全部 PPT。

- **继续开发的分支：`feature/evidence-workbench-20261104`**。当前代码和完整归档均在本分支。
- [英文工作台](https://yugongshit.github.io/AIE1903-map-update66/en/index.html#decisions) · [中文工作台](https://yugongshit.github.io/AIE1903-map-update66/zh/index.html#decisions)
- [直接下载网站、代码与说明 ZIP，约 18 MB](https://github.com/Yugongshit/AIE1903-map-update66/raw/d5c61b5ec9c813d1af6837fadcca38e50923cb52/downloads/AIE1903_Evidence_Studio_20261106.zip)
- [Word 使用说明](https://github.com/Yugongshit/AIE1903-map-update66/raw/d5c61b5ec9c813d1af6837fadcca38e50923cb52/downloads/AIE1903_Quick_Start_CN_20261106.docx)
- [实现、演示路线与构建说明](docs/decision-studio.md) · [全部归档索引](archives/session-20261108/README.md)

`main` 保存成员原始资料；`gh-pages` 保存部署网站和下载文件。旧的 `deliverables/evidence-demo-20261103`、`deliverables/m3-downloads-20261103`、`presentation-downloads` 是历史交付入口，不是当前开发起点。不要把本地旧 `work` 分支上较早的脚本覆盖到当前代码。

## 2. 已经完成的功能

1. **地图对比**：默认 LD003 第 18 帧；29 个候选可选。可拖动分界线、切换观测/处理结果、叠加、缩放和平移；41 帧时间轴区分支持、不支持和没有记录。
2. **证据追溯**：单候选展开感知、多帧验证、可靠性和地图决策，包含原始字段、精确窗口、来源文件与 SHA256；多帧验证和可靠性是汇入决策的两项依据。
3. **评估检查**：真实评估缺口与 126 个受控扰动案例分开；切换方法和条件，检查误报、漏报、证据不足和待复核案例；同时展示有判定样本上的 F1、判定覆盖率和全部正例召回率。
4. **受控写图**：读取现有维护引擎保存的副本结果，展示 0.6 / 0.8 / 1.2 米偏移，0 / 12 / 26 小时阶段、版本和几何回退，并有局部放大图。
5. **原始场景和 Rerun**：保留原视频与成员标注；嵌入官方 Rerun 0.22.1。已核对场景的帧和候选可双向联动；本地未知录制独立显示，不伪造时间或评估对齐。
6. **双语与交付**：英文保留原 nuScenes 类别，中文只翻译显示名称；两个网站、Word 说明、问题报告、案例 JSON 和 CSV 下载均可使用。

推荐操作路线：默认 LD003 → 拖动分界线 → 对比处理结果 → 展开 M3 和决策依据 → 受控写入实验 → 26 小时 → 回退 → 评估检查。网页是可操作的展示，不是自动播放动图。

## 3. 不要改变的事实与口径

| 项目 | 已核对事实 |
| --- | --- |
| 数据范围 | 当前只运行 **nuScenes / scene-0980**。Argoverse 资料待审核，不能合并运行或统计。 |
| 场景 | 41 帧，约 19.85016 秒，一次真实访问。连续帧、重复播放、重复目录不等于独立复访。 |
| 候选与标注 | 29 个候选：23 个占用、6 个标线差异；157 个局部地图要素；2,278 条 nuScenes 原标注。没有重新训练或预测物体类别。 |
| 其他交付 | 332 行逐帧候选证据、13 个行为片段、175 对关联。关联表示时空接近，不证明因果或地图差异导致车辆行为。 |
| 真实改图 | **基础地图真实写入 0 次**。占用进入事件候选层，但没有证据确认其短期消失、已清除或整条车道封闭。 |
| M3 | 梅子衡的可靠性模块已接入。七个复核单元均证据不足：六个未匹配标线候选，加独立的 T004 第 26–40 帧局部匹配窗口。 |
| 精确窗口 | LD003 为 T004 第 18–25 帧，不能套用同 track 第 26–40 帧诊断。LD003 支持 8/8，规则分数 1.00，仍不构成写图许可。 |
| 分数 | 预设规则分数不是模型概率或真实准确率。整个窗口的分数包含后续帧，不能伪装为更早时刻的在线判定。 |
| 距离 | LD003 第 18 帧单点最近参考距离约 2.87 米，只是可视化辅助；源窗口距离中位数约 7.07 米是另一种量。最近参考线不是已核验修改对象。 |
| 真实准确率 | 217 条独立标签仍缺失，真实 Precision / Recall / F1 未知。软件检查通过不能替代准确率。 |
| 受控 M3 评估 | 126 个案例来自同一场景三个窗口的依赖性扰动。质量门控方法已判定 F1 94.1%，判定覆盖率 28.6%，全部正例召回率 22.2%，需要一起解释。 |
| 受控写图 | 与上述 M3 扰动评估分开。偏移、复访时间、95% 覆盖率及对齐条件为人为输入；实际引擎输出只修改隔离副本，没有对应真实候选。 |
| 照片与规划 | 有原感知演示视频，但当前可读材料没有 CAM_FRONT JPG；标定投影不是实景照片。已有自车轨迹不是推荐路线。规划和跨车共享未接入，属于后续扩展，不能阻塞维护原型开发。 |

源地图 SHA256：`17939122b940d7faff50fa7ff623ded7f739549a781c32dff1ecd1ac7bebca02`。物体 CSV SHA256：`6c8041f1b0e065d3341b381189f396376d80be20f7dda7f20269adbc2b387367`。更多来源见网站 `version.json`、证据面板和各文件 manifest。

## 4. 代码和数据在哪里

| 路径 | 用途 |
| --- | --- |
| `AIE1903 原始资料/` | Git 中已有的成员资料；原始资料基点 `ba59989`。避免改写或覆盖。 |
| `external/perception_delivery/` | 已整理并带 manifest 的成员交付；感知 stage3/5/6 和评估输入。 |
| `AIE1903 原始资料/M3_scene0980_proposal_supplement 2/` | M3 真实窗口、受控逐例 CSV 和比较表。 |
| `map_update/engine.py` 等 | 地图维护、质量门控、访问累计、事件、版本和回退。 |
| `map_update/decision_data.py` | 验证源表和已有指标一致，生成只用于展示的索引和单点距离。 |
| `map_update/decision_studio.html`、`.css`、`.js` | 新地图对比、证据链与评估工作区。 |
| `map_update/rerun_bridge.js` | 官方 Rerun 与已核对网页场景联动。 |
| `scripts/build_decision_studio.py` | 基于已核对 Rerun 网站构建当前展示。 |
| `scripts/check_decision_studio.py` 等 | 工作台、双语与原生 WebGL 交互检查。 |
| `archives/session-20261108/` | 1,449 个现有项目文件的完整归档、校验清单与目录索引。 |
| `scripts/restore_session_archive.py` | 按组恢复归档，检查 SHA256，拒绝覆盖内容不同的现有文件。 |

最近应用实现提交 `0d90d1f`，当时网站部署提交 `35aa3d4`；下载文件提交 `d5c61b5`。本次新增的提交保存归档和接续说明，不能把它们的日期当作新模型实验日期。

## 5. 新环境怎么恢复并继续

先切到 `feature/evidence-workbench-20261104`。不要依赖旧对话中的 `/tmp/...` 或 `/workspace/...` 必然存在。归档包含 1,229 个 outputs 文件，以及现存上传附件、旧下载、生成图片、工作区备份和未提交根目录代码快照；不包含虚拟环境、缓存、凭据或从未成功读取到的文件。

```bash
# 查看可恢复的目录
python3 scripts/restore_session_archive.py --list

# 恢复当前展示及其构建输入；只还原缺失文件，已有且内容相同的文件跳过
python3 scripts/restore_session_archive.py \
  --group outputs/nuscenes/scene-0980/rerun-release-20261105 \
  --group outputs/nuscenes/scene-0980/decision-release-v2-20261106 \
  --destination .

# 启动已经构建的网站，无需先重跑模型
python3 -m http.server 8000 --bind 127.0.0.1 \
  --directory outputs/nuscenes/scene-0980/decision-release-v2-20261106

# 可选：将所有历史成果恢复到独立目录，避免混入当前代码
python3 scripts/restore_session_archive.py --destination restored_session
```

本地英文入口 `http://127.0.0.1:8000/en/index.html#decisions`，中文替换为 `/zh/`。Rerun 需要 HTTP 和 WebGL，直接双击 HTML 不能验证其全部功能。构建与依赖说明见 [decision-studio.md](docs/decision-studio.md)、[rerun-integration.md](docs/rerun-integration.md)；Rerun 环境使用独立虚拟环境和已固定版本，不能随意升级 SDK 后宣称结果相同。

归档中的旧 Proposal、PPT、讲稿及其生成脚本保留原貌，属于历史记录。早期“可靠性未交”“当前用户做地图维护”、旧测试数量、旧图例或旧原型演示不能取代本文件记录的当前状态。原根目录脚本快照在 `historical_root_working_copy/`，不要直接覆盖活跃源码。

## 6. 验证与待办

当前应用已完成 **99 项核心测试、56 项新工作台检查、40 项双语回归、35 项 Rerun 检查**。最近验证记录见 [decision-validation-20261106.json](docs/decision-validation-20261106.json)。范围为云端 Chromium / SwiftShader，未宣称验证实体 Mac、Safari 或车辆。本次归档另外逐一恢复并核对 1,449 个文件，检查了已有文件保护、路径安全和损坏归档拒绝。

已修复并记录的运行问题包括：无效多边形通过检查、wheel 漏装翻译表、旧 SDK 属性/类别丢失，以及静态模块导入路径 404。详见 [运行审查](docs/runtime-audit-20261105.json)。网站仍保留 11 个数据/实现/评估问题，不能把“应用能跑”写成“全部科研问题解决”。

下一步按新交付推进：

1. 核对新成员文件的路径、版本、候选 ID、时间窗口、坐标系、地图版本和可复现命令，保留原文件与来源哈希。
2. 补有来源的有效覆盖率、物理对齐和候选到地图对象的匹配；未知值不能手改成通过。
3. 补真实独立复访及地图历史/当前角色说明，才可能验证长期变化和真实基础地图写回。
4. 补独立真值、正常/遮挡/偏移负例和按场景划分的测试集，统计误更新、覆盖、几何误差和延迟。
5. 若新交付含 CAM_FRONT 原图或完整带图像的 RRD，先核对帧/时间/标定，再接入真实前视显示；原视频不自动等于逐帧相机数据。
6. 规划是可选后续对照实验：需要真实规划器和结构化输出，不能画一条线就称为安全推荐路线。Argoverse 仍待明确审核后再单独接入。

## 7. 用户明确的偏好

- 优先做可操作的网页、可追溯的真实数据展示和能直接下载的文件；不让用户反复复制长命令或找临时工作区路径。
- 英文和中文分别提供；英文保留成员原标注，中文翻译同一标注。避免擅自造类别、几何、照片、路线或高分结论。
- 必须能解释“地图到底哪里改了”和“为什么当前不能改”。真实与受控数据始终分开。
- 黄子明是最后的 Evaluation and Evidence 汇总环节；潘明阳的环节在其前。姓名放分工处即可，架构图不要突出个人。其他已明确分工：崔映庭负责感知/单帧变化，梅子衡负责可靠性，王子墨负责多帧/行为与关联。
- 如果后续重做材料，约定的组织形式是 Objective、Input、Pipeline、Output、Conditions and Limitations；长期变更写基础图，短期/占用走事件层，跨车共享为 Extension。描述这些能力时仍需保留证据边界。
- 用户希望持续推进并报告实际问题；不要因为上下文长就从头重做已有成果，也不要把旧草稿当作最新实现。

可复制给新对话的短指令见 [next-conversation-prompt.txt](docs/next-conversation-prompt.txt)。
