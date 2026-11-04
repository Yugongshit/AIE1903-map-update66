# 中文与英文网站

默认入口打开英文版；两个独立入口为 `/en/index.html` 和 `/zh/index.html`。每个入口都有自己的证据复核页、问题清单 JSON 和 Word 下载。原始视频使用共同媒体文件，不重绘视频中的已有标注。

英文版的物体名称直接使用交付数据的 `category`，例如 `vehicle.car`、`movable_object.barrier`、`human.pedestrian.construction_worker`。中文版使用对应的中文显示名称。两者显示相同实例和几何；`#001` 等为浏览器展示用编号，原始实例 token 在标注详情中完整保留。

语言转换不改写 `real`、`camera` 或 `write_experiment` 数据块。类别、坐标、尺寸、旋转、来源 token、M3 结论和评估结果保持一致。对成员交付的说明文字，仅在显示时翻译；问题清单同时保留 `source_reference_original`。受控试验的英文导出翻译假设说明，不改变试验输入或写入结果。

页面右上角可切换语言。场景页保留当前帧和选中物体；证据页保留候选、帧和标签页。个人复核草稿沿用原来的同源 localStorage 键，两种语言不会生成互不相通的复核记录。原来的 `/review/index.html#case=...` 链接转到英文证据页，并保留定位参数。

## 复现

先按 `docs/visual_studio.md` 生成场景网站并运行物体标注检查，然后生成双语发布包：

```bash
python3 scripts/build_bilingual_studio.py \
  --source outputs/nuscenes/scene-0980/object-labels-release-v2-20261104 \
  --out outputs/nuscenes/scene-0980/bilingual-new
python3 scripts/check_bilingual_studio.py outputs/nuscenes/scene-0980/bilingual-new
```

输出目录必须是新目录。开发用浏览器截图和检查文件不发布；发布根目录入口、`en/`、`zh/`、共用 `assets/` 及版本信息即可。部署时保留已有资产下载地址。翻译表位于 `map_update/website_en.tsv`；编译器在翻译模板前提取并保护原始 JSON，页面不依赖外部翻译服务或 CDN。

验证覆盖：英文原类别不截断、中文对应关系、两版完整数据块一致、语言切换定位、共享复核草稿、旧链接、视频、写入与回退、英文 Word、英文评估页、手机布局和浏览器错误。
