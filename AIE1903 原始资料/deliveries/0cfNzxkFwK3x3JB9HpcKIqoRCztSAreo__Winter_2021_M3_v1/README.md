# M3 逐帧地图差异候选 v1

输入为冻结M2_v1中的先验地图、道路观测、匹配与帧索引。算法不读取ArgoTweak变化标签、GT导出或当前GT地图。原始M2交付不改写。全部10278条输入均保留一条decision，不能把未输出变化候选解释为未发生变化。

## 几何和类型规则

仅接受长度≥3m、保留地面点占像素曲线点数≥0.5、模型前景通道均值≥0.6的曲线。该分数是模型输出，不是变化概率。M2地图身份ambiguous的不做确定候选归类。NONE/UNKNOWN地图边界不作为已知油漆标线比较，以减轻路缘混淆；此策略也会漏掉真实几何变化。

对M2 matched曲线，将观测和地图折线按0.5m采样。先将观测投影到地图折线弧长范围，截出该范围内的地图几何，然后计算双向平均点到折线距离。这样不会因地图对象远长于当前观测而虚增差异。地图覆盖长度至少3m，无向方向差≤20度；差异>1.5m输出geometry_changed候选。阈值未经独立标定。

几何差异≤0.75m且观测颜色、先验颜色均已知且不同时，输出marking_changed候选，标明仅为颜色启发式证据。没有实虚线识别，不能判定该属性。已知颜色来自M2局部像素统计，仍可能错误。

M2 unmatched不直接当作新增。满足质量条件、距所有先验边界的最小距离>3m，并且没有远处方向接近且投影覆盖≥3m的地图对应物，才输出added候选。存在远处平行对应物时无法区分新增和大几何变化，保留insufficient_evidence。该策略有意保守，可能完全不输出新增，不等于本场景没有新增GT。

## 删除与其他能力

当前可见性unknown，没有充分可见的有效观测分母，因此每帧30m局部地图边界均输出deletion_assessment=not_evaluable，不生成删除候选。不对横道、车道用途或拓扑变化做传感器推断。视野覆盖、遮挡、多帧持续性不是本次已完成能力。

## 文件及接口

- decisions.jsonl：每条M2 observation恰好一行，含关联ID、判定状态、原因、比较指标和candidate_id。status为candidate、no_discrepancy_detected或insufficient_evidence。no_discrepancy_detected仅表示本次受支持比较未超阈值，不等于GT未变化。
- change_candidates.jsonl：候选City三维几何、来源相机/时间/观测ID、变化类型、地图对象/边界、差异值及质量标记。JSON保留完整几何，CSV仅为便于查看的摘要。
- map_checks.jsonl：逐参考帧局部先验边界的观测计数和删除不可评估原因；多个相机观测不当作多个独立时间帧。
- rules.json：全部规则阈值。
- provenance.json：M2来源文件及脚本摘要，gt_read=false。
- summary.json、validation.json、qa/及qa_index.json：统计、检查和抽样证据。青线为观测，红线为局部prior几何。

时间、City坐标、原图像素与M2相同。关联M2时使用source_run_id+observation_id；run_id为m3_rules_v1，schema_version为1.0.0。每个候选仅代表一个相机的一次观测，不是独立变化事件。所有候选status=pending_multiframe_confirmation，evidence_score=null。需交给M4按City空间与地图身份归并，再验证多帧支持；不能把候选数量直接报告为道路变化数量。

geometry_usable=true只表示通过几何质量检查；observation_valid=null表示尚未核实可见性，不能当作M4的有效可见帧分母。M4如只做重复出现统计，应明确称为持续性证据，不能声称得到了proposal中的可见性支持率。

复现：先运行m3_changes.py，再运行check_m3.py。检查包含短观测对长地图、平移距离、反向曲线三个数值回归，并核查所有来源引用、候选阈值、未做删除结论及ZIP完整性。没有独立GT评估，不报告准确率。

## 本轮结果及人工抽查

本轮输出4条geometry_changed候选，均属于ls-168084944的right边界，来自前左相机连续4帧，时间覆盖约0.301秒。几何差异约2.03至2.21米。其余记录大多证据不足，不能推断本场景只有这一个变化区域。

已人工查看qa/00.jpg与qa/01.jpg：观测青线贴近横道旁路缘，存在YOLOP把路缘当作车道标线、进而错配先验边界的明显风险。因此这些候选只能作为待复核的疑似误检，不应交付为可信道路变化。M4也不能仅靠连续4帧就消除这种系统误识别。此人工观察不使用GT标签，不计算精度。
