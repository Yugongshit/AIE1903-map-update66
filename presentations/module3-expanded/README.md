# Complete English presentation: expanded Module 3

[Download the complete English PPTX](AIE1903_Complete_EN_Module3_Expanded.pptx)

整套英文 PPT 共 39 页。Part 2 / Module 3 位于第 24–32 页，共 1 页扉页和 8 页正文。其他成员的原稿内容与版式保留，讲稿文件未修改。

## Module 3 每页讲述重点

| 页码 | 主题 | 讲述重点 |
| --- | --- | --- |
| 24 | Map Maintenance | 承接感知、可靠性和规划，提出本模块的问题：是否需要更新地图，以及更新到哪一层。 |
| 25 | Map Updating and Evidence Aggregation | 说明你接收候选几何、可靠性结果和规划记录，再按场景、时间窗口和地图版本关联证据，输出地图决策、可视化与证据包。 |
| 26 | Temporary, Structural and Unresolved Changes | 说明三种结果：短期事件进入临时事件层；证据支持的长期结构变化更新底图；尚不明确的情况继续保留验证。当前占用可以先确认，长期性仍可未确定。 |
| 27 | Persistence and Independent Evidence | 解释首次与末次观测、有效批次、独立重访和几何一致性。一次行驶的连续多帧不能当作多次独立重访。 |
| 28 | Temporary Event Layer | 指着分层图说明临时事件叠加在底图之上。记录位置、受影响道路、时间、有效状态和证据；过期或暂时没看到，不能直接证明障碍已清除。 |
| 29 | Versioned Base-Map Updates | 说明可靠结构几何与独立持续性证据是修改条件。解释新增、修改、停用地图元素，以及旧版本、精确差异、来源追溯和回滚。 |
| 30 | A Candidate with Strong Short-Window Evidence | 用代码中的真实候选说明：15 帧、6.899 秒、证据分数 1.0，但地图变化状态仍为 UNVERIFIED。高分和短时连续出现不等于长期变化，分数也不能解读为 100% 准确率。 |
| 31 | Map Visualization and Decision Review | 比较原底图、临时事件叠加和结构更新。后两种是不同处理结果，不是必然升级顺序；在实际系统中应使用相同坐标与范围，并关联证据和版本。本页图片是方案示意，集成地图编辑仍是后续工作。 |
| 32 | Evaluation Protocol and Evidence Package | 说明你如何汇总全组评估：感知与可靠性、规划对比、地图更新正确性、延迟、错误编辑和回滚，并保存失败案例及决策历史。真实观测与受控扰动分别报告。 |

地图配图为概念示意；第 30 页的数值来自仓库中的候选记录。以上为逐页要点，不替代或修改已有讲稿。
