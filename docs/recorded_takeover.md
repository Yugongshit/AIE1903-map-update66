# 真实记录回放与车辆接管（2026-11-16 开发入口）

本轮方向覆盖此前随机交通优先级：先做真实道路/参与者恢复、原轨迹回放、逐车外部接管和分支对照。旧随机交通和长期停滞诊断保留，不把它当本轮主线。

## 当前完成与入口

新增 `map_update/recorded_takeover.py`，复用现有 MetaDrive/Bullet、`native_environment`、动作校验及物理接触读取。直接读取可信的 ScenarioNet 数据库；数据库为 pickle，只使用可信来源，不能上传任意文件到公网执行。独立 Python 开发入口，**尚未接到现有网页/HTTP房间，也尚未接入同学真实 Planning**。

当前工作区 Python 3.11 + MetaDrive 0.4.3 已验证。安装时官方 MetaDrive 0.4.3 assets 包自带三个 Waymo 转换样例，之前的平台场景目录没有接入它们。不是本轮取得原始 WOMD 下载授权、不是新转换的 TFRecord，不是独立测试。

```bash
cd /workspace/AIE1903-map-update66
source .venv/bin/activate
export MPLCONFIGDIR=/workspace/.cache/matplotlib
python scripts/check_recorded_takeover.py \
  --database 'AIE1903 原始资料/simulation/metadrive/metadrive/assets/waymo' \
  --manifest demo/recorded-takeover/development-manifest.json \
  --out outputs/recorded-check-new
```

脚本拒绝覆盖输出。先检查冻结样例哈希，然后运行完整原记录；另起独立世界，在同一第10帧分别接管一辆非采集车、两辆非采集车、采集车。测试刺激只按接管时当前速度选择：速度>1m/s用固定刹车，其余用0.3油门；不读取未来轨迹选动作，不是规划算法。仍要求所有受控车与原轨迹偏差>1cm，低速油门车另要求实际位移>5cm。原地停车时刹车不产生偏差，旧的全刹车断言不能作为通用接管验收。原记录分支和接管前世界的当前状态必须逐项一致；接管只更换策略，不销毁/重建实体或重设位姿/速度。每次动作批次必须覆盖所有已接管车辆且对应当前帧，全部校验后只推进一个统一0.1秒物理步。

```python
from map_update.recorded_takeover import RecordedTakeoverSession
session = RecordedTakeoverSession(trusted_database_path, index=0)
try:
    observation = session.observe()       # 当前对象状态、当前信号；不推进
    map_features = session.map_input()    # 单独复制静态道路地图
    session.takeover([source_track_id])   # 当前有效、地图包围范围内的原记录车辆
    observation = session.observe()
    # 交给同学的外部 Planning；这里不实现 Planning。
    actions = {source_track_id: [0.0, -1.0]}  # 仅接线测试
    next_observation = session.advance(actions, observation_step=observation['step'])
finally:
    session.close()
```

`observe()` 的显式字段白名单不含未来轨迹、未来信号、其他车辆未提交动作或事件日程。它是理想全局当前状态，不是传感器可见性模型。`scenario`、`replay_error()`、分支真值和 `contacts()` 属于环境/评价端，不交给 Planning。后续 HTTP/进程隔离仍需实现；Python 同进程不是安全隔离沙箱。

## 模式与生命周期边界

