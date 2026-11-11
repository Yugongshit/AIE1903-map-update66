# 随仓库提供的仿真地图

这两份数据库让本机拉取代码后直接运行，无需访问云端文件或下载额外压缩包。
原始数据仍在 `AIE1903 原始资料/simulation/converted/scene-0980/`。

| 目录 | 用途 | 道路结构修改 | 障碍策略 |
| --- | --- | --- | --- |
| `highlighted/` | 检查地图更新与标注的受控演示 | 一条标线沿城市 X 移动 1 米，明确标 DEMO | 路障/锥桶首次出现后持续保留 |
| `persistent/` | 查看真实算法输出 | 0 项；23 个状态事件保持原语义 | 路障/锥桶首次出现后持续保留 |

已有 `aie1903-sim` 环境时，在项目根目录执行：

```bash
conda activate aie1903-sim
python -m pip install "numpy==2.2.6" "shapely>=2.1,<3"
python scripts/run_updated_simulation.py --database-path demo/simulation/highlighted --mode manual --render 3D --no-traffic --steps 6000
```

Mac 本机不加 `--offscreen`。点击窗口后使用 W/A/S/D；Esc 退出。
首次安装、导出自己的地图及控制器接入见 [仿真说明](../../docs/simulation_bridge.md)。

每份目录保留完整导出结果，包括场景、索引、前后地图、规划地图、更新日志、
状态事件、障碍清单和哈希审计。场景索引相对本目录解析，补面读取仓库内
原始地图，不依赖云端路径。示例包含 pickle，仅加载可信数据。

持久化是一项显式仿真策略，不证明现实障碍永久存在；DEMO 修改不是算法检测成果。
当前源地图没有建筑轮廓和高度，未知周边留空，不生成虚构楼房或山地。
