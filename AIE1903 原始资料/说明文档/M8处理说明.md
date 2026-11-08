# M8 GT核对与评估准备 v1

本阶段已完成可用地图GT整理、预测对照诊断、评估入口和独立标注表；独立人工标注与正式效果评估尚未完成。M2至M7的一致性PASS只说明程序与数据自洽，不代表准确率合格。

## 地图GT及方向

本场景属于constructed_train。官方stale先验由M2固定输入提供；当前物理GT由原始ArgoTweak的old对象提取。本场景没有[1]对象，因此不存在把官方解析器为解释保留的删除占位物误导出为现实对象的问题。导出脚本对分组和[1]做断言，不能不加核实推广至其他场景或真实测试集。

current_gt_map.json含240个车道段、33个横道；prior_map_reference.json是M2使用的219个车道段和27个横道。这里只导出这两个M2地图层；15个可行驶区域仍保留在对象GT清单，不能因为它们没放入先验文件就当作新增。

gt_object_inventory保留288个对象、原始changes、官方变化标记和派生状态差异。gt_changed_objects保留98个官方变化对象及完整prior/current状态。98=89个车道段+9个横道，190个对象未标注变化。

派生类别是比较实际输入先验与当前GT得到的，不声称解决所有原始数字代码：

| 更新类别 | 定义 | 对象数 |
|---|---|---:|
| added | 先验缺少、当前存在 | 27 |
| geometry_changed | 两者都存在，二维边界双向距离>0.01m | 30 |
| marking_changed | 标线属性字段不同 | 11 |
| lane_type_changed | lane_type不同 | 35 |
| connectivity_changed | predecessors或successors集合不同 | 16 |

同一对象可以属于多类，计数不能相加当作对象总数。几何差异只比较二维地图，Z不作为这些类别定义的依据。原始代码4/6/7/9/10仍完整保留，不凭字段对应关系擅自命名为官方编码。

## 当前预测诊断

M4只有一个uncertain组，指向ls-168084944的right边界。该对象官方changes=[0]，先验与当前相同；因此这个候选指认的对象不是真值变化对象。这与此前路缘误识风险相符。M4没有confirmed正例，不能把该候选直接计为最终预测FP再宣称最终Precision=0。

prediction_gt_diagnostics逐项保存对象对应关系、预测状态和诊断。当前无可靠可见性评估范围，也不能直接把98个全局变化对象都当作三路前视有效检测区域中的FN。因此正式地图Precision/Recall/F1为null，不输出虚假的全地图效果。

## 需要独立补齐的材料

annotation_templates中的人工字段不会用预测填充，重复运行也不会覆盖已有表。

1. map_evaluation_scope.csv：288个对象的官方变化标签已填；reviewer须依据数据覆盖和可见性，在不参考预测正误的前提下填写include_in_evaluation、scope_reason、reviewer和evidence_reference。缺这些字段不进入正式二元评估。规则固定后，uncertain作为弃判，在正GT上计漏检；没有预测正例时Precision为null，而非1。
2. vehicle_2d_gt.csv：按固定时间规则选择0–10、24–34、48–58秒三个连续窗口，每0.5秒、三路相机，共189张来源图像。truth_objects_json、annotator和evidence_reference均空。格式为[{"gt_track_id":"manual_001","class_name":"car","bbox_xyxy_px":[10,20,100,150]}]；人工确认无车辆的图像才填[]。不要把M2/M5框复制为GT。可以使用CVAT等工具标注后转换到此表，需检查框坐标对应原图。
3. behavior_gt.csv：独立观看连续图像，给人工GT身份标注直行/停车/转向等及起止时间、gt_behavior_event_id；不明确填uncertain，不能按M6候选照抄。当前为表头，不假造标注行。
4. association_gt.csv：结合独立行为、变化GT与连续画面，标注关联、不关联或uncertain；配对使用gt_change_id、gt_track_id、gt_behavior_event_id，不直接复用预测配对结果。
5. identity_matches.csv：预填126个预测轨迹ID，供人工建立与独立GT身份的映射。gt_track_id为空；这不是GT轨迹身份表。不能把每个预测片段直接当作一个真车辆。

同一标注窗口内保持GT身份一致；窗口间有长空档时不强行连续，应记录窗口范围。189张2Hz图像可作为检测和窗口内跟踪标注起点，完整行为起止仍需查看连续源图。2D框无法验证绝对City三维定位误差，另需独立三维参考。

## 评估入口的实际状态

m8_evaluate.py可导出GT、对照当前预测并在map_evaluation_scope完整填写后计算二元对象级地图指标。其他模块当前只准备独立标注接口与阻塞状态，尚未实现/运行完整AP、IDF1、行为事件和关联评估器；需要拿到相应人工标签后，按固定匹配协议补齐，不能把本报告叫作完整性能评估。

evaluation_report.json为唯一状态入口；所有缺少真值的metrics为null而不是0。整个场景属于开发训练池，后续即使完成标注，也只能报告开发场景结果；独立测试仍需按官方划分另取场景，固定参数后评估。不能按邻近帧切分来冒充独立场景测试。

GT导出与标注材料仅用于M8，不回填M2–M7推理特征。若根据M8诊断修改算法，必须新建run_id并报告开发迭代，不再把本场景当作未见测试数据。

## 交付

ZIP含当前GT、先验参考、对象级标签、预测GT诊断、评估报告、人工表和来源摘要；不含原始传感器文件。队友收到后可优先做独立人工标注。需要图像时按source_image_ref相对于Argoverse工作区读取。

运行m8_evaluate.py，再运行check_m8.py核验与打包。检查验证对象ID、状态差异、唯一候选的GT、人工字段未被填预测、GT来源哈希及无标签时不产生指标；这仍不是性能评价本身。