- 默认为 `recorded_replay`：所有可由 MetaDrive 表达、当前有效的参与者按记录回放，关闭 reactive traffic 和重叠过滤，不暗中避让。每帧集合可能随有效性变化；不把211条完整片段轨迹当作211辆同时在场车辆。
- 车辆接管后为 `external`，动作作用于同一原生物理实体。行人/骑行者本版不支持车辆控制。未来可响应背景策略应另设模式，本版没有加入。
- 回放车辆不响应反事实动作；Bullet 仍可能接触、产生冲量，随后回放策略恢复该回放车的记录位置。不能将这种非响应碰撞全部判为 Planning 失败。报告保留双方模式及逐帧接触，接触行数不等于独立碰撞次数。
- `return_to_replay()` 明确拒绝。首版通过重新开启原记录世界、推进到同一接管帧作对照，验证起点相同；`branch` 是起点观测和重建描述，不是可恢复任意闭环状态的 Bullet 检查点。不支持已发生反事实运动后的热切回。
- 原记录结束、采集车/受控车下一帧失效或轨迹缺口：在推进前停止整条分支。未接管对象按引擎有效性进出场。没有补造缺失状态。
- 本版地图运行范围只检查已记录地图几何的 XY 包围范围（接管前和每步后），不是精确可行驶面/地图覆盖多边形。越出范围停止后续步进；范围内部空洞和驶离道路的精细判定待补。首次越界的物理步保留，不回滚/瞬移。
- 仅支持约10Hz记录（相邻源时间允许1毫秒采样抖动）；`recorded_time_s` 与固定步长 `simulation_time_s` 分开报告，不假称原始时间严格等间隔。
- 接管后属于真实数据初始化的反事实仿真。换车不会获得该车真实相机/LiDAR；将来新增渲染或点云必须标为仿真。道路矢量/通用物体不代表真实建筑恢复。
- 地图输入配对实验尚未实施。后续应固定起点、参与者模式、随机种子和演化规则，只替换传给 Planning 的地图；不能修改物理道路再声称仅输入地图有差异。

## 数据获取与适配核验

2026-11-16：直接请求 WOMD 和 INTERACTION 官网均被当前环境网络代理以403拒绝，不能据此推断数据网站不公开或账号无权限。已读取官方 GitHub 文档：

