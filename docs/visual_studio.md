# 场景、改图与周末问题清单

2026-11-04。入口：https://yugongshit.github.io/AIE1903-map-update66/

本轮继续使用 nuScenes / scene-0980，Argoverse 待审核。成员原始数据保持不变。

## 为什么重做

原 PPT 第 2–3 页承诺地图前后对比、判断原因、版本记录与回退。旧工作台主要展示候选和统计，无法清楚呈现地图维护动作。新版增加驾驶视角、真实事件层变化与受控地图写入的完整展示；保留原证据复核工作台，并兼容旧 `#case=` 分享链接。

## 场景驾驶

- 左侧前视重建来自相机标定、相机曝光时的 ego pose、原地图几何、感知标线和 nuScenes 物体标注。画面是几何投影，不是原始照片。
- 41 个相机 keyframe 通过 sample_token 对应场景帧，再使用该相机的 ego_pose_token、calibrated_sensor_token 和内参。地图按 z=0 地平面处理；物体采用标注帧的完整三维尺寸、旋转与中心。近 / 远平面和图像边界经过裁剪，不将相机后的点翻转到前方。
- CAM_FRONT 与参考帧最大相差 37.609 ms。相机位置使用曝光时的位姿；物体仍位于原标注时刻，未补偿动态物体运动。没有遮挡消隐、坡度建模或物理重投影精度结论。
- 青色路线来自原 ego 位置。它是实际行驶记录，包括事后知道的未来段，不能用于宣称在线决策或安全路线推荐。
- 支持时间轴、逐帧、变速、图层开关、鸟瞰拖动缩放、跟随 / 全景、候选选择与跳转到原证据页。
- 可以在参考车道内设定目标并导出 `planning-request`。它只有起点、目标、决策时间、地图身份与输入需求，`route=null`，不是规划结果。
- 包含用户先前提供的真实感知 MP4，使用原生视频播放器。录屏有起始停顿，未按视频播放时间冒充采集时间轴的精确同步。

## 相机照片交付状态

当前 GitHub 有 41 帧 CAM_FRONT 的元数据，没有对应 JPG。先前上传的完整 `scene-0980_results_enhanced.rrd` 超过文件服务 32 MiB 传输上限，本轮下载失败；工作区可读的 `overlay.rrd` 只有增量图层。上游 README 明确区分完整增强记录与增量层，因此不能把后者当成照片来源。

浏览器支持选择本地 CAM_FRONT 图片文件夹，按已知原文件名匹配并检查 1600×900 分辨率。图片只在访问者浏览器读取，不上传，不写入仓库。缺图的帧显示缺失，不沿用上一帧照片。文件名与分辨率检查不能独立证明图片内容真实，仍须人工核查。刷新页面后需重新选择本地文件夹。

## 地图修改

**真实维护结果：**原基础地图修改 0。23 个占用事件候选保存到事件层；可以切换原参考图与逐帧事件层，导出带来源的实际记录。缺测不等于恢复，不能由占用候选推导永久封路。

**受控改图试验：**从真实参考地图复制一条标线到独立试验地图，分别人为平移 0.60 / 0.80 / 1.20 米。三次访问的身份、0 / 12 / 26 小时时间、95% 有效覆盖与对齐通过状态均是试验输入，不是新收到的真实证据。

每个变体都由现有 `Maintainer` 计算：初始、一次、两次、三次访问、回退五个阶段。达到默认门槛后生成 `geometry_change` patch；回退恢复原几何并追加版本。网页显示的是这些实际计算结果，并不在浏览器伪造“成功”返回。交互用于选择已计算的试验阶段与偏移，没有向真实地图服务发送写入。

支持拖动前后对照分隔线、查看具体 element_id、偏移、版本、支持记录及导出完整引擎结果。实验命名空间与真实结果隔离，真实 `base_map_updates` 保持为空。

该试验验证机制，不证明真实路段发生了给定偏移，不提供真实误更新率或行驶可行性。

## 周末问题清单

网站第三页列出 11 项问题，其中 6 项 P0。每项都含已核实的现象、影响、协作方、下一步、验收与来源。可直接下载 Word；JSON / Markdown 便于成员协作。

核心缺口：相机素材可读取性、覆盖率与物理对齐、LD 目标对应、独立复访与地图时间角色、Planning 结果、独立真实标签。另需处理条件 F1 与拒判覆盖率的共同报告、未来信息泄漏、投影近似、可移植入口及 PPT 口径。

## 复现

```bash
python3 scripts/build_visual_studio.py \
  --out outputs/nuscenes/scene-0980/visual-studio-new \
  --perception-video /path/to/original-perception-recording.mp4
python3 scripts/check_visual_studio.py outputs/nuscenes/scene-0980/visual-studio-new
python3 scripts/check_workbench.py outputs/nuscenes/scene-0980/visual-studio-new/review
python3 -m unittest discover -s tests -q
```

构建 Word 需 `python-docx`；浏览器检查需 Playwright、Chromium、Pillow。运行核心投影与地图试验使用 Python 标准库；发布网站无需第三方 CDN。新目录不存在时才会构建，失败时清理临时目录，不覆盖已有结果。

本轮重新运行上游规则与 M3：17 个 stage3/5/6 文件结构化内容完全一致；stage7 的 30 项内置检查通过；M3 的 13 个 CSV / JSON 与交付一致，原文件未改动。这没有重跑 YOLOP 相机模型，也不等于真实准确率验证。

源码：`map_update/camera.py`、`studio.py`、`studio.html`、`studio.css`、`studio.js`。旧的证据和个人复核入口位于 `/review/index.html`；本地复核存储仍使用原数据集 / 场景 / 地图哈希键。
