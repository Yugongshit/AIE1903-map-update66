# 增强版 Rerun 可视化

双击 `open_results.cmd` 打开 `scene-0980_results_enhanced.rrd`。运行组件位于工作区 `.runtime/rerun`，无需全局安装。如果已安装 Rerun 0.22.1，也可以直接在其中打开 rrd 文件。

## 两个页面

- **Scene + six cameras**：保留原有地图、六路相机、道路语义、物体与轨迹；原候选标签追加 `score=…`，车辆标签追加行为。
- **Results + distance curves**：地图、当前帧的分数和行为表，以及5对关联结果的距离曲线；每对结果独立子标签，灰色参考线为3米和5米。

底部 elapsed_s 时间轴控制同步回放。灰色关联线只在该对结果被判为 associated、且当前同帧距离不超过3米时显示；下一帧显式清除旧线。其余81对 not_associated 和89对 uncertain 保留在 outputs/stage6/associations.csv，不全部画线。

## 显示约定

原图配色完整保留；不使用颜色区分评分等级，不显示高/中/不足等级。score 为0至1的规则证据排名分数，1.00也不代表百分之百地图变化。关联仅表示时空接近，不代表因果。车辆轨迹来自已有 nuScenes 标注重建。

## 文件及重现

- `scene-0980_results_enhanced.rrd`：可单独打开的完整增强版，包含原记录图像和几何。
- `scene-0980_results_overlay.rrd`：增量图层，使用与原记录相同的应用和记录ID。
- `validation.json`：原始文件哈希、时间对齐、几何/颜色/相机保留检查，以及标签数量和前缀校验。

工作区执行 `python pipeline/visualize_results.py` 重新生成。可视化组件版本固定为 rerun-sdk 0.22.1；工作区运行组件已准备。

原始 `Nuscnenes已处理/processed/processed/scene-0980_discrepancy_full_v2.rrd` 不修改。
