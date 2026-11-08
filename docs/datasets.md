# nuScenes 与 Argoverse 分开运行

2026-11-08 最新定义与运行：两个数据集现已使用独立可靠性输入接入同一套“底图结构变更 / 地图状态事件”分类，持续时间与数据集名不决定图层。方法、实际误报、状态恢复与复现命令见 [结构/状态算法](map_structure_state.md)。以下记录原数据接入与基线结果。

用户已于 2026-11-08 提供并授权运行 Argoverse。原 `pending_review` 执行限制已解除；两个数据集通过各自的适配器处理，不能仅改数据集名称后复用另一套结果。

| 数据集 | 本次输入 | 运行范围 |
| --- | --- | --- |
| nuScenes | scene-0980 原始交付与 M3 supplement | 已有场景、观测记录、事件层与标线差异层 |
| Argoverse | main 提交 `9c6f86e`，Winter_2021 的 M2–M8 冻结交付 | 重跑 M3 单帧差异与 M4 多帧融合；冻结预测后，用 M8 地图真值评价 |

Argoverse 的阶段编号 M3 是单帧候选生成，不是 nuScenes 的可靠性统计 M3；M4 是多帧融合，不是 Planning。模块编号不能直接跨交付对应。

## 运行

```bash
python3 -m pip install -e '.[argoverse]'
python3 scripts/run_dataset.py --dataset nuscenes --run-name nuscenes-run-001
python3 scripts/run_dataset.py --dataset argoverse --run-name argoverse-run-001
```

Argoverse 输出位于 `outputs/argoverse/0cfNzxkFwK3x3JB9HpcKIqoRCztSAreo__Winter_2021/<运行名>/`。再次运行请使用新名字，不覆盖旧结果或成员原始文件。`result.json` 汇总候选、原因、自动评价和来源；`M3/`、`M4/` 保存实际复跑结果；`prediction_freeze.json` 记录评价前的预测哈希。

实现先核对消费文件的 manifest、代码版本、场景、观测关联、纳秒时间与地图身份，再在没有 M8 文件的目录运行交付的预测代码。GT 在预测写出及冻结后才进入评价。

本轮没有原始 RGB／LiDAR 和可运行模型权重的完整输入，因此 M2 使用冻结观测，没有重新运行 YOLOP。原 M8 导出器依赖未提供的原始 annotation；本次复用并校验已导出的地图 GT，不伪造原 annotation 或人工 reviewer。

## 数据与标签

数据集、场景、运行版本、地图哈希、时间和坐标保持各自命名空间。nuScenes 的入口拒绝误传 Argoverse；Argoverse 结果不能填入 scene-0980 的候选、地图、M3 或受控指标。

用户说明“选中的 nuScenes 变化样本为暂时、Argoverse 变化样本为永久”。这是样本选择与持续性标签说明，不能作为推理的输入特征，也不意味着上传地图里的每个对象都发生了变化。Argoverse 本次对象清单有 288 个对象，其中 98 个标注变化、190 个未变化；可比较的地图层为 273 个对象。

已有代码的 `unknown` 和本次的 `not_implemented` 不等于模型预测“暂时”；也不等于已经预测为“永久”。当前交付的差异／融合算法尚不输出长短期类别。自动记录完成、可靠性状态、持续性预测和结构写图资格应分别统计。

评价用全部已供给支持地图层定义固定范围，单独报告确认检出、未检出和候选命中。全地图任务检出率不等于相机可见区域准确率；不把没有输出的未变化对象自动算成模型正确回答。详见 [本次复跑与问题定位](argoverse_rerun_20261108.md)。
