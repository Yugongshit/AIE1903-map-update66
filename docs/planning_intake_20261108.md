# Planning 交付已收到：初步核对（2026-11-08）

用户已通过 GitHub Desktop 将 Planning 模块推送到云端。实际交付提交为 `b44f828`（Planning），合入 `main` 后的提交为 `17ee2c19d9d99919ca9f9d0e5f277f585e177115`。

路径：`main` 分支的 `AIE1903 原始资料/Planning_M4_scene0980_handoff/`，共 **44 个文件**。这些文件是在此前会话归档之后新增的，已经由 `main` 保存，不属于旧归档的 1,449 个文件。本次没有合并到应用开发分支或部署网站，也没有重跑规划算法。

## 已读取的内容

- `HANDOFF_README.md`、`requirements.txt`、`run_planning.py`：交付说明、依赖和可复现入口。
- `inputs/`：冻结输入，包括地图、逐帧数据、障碍物、候选与 M3 结果。
- `planning_results.jsonl`、`planning_summary.csv`、`summary.json`：逐例结果与汇总。
- `routes_city.geojson`、`obstacles_city.geojson`：路线候选与障碍物的 City XY 米制几何，不是经纬度。
- `candidate_evidence_used.jsonl`、`run_manifest.json`、`map_graph_audit.json`、`m5_handoff.json`：证据引用、参数/来源、图结构审计和后续对接接口。

说明文件提到了 `figures/`，但此次 GitHub 文件树中没有该目录。仓库忽略 PNG 等图片扩展名；文件缺失的实际上传原因尚未确认。代码、输入和结构化路线结果已经可读，后续可先据此复现。

## 本次确认的范围

仅进行了读取和数据结构核对，没有执行 `run_planning.py`，没有验证其算法正确性或重现运行时间。

- JSONL 与 CSV 均为 **123 条记录**；planning ID 唯一。
- 三种策略 `map_only`、`fixed_candidate_penalty`、`reliability_aware` 各有 41 条，帧号均覆盖 0–40。
- scene ID 为 `scene-0980`；每条记录中的地图 SHA256 与现有工作台一致：`17939122b940d7faff50fa7ff623ded7f739549a781c32dff1ecd1ac7bebca02`。
- 已从逐例 JSONL 重新计数，三种策略均有 **7/41** 帧通过其静态路线几何检查；行为计数均为 5 条 `NO_VERIFIED_PLAN`、29 条 `STOP_CANDIDATE_UNVERIFIED`、7 条 `VERIFY_REQUIRED`，与汇总文件一致。
- 全部记录的 `observed_space_verified` 为 null，`execution_authorized` 为 false。
- 三策略上述汇总计数相同，不能据此声称可靠性策略已经提高规划效果；逐帧路线、代价或其他差异尚待进一步检查。

`run_planning.py` SHA256：`dca36c2f4047e5e9148d31805dee3c68b68f305128651b5853b0a1bfcf1e7881`。

## 接下来如何继续

当前状态是 **Planning 已收到，待复现及接入 Evaluation & Evidence 网站**。既有网站仍展示之前的版本，不能以本次文件接收代替集成完成。

新对话先从 `origin/main` 上述提交读取完整交付，再在独立工作分支按交付说明运行。核对输入哈希、逐帧时间、场景/地图身份、候选 ID、M3 只在场景末尾可用的时间限制，以及所有失败/不可评估记录。复现后将三策略对照、路线候选和拒绝原因接入中英文评估展示。

该模块属于基于冻结标注快照的静态规划基线，没有自由空间核验、动态预测、交通信号与完整规则状态。路线是分析候选，不是已经执行或批准安全行驶的轨迹。`m5_handoff.json` 明确记录 `map_updates_authorized_by_this_package: false`；规划失败或绕行不能直接证明永久地图变化。
