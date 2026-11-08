# M4 多帧归并与证据记录 v1

输入冻结M3候选和逐帧地图检查，以及M2参考帧索引；不读取GT。输出为M4变化记录，不代表已确认变化。地图候选按场景、clip、变化类型、地图对象ID和左右边界归并；新增候选采用方向约束及完全连接空间聚类。不能跨clip合并，接近的不同地图对象不自动当作同一对象。

## 输出

- change_records.jsonl/CSV：每个候选组一条变化记录，含成员ID、时间窗口、独立参考帧数、原始观测数、相机列表、几何和证据状态。
- frame_evidence.jsonl：每组每参考时刻一条证据，不把同一帧的多个相机当多个时间帧。
- rules.json、provenance.json、summary.json、validation.json：规则、来源摘要、统计和检查。
- qa/、qa_index.json：来自M3的单次观测检查图，保留来源，不能当作融合重建图。

schema_version=1.0.0，run_id=m4_fusion_v1。时间为纳秒字符串，几何为City米制；关联上游使用source_m3_run_id+source_candidate_ids，及source_m2_run_id+source_observation_ids。一个change_id表示一个时空候选组，不是已确认的道路变化。

## 几何融合

选择成员中对其他曲线双向距离之和最小的真实观测作为代表曲线（medoid），避免凭平均制造未观测几何。记录所有成员的包围盒，centroid_city是成员顶点坐标中位数，不是变化区域真实面积中心。difference_value取成员差异值中位数。未来改进几何重建时需提升run_id，并说明geometry_method。

## 统计不能混用

observation_count为逐相机候选数；distinct_support_frame_count为候选出现的不同LiDAR参考时刻数。observed_continuity=出现候选的时间帧数/候选首末之间的M2参考帧数，只表示该窗口的重复检测连续性，不是整个58秒的覆盖率，也不是变化概率。

proposal证据分数S=N_support/N_valid只使用独立核实的可见帧。当前visibility均为unknown，因此valid_frame_count、visible_support_frame_count和evidence_score均为null；known_valid_frame_count=0仅表示已核实可见帧为零，不表示场景实际不可见。unknown与missing分别保留。不能把4次几何候选重复除以4就声称证据分数为1。

确认预设要求：至少5个独立候选支持时刻、支持首末至少1秒、至少5个核实可见帧，且S≥0.7。阈值为开发初值，未利用GT校准。上游路缘混淆风险未解决时仍保留uncertain，不自动解除风险。连续多帧也可能重复同一个误识别。

M4不会找回M2/M3漏掉的变化，也不会用无候选帧证明未变化。没有可靠可见性时，不输出删除结论、有效可见支持率或概率。

## 本场景结论

4条M3候选归为同一地图对象right边界的1个候选组，来自4个连续时间帧，总时长约0.301秒。已知M3人工抽查怀疑观测是横道旁路缘，因此该组保留uncertain；没有已确认变化。可向下游提供几何和来源，但用于行为关联时必须保留不确定状态。

复现命令为python Argoverse工作区/m4_fuse.py。内含同帧多相机不重复计帧、可见性未知不得产生分数、跨clip隔离及左右边界隔离的回归检查。检查通过表示程序与数据一致，不表示变化识别正确。
