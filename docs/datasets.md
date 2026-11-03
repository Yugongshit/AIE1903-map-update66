# nuScenes 与 Argoverse 分开运行

当前决定：使用之前处理好的 **nuScenes / scene-0980**。**Argoverse 场景仍待审核，本轮不运行、不评估**。这是一条执行范围约束，不代表 nuScenes 的地图变化已经被证实。

| 数据集 | 当前用途 | 输入 | 本轮输出 |
|---|---|---|---|
| nuScenes | 真实场景检查、M3 接入、交互复核 | `AIE1903 原始资料` 中原有 nuScenes 文件、`external/perception_delivery`、`M3_scene0980_proposal_supplement 2` | `outputs/nuscenes/scene-0980/<运行名>/` |
| Argoverse | 等待成员审核场景 | 尚未注册运行路径或场景 | 不生成结果；未来独立使用 `outputs/argoverse/` |

没有移动、重命名或改写成员的原始数据，也没有将 proposal 中提到 Argoverse 的文字当作已交付数据。现有 nuScenes 的地图、坐标、时间戳、输入哈希仍逐项校验。

## 统一入口

```bash
python3 scripts/run_dataset.py --dataset nuscenes
```

默认也是 nuScenes。每次创建一个新目录，其中包含 `index.html`、`evaluation.json`、`provenance.json`、`dataset_manifest.json`。可使用 `--run-name review-001` 指定运行名，已有目录不会被覆盖。

`--dataset argoverse` 会在读入数据或创建输出前报“待审核”并退出。旧的 `build_evidence_demo.py` 入口也受同一约束；它保留自定义 `--out` 以兼容旧命令，推荐新工作使用统一入口。以往输出目录属于历史产物，不会自动搬迁。

## 三层分离

1. **输入与运行**：`map_update/datasets.py` 登记数据集、允许运行的场景与原路径。当前仅有 nuScenes 适配器，不能把 Argoverse 文件改名后塞进去。
2. **证据与评估**：场景、参考地图、M3、事件层、导出、来源记录都有数据集标识。场景键为 `nuscenes:scene-0980`；同名场景或同名候选不代表同一来源。受控扰动仍单独标为 `controlled_perturbation`，其来源为 nuScenes，不计作真实道路标签。
3. **可视化与人工复核**：页面明确显示 nuScenes 当前运行、Argoverse 待审核。浏览器草稿按“数据集 + 场景 + 地图哈希”保存；导入其他数据集的记录会被拒绝。旧版只针对已知 scene-0980 地图哈希的 nuScenes 草稿可以迁移，旧副本保留。

## 当前数据口径

本轮仍是 41 帧、29 个变化候选、7 个 M3 复核单元。真实基础地图写入为 0，原因仍是长期变化、有效覆盖率、独立访问等证据不足。数据集分离不改变阈值、计算结果或改图条件。

Argoverse 审核通过后，应先交付数据集版本、场景清单、地图与坐标系说明、时间和对齐规则、感知 / 可靠性结果以及评估标签状态，再增加它自己的适配器和验证。仅修改 `enabled` 不能实现跨数据集兼容；两个数据集应分别报告结果。
