# 资料可读性确认与后续对接准备

核查日期：2026年11月3日（中国标准时间）。本次按用户最新要求停止寻找和制作 Demo，先确认真实资料能否读取，并为后续成员交付保留接入基准。

## 1. 当前访问状态

- 云端执行环境已恢复连接，文件命令可实际运行。
- 仓库：`Yugongshit/AIE1903-map-update66`；工作区路径：`/workspace/AIE1903-map-update66`。
- 当前工作分支为 `work`，HEAD 为 `6c5d134380725999425e0d6626773fef2aef3e56`。
- 本次通过 `git ls-remote --heads origin main` 成功访问 GitHub，远端 main 的提交号与当前 HEAD 相同。这个判断仅针对检查时刻；工作区已有未提交的代码和文档也仍保留。
- `AIE1903 原始资料` 中的 **542 个文件均完整读取并计算 SHA-256**，总大小 **27,810,206 字节**。所有 JSON 均成功解析，所有 CSV 均读取表头和行数。没有读取错误、Git LFS 占位指针或已跟踪但缺失的文件。
- 文件类型：310 个 CSV、143 个 JSON、75 个日志、8 个 Python 源文件、2 个 Markdown、3 个 TXT、1 个 CMD。

机器可读基准：`outputs/readiness-2026-11-03/input_inventory.json`；实际字段和核心行数：同目录的 `upstream_schemas.json`。

## 2. 已实际读取的核心数据

以下路径相对 `AIE1903 原始资料/`。

| 文件 | 本次确认 |
| --- | --- |
| `scene_info.json` | 场景 scene-0980，地点 singapore-onenorth；confirmed_hd_map_conflict 为 false |
| `processed/frames.csv` | 41 帧，片段跨度 19.85016 秒；含 frame_index、timestamp_us、time_s 和车辆位姿 |
| `perception/outputs/stage3/scored_candidates.csv` | 29 个候选：23 个 ROAD_OCCUPANCY、6 个 LANE_MARKING_GEOMETRY；map_change_status 均为 UNVERIFIED |
| `perception/outputs/stage3/frame_evidence.csv` | 332 行逐帧证据 |
| `perception/outputs/stage3/geometry.json` | 可按候选几何标识读取的 JSON 对象 |
| `perception/outputs/stage5/behavior_events.csv` | 13 个行为事件 |
| `perception/outputs/stage6/associations.csv` | 175 对关联记录：5 对 associated、81 对 not_associated、89 对 uncertain |
| `perception/evaluation_labels/` 下三份 CSV | 分别为 29、13、175 行，truth 已填写数量均为 0 |

地图文件、原始 processed 表格、nuScenes 元数据、感知流水线源码也包含在上述完整读取范围中。此处的“完整读取”不等于重新运行算法或验证全部数据语义；本次没有重新执行感知、规划或地图更新流程。

## 3. GitHub 副本与 Mac 本地文件的边界

仓库已提交的 `.gitignore` 排除了 `.rrd`、`.mp4`、`.mov`、`.jpg`、`.png`、`.bin`、模型权重和压缩包等文件类型。当前 main 的资料目录不含任何 RRD 或 MP4，也不含原始图像和点云二进制文件。

因此，用户截图中的 Mac 路径存在，不代表其中所有文件已进入 GitHub。云端不能直接读取 `/Users/huangziming/...`，也不能自动读取尚未推送的本地新增资料。

此前单独上传的附件中，本次仍能完整读取：

- `scene-0980_results_overlay.rrd`：341,567 字节。
- `1368f655c534d97224a1f417aed524eb.mp4`：3,403,398 字节。

这两项是单独上传的副本，不是 GitHub main 中的大文件。本次仅核验其字节可读性和哈希，没有播放视频或打开 Rerun 查看器；其他本地录制的可用性不能据此推定。

## 4. 等待新交付期间的准备基准

### 已整理的连接字段

- 候选：`candidate_id`、`event_id`、`evidence_family`、`geometry_id`、`first_time_s`、`last_time_s`。
- 逐帧证据：`candidate_id`、`frame`、`time_s`、`observation_usable`、`supports_hypothesis`、`source_file`。
- 绝对时间连接：将逐帧 `frame` 对应到 `processed/frames.csv` 的 `frame_index`，保留整数 `timestamp_us`；原型纳秒接口用整数乘 1000，不经过浮点转换。
- 行为与关联：`behavior_id`、`track_id`、`candidate_id`、时间窗口、关联结论及原因。
- 坐标、地图版本和元素身份沿用真实地图与现有 `inspect-scene` 接入结果；不从视频标签或文件名推断坐标和版本。

### 可靠性模块到齐后

先检查真实输入输出样例，再核对候选或地图要素、场景/访问、地图版本、坐标系和时间窗口。需要保留可靠性结论、有效覆盖、对齐有效性和原因。`stage3` 是感知流水线阶段，不是成员三的可靠性模块；现有 evidence_score 不能自动替代其输出。

现有原型规范见 `docs/input_contract.md`。它是适配目标，不要求组员无条件重写其现有格式；应通过字段映射连接实际交付，并保留原值和来源。

### 规划模块到齐后

核对地图版本、起终点、规划时刻、路线/行为结果、可行性、拒绝原因和运行时间。将实际规划结果交给最终证据与评价汇总。遵循最后确认的展示顺序：规划在前，地图更新与最终汇总在最后；地图更新的证据门槛仍由真实观测、可靠性和持续性支持决定。

### 评价与长期性

当前三份真值模板仍未填写，不能从它们计算实际准确率；现有场景片段不能单独证明长期变化。新交付到齐后，先检查独立标注、跨次访问身份和采集时间，再决定哪些评价成立。

后续每次资料更新，先与本次 SHA-256 和字段清单比较，避免把重复副本计为新的访问或新实验。无需重建模拟数据来替代缺失的真实输入。

## 5. 本次工作边界

本次只新增资料核查和接入准备记录，未修改原始资料，未提交或推送 GitHub，未重新寻找、制作或播放 Demo。旧工作区中的代码与用户未提交改动均保留。
