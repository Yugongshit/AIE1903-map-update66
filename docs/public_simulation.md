# 公网地图审阅与多人仿真部署

公网审阅入口：<https://yugongshit.github.io/AIE1903-map-update66/simulation/>。

同伴打开链接即可选择 nuScenes / Argoverse 场景、查看道路前后变化、旋转三维视角、拖动记录时间，并导出对应时刻的地图与状态。静态障碍和变化标记从开场完整保留。它使用已校验的源几何与记录数据，不生成缺失的真实建筑模型。

GitHub Pages 提供地图网页和数据文件。玩家输入、物理碰撞及外部 Planning 的控制循环需要持续运行的 MetaDrive 后端。没有后端时仍可完整审阅地图，页面会明确提示实时交互尚未连接，不会用网页动画冒充原生物理仿真。

## 与同伴一起使用

部署下述后端后，在网页的服务器地址处填写其 HTTPS 地址并连接。主持人使用服务器控制密钥创建交互场景，再将页面生成的房间邀请链接分享给同伴。不要把服务器控制密钥放到分享链接、GitHub 仓库或群聊中。

网页右侧 **驾驶控制 → 交互交通** 有五个可交互车辆席位 P1–P5，另有主规划车 ego。控制区与地图并排，切换或展开它不会缩小地图高度；小屏幕上位于地图下方。玩家领取一个空闲席位后，用 WASD / 方向键控制；无人接管的席位由内置 AI 驾驶，也可交给外部模块。已经有人驾驶的席位不能由另一位玩家直接抢占。具体动作、观察、心跳和外部 Planning 接入格式见[接口说明](interactive_planning_api.md)。

服务器推进同一个物理世界。多个网页查看它不会重复推进仿真；各车的新位置和速度会进入下一次 Planning 观察。Planning 模块如何改变路径与控制动作由接入模块决定。内置 AI 仅用于生成可交互交通，不代表你们同学的完整规划算法。

默认三维视图使用引擎返回的实际车辆位置，每位玩家可独立调整视角。物理更新不必等待 PNG 渲染；切换到 MetaDrive 原生画面后，服务器才持续生成图片，该画面的相机由同房间共享。仿真推进速度取决于服务器性能，不保证与墙钟实时一致。

内置 AI 沿源地图的有效车道连接低速行驶，并对附近车辆制动；有限道路目标完成后会停车，不凭空补道路。它不实现完整交通灯规则，也不承诺永不碰撞。接入同学的 Planning 后，车辆如何避让、选择路线和处理冲突由该模块决定。

## 在 Linux 服务器上部署

使用可以持续运行 Docker 的 Linux 主机。需要可访问的域名和服务器地址；GitHub Pages 本身不提供 Python 或 MetaDrive 进程。示例采用 CPU / Mesa 无窗口渲染，渲染性能取决于服务器硬件。

在仓库根目录复制配置：

```bash
cp deployment/.env.example deployment/.env
```

在服务器上编辑 `deployment/.env`，设置 `SIMULATION_DOMAIN` 为指向服务器的域名，`SIMULATION_CONTROL_KEY` 为随机生成的长密钥。可以用 `openssl rand -hex 32` 生成密钥。`.env` 已被忽略，不会提交到 GitHub。

首次构建会安装 Python 3.10、原仓库 MetaDrive / ScenarioNet 依赖、Mesa，并从 MetaDrive 官方发布下载模型资源。仓库没有提交这些模型二进制文件，因此新服务器必须完成这次下载。

有域名并已让 DNS 指向服务器时，在防火墙允许 80/443 入站后启动：

```bash
docker compose --env-file deployment/.env \
  -f deployment/compose.yaml -f deployment/compose.public.yaml up --build -d
```

Caddy 申请 HTTPS 证书并代理仿真服务。访问 `https://你的域名/` 可以直接使用同源网页，也可以在 GitHub Pages 页面连接这个地址。默认允许的跨域网页来源是 `https://yugongshit.github.io`；如更换前端域名，应在服务启动参数中显式增加 `--allow-origin`。

