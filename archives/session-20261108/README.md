# 2026-11-08 会话工作文件归档

当前进度和事实边界请先读 [CONTINUE_HERE.md](../../CONTINUE_HERE.md)。这里保存历史内容，旧草稿和失败/过渡版本没有被冒充为最新版。

归档覆盖 **1,449 个现有文件**（原文件约 644.9 MB，压缩后约 290.8 MB），分为 61 个独立 ZIP。包括项目全部 1,229 个 outputs 文件、现存用户附件、旧下载、生成图片、备份和未提交根目录快照。原始数据和当前源码已在 Git 中。

排除环境生成的缓存、虚拟环境、Git 内部文件和凭据。没有声称保存从未成功读取到的原图、完整录制或其他文件。

## 先恢复当前成果

```bash
python3 scripts/restore_session_archive.py --list
python3 scripts/restore_session_archive.py \
  --group outputs/nuscenes/scene-0980/decision-release-v2-20261106 \
  --group outputs/nuscenes/scene-0980/rerun-release-20261105 \
  --destination .
```

完整恢复到独立目录：

```bash
python3 scripts/restore_session_archive.py --destination restored_session
```

脚本先验证 ZIP 及每个文件的 SHA256；内容相同的现有文件跳过，内容不同的文件拒绝覆盖。支持多个 `--group`；目录前缀也可匹配，例如 `--group outputs/team_proposal` 不会匹配名称前缀，只匹配目录层级，请使用下表精确组名。仅验证可使用 `--verify-only`。

若手动下载 ZIP，可直接解压，内部保留相对目录。多分片组需要下载该组的所有 ZIP；每片都是可单独解压的标准 ZIP，不是需要额外工具拼接的拆分压缩格式。

## 哪些是当前，哪些是历史

- 当前应用：`outputs/nuscenes/scene-0980/decision-release-v2-20261106`。
- 当前构建输入：`outputs/nuscenes/scene-0980/rerun-release-20261105`。
- 当前下载包：`outputs/downloads-20261106`。
- 旧版 Word / PPT / 讲稿：`outputs/evidence_presentation`、`outputs/map_maintenance_five_sections`、`outputs/shared_map_*`、`outputs/team_proposal_*`。这些文件按原样保留，本次没有重写内容。
- 旧演示和各种 v1/v2：供审计和恢复，不应自动成为新工作的起点。
- `historical_root_working_copy`：旧根目录的 146 个未提交或修改文件快照。部分制作脚本只在这里存在；旧 Rerun 和检查代码不能覆盖当前实现。
- `workspace_inputs/attachments`：这次会话中实际可读到的附件，包括原文档、CSV、脚本、原视频和原 overlay RRD。
- `session_tools`：此前下载包制作脚本与本次归档脚本，保留原路径设置；作为制作过程记录，不保证在别的路径直接运行。

[完整文件清单及 SHA256](manifest.json) · [恢复与保护检查](archive_validation.json)

## 目录索引

