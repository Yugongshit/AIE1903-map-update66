# nuScenes 最终结构判断：接线修复与未达到的 5% 目标

用户明确提出的是**最终结构变化判断**的 uncertain 比例，不是物体类别、运动状态或图层语义分类的比例。本次按六个已交付结构候选独立统计，不替换分母。

## 修复的实际问题

上一版仅在 Argoverse 接通了 `model_change_decision`。nuScenes 也调用了同一个局部统计函数，但适配器只复制几何与统计值，丢弃模型判定，随后把结构假设统一写成 `confirmed=False`。因此 LD004 已有重复局部差异支持，却仍被网页显示为绿色。

现在六个 nuScenes 标线候选都保留明确的模型判定。LD004 显示红色的“算法检出局部结构差异”；五个观测不足候选显示绿色，并逐条说明原因。23 个有官方物体标注支持的占用状态仍显示黄色。模型局部检出与具体修改操作、完整几何、写入资格分别记录。

LD004 的同一时间窗口内，原参考线仍有六次观测支持。因此这里检出的是额外或位置不同的曲线，**没有认定原边界已移动**。其修改操作仍未确定，模型结构补丁为 0。

## 当前结果与 5% 目标

| 事项 | 数量／比例 |
| --- | --- |
| 占用状态事件 | 23，状态层内容保持不变 |
| 结构候选 | 6 |
| 算法局部检出 | 1：LD004 |
| 结构候选观测不足 | 5/6 = 83.3% |
| 未确定图层语义 | 0/29；不是结构变化确认率 |
| 可执行／已执行模型结构补丁 | 0/6 |
| 最终结构判断 uncertain ≤5% | **尚未达到** |

六个候选中，任何一个未确定就占 16.7%；因此在这批样本上，≤5% 实际要求零个未确定。不能把“没有确认变化”改写成“确定没有变化”，也不能把缺失观测直接改为通过。

## 为什么剩下五个不能靠接线自动确认

| 候选 | 源帧窗口 | 观测帧数 | 当前问题 |
| --- | --- | ---: | --- |
| LD001 | 11–12 | 2 | 原搜索范围内无合格参考；放宽后也仅一个共同锚点观测 |
| LD002 | 16–17 | 2 | 不满足原三时间块、一秒支持条件 |
| LD003 | 18–25 | 8 | 自身窗口内没有合格稳定参考，不能借用同 track 后续窗口的匹配 |
| LD004 | 27–33 | 7 | 六帧共同锚点支持局部差异；原参考线同时仍有观测 |
| LD005 | 28 | 1 | 单帧且参考对象有歧义 |
| LD006 | 38 | 1 | 单帧，局部重叠长度约 1.31 米 |

对同一六个精确窗口做预先指定的敏感性诊断：

| 条件 | 局部检出 | 证据不足 |
| --- | ---: | ---: |
| 原条件 | 1 | 5/6 |
| 两个时间块、0.5 秒 | 2：LD002、LD004 | 4/6 |
| 上述条件＋4 米搜索范围 | 2 | 4/6 |

没有结构变化真值用于检验新增检出是否正确，故没有把该诊断用作参数选择，也没有宣称准确率提高。生产阈值保持不变；诊断见 [逐项结果](nuscenes_threshold_diagnostic_20261108.json)。

## 核对了哪些源标注

当前交付有 2,278 条物体框标注，类别均明确；这些记录不是道路结构变化真值。物体运动报告中的 `uncertain` 为 3/154≈1.95%，另有 10 个 `insufficient_observations`；该比例与用户指定的最终结构判断目标不同，仅保留在来源说明中。

六个标线候选的原始 `review_status` 全部为 `CANDIDATE_UNVERIFIED`，`track_identity_verified=False`。上游证据级别为两个 HIGH_EVIDENCE、四个 INSUFFICIENT_EVIDENCE；HIGH_EVIDENCE 不等于独立真值确认。29 行候选评价模板的 truth 全空，交付报告明确没有经核验的历史／当前道路地图变化配对。因此当前不能像 Argoverse 的 98 个真值变化那样，为 nuScenes 构造已确认道路修改回放。

新增审计在预测冻结之后读取这些来源，保存文件哈希，并分别给出物体类别、运动状态、源候选、图层语义、结构模型判断及写入状态的分母。不会读取这些统计后更改模型预测。

## 输出与复现

结果目录：`outputs/pipeline-v5/nuscenes-confirmation-20261108/`；审计位于 `result.json` 的 `evaluation.nuscenes.status_audit`。

```bash
python scripts/run_change_pipeline.py --out outputs/pipeline-v5/new-nuscenes-run
python scripts/build_pipeline_studio.py \
  --result outputs/pipeline-v5/new-nuscenes-run/result.json \
  --out outputs/pipeline-v5/new-nuscenes-site
python -m unittest discover -s tests -v
python scripts/check_pipeline_studio.py outputs/pipeline-v5/new-nuscenes-site
python scripts/check_composite_map.py outputs/pipeline-v5/new-nuscenes-site
```

输出目录必须不存在。原始资料保留。Argoverse 的模型预测及两个数据集的状态层输出需与上一版本逐字节核对，避免修复 nuScenes 时改变已发布的 Argoverse 结果。
