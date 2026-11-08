# AIE1903 双数据集工作台 — 2026-11-08

nuScenes：[英文](en/index.html#decisions) · [中文](zh/index.html#decisions)

Argoverse：[英文](argoverse/en/index.html) · [中文](argoverse/zh/index.html)

nuScenes 29 个候选自动记录：23 个占用事件、6 个标线差异；当前观测流程没有运行长短期分类器。

Argoverse 实际复跑冻结 M2 的 M3/M4：582 帧，10,278 条观测，4 条逐帧候选，1 个 uncertain 组，0 个确认变化。冻结预测后，已有 M8 地图 GT 用于自动评价：候选命中 0/1，全供给地图 98 个变化中确认检出 0。整图检出不能当作可见区域准确率，也不是长短期分类准确率。

两个数据集独立，不生成当前人工待办。来源代码提交 `219aa537b930dd1eea09e4cbb525c68fe3f5d9f8`，开发分支 `feat/argoverse-rerun`；`main` 中用户的新增原始资料保持原样。

验证见 [release_validation.json](release_validation.json)：121 项核心测试，177 项浏览器检查通过。本地 HTTP/Chromium 验证不等于线上 Pages 缓存刷新核验。

`downloads/` 中 20261106 文件仍是历史交付包；当前 Argoverse 页面可下载完整运行结果 JSON。