| 归档组 | 文件数 | 还原大小 | ZIP 文件 |
| --- | ---: | ---: | --- |
| `historical_root_working_copy` | 146 | 1.86 MB | [分片 1](parts/001_part01.zip) |
| `outputs/demo` | 7 | 0.15 MB | [分片 1](parts/002_part01.zip) |
| `outputs/downloads-20261106` | 158 | 78.79 MB | [分片 1](parts/003_part01.zip) · [分片 2](parts/003_part02.zip) |
| `outputs/evidence_demo_20261103` | 6 | 2.07 MB | [分片 1](parts/004_part01.zip) |
| `outputs/evidence_demo_20261103_final` | 5 | 1.90 MB | [分片 1](parts/005_part01.zip) |
| `outputs/evidence_demo_20261103_v2` | 3 | 1.48 MB | [分片 1](parts/006_part01.zip) |
| `outputs/evidence_demo_ready_20261103` | 36 | 8.96 MB | [分片 1](parts/007_part01.zip) |
| `outputs/evidence_presentation` | 43 | 8.35 MB | [分片 1](parts/008_part01.zip) |
| `outputs/evidence_workbench_20261104` | 5 | 2.00 MB | [分片 1](parts/009_part01.zip) |
| `outputs/evidence_workbench_ready_20261104` | 7 | 2.25 MB | [分片 1](parts/010_part01.zip) |
| `outputs/evidence_workbench_release_20261104` | 8 | 2.26 MB | [分片 1](parts/011_part01.zip) |
| `outputs/evidence_workbench_v2_20261104` | 8 | 2.25 MB | [分片 1](parts/012_part01.zip) |
| `outputs/from-files` | 8 | 0.53 MB | [分片 1](parts/013_part01.zip) |
| `outputs/full-reproduction` | 25 | 0.42 MB | [分片 1](parts/014_part01.zip) |
| `outputs/full-reproduction-verified` | 25 | 0.42 MB | [分片 1](parts/015_part01.zip) |
| `outputs/m3_handoff_20261103` | 11 | 1.38 MB | [分片 1](parts/016_part01.zip) |
| `outputs/m3_integrated_20261103` | 11 | 10.89 MB | [分片 1](parts/017_part01.zip) |
| `outputs/m3_reproduction_20261103` | 15 | 0.23 MB | [分片 1](parts/018_part01.zip) |
| `outputs/map_maintenance_five_sections` | 34 | 5.47 MB | [分片 1](parts/019_part01.zip) |
| `outputs/material-review-6c5d134` | 2 | 0.02 MB | [分片 1](parts/020_part01.zip) |
| `outputs/normalized-run` | 7 | 0.04 MB | [分片 1](parts/021_part01.zip) |
| `outputs/normalized.json` | 1 | 0.00 MB | [分片 1](parts/022_part01.zip) |
| `outputs/nuscenes/scene-0980/bilingual-release-20261104` | 32 | 20.76 MB | [分片 1](parts/023_part01.zip) |
| `outputs/nuscenes/scene-0980/bilingual-studio-v1-20261104` | 30 | 22.34 MB | [分片 1](parts/024_part01.zip) |
| `outputs/nuscenes/scene-0980/bilingual-studio-v2-20261104` | 30 | 20.75 MB | [分片 1](parts/025_part01.zip) |
| `outputs/nuscenes/scene-0980/dataset-separation-20261104` | 10 | 2.35 MB | [分片 1](parts/026_part01.zip) |
| `outputs/nuscenes/scene-0980/decision-release-20261106` | 74 | 64.17 MB | [分片 1](parts/027_part01.zip) |
| `outputs/nuscenes/scene-0980/decision-release-v2-20261106` | 77 | 63.79 MB | [分片 1](parts/028_part01.zip) |
| `outputs/nuscenes/scene-0980/object-labels-release-20261104` | 12 | 11.27 MB | [分片 1](parts/029_part01.zip) |
| `outputs/nuscenes/scene-0980/object-labels-release-v2-20261104` | 23 | 13.10 MB | [分片 1](parts/030_part01.zip) |
| `outputs/nuscenes/scene-0980/object-labels-v1` | 12 | 11.27 MB | [分片 1](parts/031_part01.zip) |
| `outputs/nuscenes/scene-0980/object-labels-v2` | 17 | 12.53 MB | [分片 1](parts/032_part01.zip) |
| `outputs/nuscenes/scene-0980/rerun-recordings-v1-20261105` | 3 | 46.34 MB | [分片 1](parts/033_part01.zip) |
| `outputs/nuscenes/scene-0980/rerun-recordings-v2-20261105` | 3 | 10.58 MB | [分片 1](parts/034_part01.zip) |
| `outputs/nuscenes/scene-0980/rerun-recordings-v3-20261105` | 1 | 5.07 MB | [分片 1](parts/035_part01.zip) |
| `outputs/nuscenes/scene-0980/rerun-recordings-v4-20261105` | 3 | 10.14 MB | [分片 1](parts/036_part01.zip) |
| `outputs/nuscenes/scene-0980/rerun-release-20261105` | 60 | 62.09 MB | [分片 1](parts/037_part01.zip) |
| `outputs/nuscenes/scene-0980/studio-m3-check-20261104` | 15 | 0.23 MB | [分片 1](parts/038_part01.zip) |
| `outputs/nuscenes/scene-0980/studio-upstream-check-20261104` | 25 | 0.42 MB | [分片 1](parts/039_part01.zip) |
| `outputs/nuscenes/scene-0980/visual-studio-release-20261104` | 25 | 10.33 MB | [分片 1](parts/040_part01.zip) |
| `outputs/nuscenes/scene-0980/visual-studio-v1` | 16 | 9.59 MB | [分片 1](parts/041_part01.zip) |
| `outputs/nuscenes/scene-0980/visual-studio-v2` | 17 | 9.56 MB | [分片 1](parts/042_part01.zip) |
| `outputs/project_demo_v1` | 103 | 27.47 MB | [分片 1](parts/043_part01.zip) |
| `outputs/readiness-2026-11-03` | 2 | 0.21 MB | [分片 1](parts/044_part01.zip) |
| `outputs/restored` | 7 | 0.17 MB | [分片 1](parts/045_part01.zip) |
| `outputs/scene0980-intake` | 6 | 2.03 MB | [分片 1](parts/046_part01.zip) |
| `outputs/scene0980-map-replay` | 8 | 7.58 MB | [分片 1](parts/047_part01.zip) |
| `outputs/shared_map_revision` | 33 | 6.54 MB | [分片 1](parts/048_part01.zip) |
| `outputs/shared_map_structured` | 33 | 5.85 MB | [分片 1](parts/049_part01.zip) |
| `outputs/team_proposal_clean_architecture` | 22 | 7.83 MB | [分片 1](parts/050_part01.zip) |
| `outputs/team_proposal_datasets_models` | 22 | 7.05 MB | [分片 1](parts/051_part01.zip) |
| `outputs/team_proposal_final_evidence` | 20 | 7.56 MB | [分片 1](parts/052_part01.zip) |
| `outputs/team_proposal_focused` | 9 | 2.82 MB | [分片 1](parts/053_part01.zip) |
| `outputs/team_proposal_revision` | 47 | 11.55 MB | [分片 1](parts/054_part01.zip) |
| `outputs/upstream-verified` | 7 | 0.03 MB | [分片 1](parts/055_part01.zip) |
| `session_tools` | 2 | 0.02 MB | [分片 1](parts/056_part01.zip) |
| `workspace_inputs/attachments` | 61 | 6.01 MB | [分片 1](parts/057_part01.zip) |
| `workspace_inputs/downloads` | 5 | 8.03 MB | [分片 1](parts/058_part01.zip) |
| `workspace_inputs/generated_images` | 4 | 5.58 MB | [分片 1](parts/059_part01.zip) |
| `workspace_inputs/scratch` | 2 | 0.21 MB | [分片 1](parts/060_part01.zip) |