- [Waymo 官方仓库](https://github.com/waymo-research/waymo-open-dataset)：代码 Apache 许可与数据许可分开，数据须遵守 [Waymo 数据条款](https://waymo.com/open/terms/)。当前用户尚未获得下载访问/未下载小样本；没有代替用户接受条款。官网当前表单、版本、访问要求还需可访问页面确认。
- [ScenarioNet 官方项目](https://github.com/metadriverse/scenarionet)：仓库内 `documentation/waymo.rst` 指向 WOMD v1.2.0 bucket、`training_20s`/validation/testing 和 TFRecord 转换。`convert_waymo` 支持 `--num_files 1 --num_workers 1`，默认转换版本标签 v1.2；不能把它当作当前最新数据版本。vendored 转换器需要 TensorFlow 和 protobuf；旧文档固定 TensorFlow 2.11，与当前 Python 3.11 不匹配，应独立 Python 3.10 转换环境或先验证新版组合。本轮未安装转换环境/未执行原始 TFRecord 转换。
- 三个官方资源样例均91帧，存储格式标签 `MetaDrive v0.3.0.1`，本轮实际由0.4.3引擎读入成功。标签不是原始 WOMD 发布版本；原始 release 未核实。当前选用 `2a1e44d405a6833f`：211条全片段轨迹、358地图要素、8个信号条目；0帧实际有效对象65个。
- [INTERACTION 官方工具仓库](https://github.com/interaction-dataset/interaction-dataset) 明确将数据访问引向官网，工具BSD许可不能替代数据条款。其 `TestScenarioForScripts` 是工具测试样例，不据此称独立真实场景。当前 vendored ScenarioNet 未发现 INTERACTION 转换器；需要 CSV 轨迹及 Lanelet2 地图的ID、投影、单位、时间戳、有效性和拓扑适配。未下载/未适配。
- inD/rounD 暂后置；本轮没有核验其当前许可/取得数据。
- [Waymax multi_actors_demo](https://github.com/waymo-research/waymax/blob/main/docs/notebooks/multi_actors_demo.ipynb) 本轮已能读取；暂不引入JAX/迁移。Python外部规划不会自动批量加速。

最小下一批数据：用户在官网完成条款/访问后，仅取一个获准 WOMD shard 中少量完整场景，记录原始发布版本、shard、scene_id、源片段组、SHA256和转换器版本。转换先单进程，不下载传感器大包。数据存储放可访问持久盘；当前 Codex 只有约58G可用，用户500G为另一块宽恒云存储，`mapupdate`是算力环境，尚未挂载/登录核验。

`demo/recorded-takeover/development-manifest.json` 冻结三个现有样例及源文件哈希，全部归开发。validation/test为空并明确标为待选，不能宣称完成独立测试划分。获得新数据后以源片段/地点/相邻重叠记录分组，再冻结开发/验证/测试清单；不随机拆帧，不把交付副本或这三个样例作为独立泛化证据。原始数据及pickle不提交GitHub。

## 下一步

1. 首个获准WOMD分片和三个开发场景转换/接管验证已完成（见下方最新用户回传）；后续独立验证/测试仍需按源片段分组冻结清单。
2. 把独立接管入口接入工作台：源ID车辆选择、模式标识、同步观测/动作屏障、原记录对照分支及碰撞解释。保留旧随机交通入口但不默认启动。
3. 接入同学真实 Planning，再验证闭环；补精确地图覆盖、观测质量和多车客户端超时语义。当前不声称网页新增功能或远端部署完成。

## 2026-11-16：真实新场景反馈与验证器修正

用户已取得WOMD访问许可，并在宽恒云上传V1.3.1 scenario/training_20s首个分片（SHA256 `7e4162b2eff3e597124e13405d3a783976453865aba0021dc1416ea904432a6f`）。据用户回传终端输出：75个场景可解析，前三个场景各198帧/19.7秒，已转换为持久盘 `/ai/map_update/waymo/converted/v1.3.1-dev-first3`。此处是用户提供的运行证据，Codex没有直接登录该实例。原始文件、子集和转换目录保留，三个场景均属开发，不能称独立测试。

- `41600af30ab8cc55`：旧版28项通过；原回放与背景误差0；128/130接管刹车偏差约7.093/10.302m，采集车920约0.060m；短时分支接触记录0。
- `2aa43fad083efbf3` 与 `eb073968f66914c7`：原回放误差0、单/双非采集车分支通过；各有34/24行逐帧接触记录，不是独立碰撞次数。两者采集车分支在旧版“刹车必偏离>1cm”处失败，不能宣称通过。旧版在该断言之后才追加报告，丢失了失败采集车的速度和偏差信息，实际原因需更新后重跑确认。

修正仅涉及开发验证器，不修改引擎/真实数据/碰撞规则：低速车使用显式测试油门、保留偏差断言并加实际位移检查。所有分支在执行前注册到报告，逐帧记录受控对象，最终偏差和原记录路径长度在断言前保存；失败列表和异常也落盘。合成回归将官方示例采集车前31帧固定静止（明确不是实测数据），复现旧断言失败；新测试通过，并用油门被替换为零的故障注入确认仍会失败。真实新WOMD两场景仍等待用户重跑结果。


## 宽恒云真实场景重跑通过（用户回传，2026-11-16）

更新验证器99649ac后，两场景均 `passed=true`、失败项为空，三个开发场景至此都通过各自接口验证。详细摘要见 [用户回传重跑数据](../demo/recorded-takeover/kuanheng-user-recheck.json)。远端原报告目录 `/ai/map_update/waymo/runs/recheck-20261116-190748`；尚未取得原报告文件，以下仅复述用户终端输出。

| 场景 | 采集车ID | 接管初速 m/s | 测试动作 | 偏离原轨迹 m | 该分支接触记录行数 |
| --- | --- | --- | --- | --- | --- |
| 2aa43fad083efbf3 | 2883 | 0.000679 | [0, 0.3] | 1.713862 | 26 |
| eb073968f66914c7 | 1331 | 0.000806 | [0, 0.3] | 1.698048 | 0 |

两车初速均接近零，说明旧的“刹车必须改变轨迹”条件不适合作为这些起点的接管刺激。新测试不是降低门槛或改动真实轨迹。单车/双车非采集车分支仍使用固定刹车，所有分支背景回放误差为0。scene1单/双车分支接触行数34/34，scene2为24/24；保留这些现象，不能把行数当独立碰撞次数或默认归咎Planning。接触可能涉及回放参与者，具体归因需要完整接触记录。

环境排查和本批基本测试到此结束，下一步是工作台接入，不再重复安装/转换。当前尚未实现新场景网页展示或接入同学真实Planning。