已有 HTTPS 反向代理时，仅启动应用：

```bash
docker compose --env-file deployment/.env -f deployment/compose.yaml up --build -d
```

应用端口默认只绑定服务器本机 `127.0.0.1:8091`，由现有 HTTPS 代理转发。不要把公网 HTTPS 网页指向不加密的 HTTP 地址，浏览器会阻止混合内容请求。

检查服务状态和日志：

```bash
docker compose --env-file deployment/.env -f deployment/compose.yaml ps
docker compose --env-file deployment/.env -f deployment/compose.yaml logs --tail 100 simulation
```

`GET /api/config` 可以确认服务进程启动；一次真实场景启动才会验证该服务器的原生渲染能力。多人共享一个房间的时钟与道路版本；当前部署不是多房间云调度系统。

## 不使用 Docker

已经装好仿真依赖的服务器也可直接运行。先通过服务器的环境配置设置 `SIMULATION_CONTROL_KEY`，然后启动：

```bash
python scripts/run_simulation_platform.py --host 0.0.0.0 --port 8091 \
  --control-key-env SIMULATION_CONTROL_KEY \
  --allow-origin https://yugongshit.github.io --no-browser
```

公网入口仍应由 HTTPS 反向代理提供。原来的本机启动方式保持不变：

```bash
python scripts/run_simulation_platform.py
```

## 重建 GitHub Pages 审阅文件

在已安装 `numpy` 和 `shapely` 的项目环境运行：

```bash
python scripts/build_public_simulation.py --output outputs/public-simulation
```

将输出目录内容发布到 **gh-pages 分支的 `simulation/` 子目录**；保留已有 `algorithms/`、`review/` 和中英文旧入口。`main` 保存源码，不需要把模型、虚拟环境或运行中的服务器数据发布到 Pages。

构建过程会验证源地图、状态和评估产物的绑定关系。网页中的完整记录只用于离线审阅；Planning 下载与本地 Python `planning_bundle()` 保持同一数据契约，仅提供所选帧已观测的信息。页面固定显示的全场景状态概览不会混入该帧导出。

构建产物使用 gzip 分块保存记录，需要支持 `DecompressionStream` 的现代浏览器（当前 Chrome、Edge、Firefox、Safari）。地图全量文件按场景保存，原图与更新图共享同一份记录时间与参与者快照，避免重复下载整张地图。

纯静态浏览器检查：

```bash
python -m http.server 8112 --directory outputs/public-simulation
python scripts/check_public_simulation.py --url http://127.0.0.1:8112/ \
  --out outputs/public-simulation-checks
```

检查脚本需要 Playwright 和 Chromium，并比较公网 Planning 导出与原 Python 导出的完整内容。它不启动 MetaDrive，因此可以验证地图审阅确实不依赖后端。

## 验证记录

[交互平台验证报告](../demo/simulation/validation/interactive-platform.json) 保存了两种源地图的指纹、真实物理检查、三个独立浏览器联调结果，以及 Docker 中实际启动六车和生成 PNG 的结果。运行测试的外部控制器只用于验证接口，不是你们同学的规划模块，也不能据此宣称任意驾驶行为都不会碰撞。

后续修复的[延迟对照](../demo/simulation/validation/interactive-latency.json)记录了三观众负载下的实际 HTTP 与物理步进数据；[碰撞对照](../demo/simulation/validation/interactive-collision.json)记录相同初态和动作序列下固定路障修复前后的真实响应，以及原记录回放回归。云端测试结果不等于所有客户端的帧率或所有撞击工况的保证。

真实物理检查使用 `scripts/validate_interactive_simulation.py`；多人浏览器检查使用 `scripts/check_interactive_platform.py`，运行后者前先启动本机平台服务。原记录回放与交互房间分开验证，默认回放仍按源轨迹运行。
