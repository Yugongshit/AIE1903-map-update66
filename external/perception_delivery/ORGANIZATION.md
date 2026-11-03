# 本次整理说明

这是用户上传的一份压缩包的拆分内容。原 README 和脚本均原样保留。不要直接运行 `pipeline/run_all.py`：它依赖尚未上传的 `Nuscnenes已处理/processed/processed` 原始处理目录。

```
perception_delivery/
  README.md                       原交付说明
  manifest.json                   47 个附件的原名称、file_id、目标位置、SHA-256
  pipeline/                       8 个 Python 源文件
  bytecode_archive/               4 个 pyc 原样留存，不参与执行
  evaluation_labels/              3 份空真值标注模板
  outputs/
    stage3/                       29 个候选、332 条帧证据、汇总几何和评分规则
    stage5/                       scene-0980 的 13 条轨迹、13 个行为片段
    stage6/                       175 对关联、1368 条距离证据
    stage7/
      evaluation_report.json      原交付报告
      synthetic_rule_checks/      人工生成的 straight/turn/u_turn/gap/stationary
        track_points.csv
        result/                   6 个合成行为片段，独立保存
    visualization/                原说明、overlay RRD、日志与 validation
```

同名 `summary.json` 按其中 `stage`、记录数和 track ID 归属；两份 stage5 规则虽相同，仍分别保留在真实场景和合成测试目录。`behavior_events.csv` / `vehicle_features.csv` / `behavior_geometry.json` 依据真实 track token 与 stage7 脚本定义的合成 ID 区分，未互相覆盖。

`scene-0980_results_enhanced.rrd` 超过附件传输 32 MiB 上限，未取得；manifest 明确记录。overlay 是增量图层，不能替代缺失的原始地图与六路相机记录。`source_schema.txt` 只有对象 repr，不是可用字段 schema。

从仓库根目录执行 `python3 scripts/verify_upstream.py --out outputs/upstream-check-new` 可在临时副本重跑已提供的 stage7。stage3、stage6 和完整增强 RRD 重建所需原始输入还不齐全。完整结果见 [交付检查记录](../../docs/delivery_findings.md)。
