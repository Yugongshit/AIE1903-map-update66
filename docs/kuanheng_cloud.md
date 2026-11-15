# 宽恒云选择与验证

本轮依据用户提供的配置截图，尚未收到可读取的快速入门 PDF，也未登录或部署宽恒云。

| 配置 | 建议 |
| --- | --- |
| 镜像页签 | 平台镜像 |
| 镜像卡片 | pytorch-gpu |
| 镜像版本下拉 | `cuda_13.2_26.05-py3`；具体 PyTorch/Python 版本仍须实例内核验 |
| 资源配置页签 | 异构计算GPU |
| 资源规格单选 | `1*GPU*5090`，即 RTX 5090 ×1、24 核 CPU、32GB 主机内存 |
| 节点数 | `1` |
| 存储左侧下拉 | 在用户截图中的 `1` / `data` 中推荐选 `data`；这是已有存储名称，容量与原有内容需在存储详情查看 |
| 存储右侧文本框 | 填 `map_update`；左边已有 `/ai/`，不要重复输入前缀，完整挂载路径 `/ai/map_update` |
| 添加文件存储 / 新建文件存储 | 当前各不再添加；只保留上述一行已有存储挂载 |
| TensorBoard日志 | 否，关闭 |
| 端口名称 | `map-update` |
| 内部端口号 | `8093` |
| 外部端口号 | 首选 `8093`；若平台要求 NodePort 范围或提示占用，改填可用的 `30093`（仍须平台校验通过）；内部端口保持8093 |
| 添加端口号 | 只保留上述一条，不再重复添加 |

Python 3.11 是实例创建后的独立仿真环境要求，不是当前截图中的额外表单项。仓库 ScenarioNet 不支持 Python 3.12，Python 3.8 不满足当前 NumPy 版本。未来训练若使用 RTX 5090，须核验 PyTorch/CUDA 构建支持 Blackwell（例如 CUDA 12.8 或更新的兼容构建），并实际执行 GPU 算子。

另一候选 `1.13.1-cuda11.4-py38` 的 PyTorch/CUDA/Python 过旧，不适合 RTX 5090 和当前依赖。所选镜像的 `py3` 不保证 Python 3.11，因此为仿真单独创建3.11环境；镜像标签本身也不证明GPU算子已经可用。

截图中的“GPU 5 块、CPU 409 核、内存 1155GB”是区域剩余资源，不是本任务分配。32GB 是主机内存，显存应以实例内 `nvidia-smi` 为准。截图未显示存储容量、价格、持久化生命周期和任务入口，不能据此确认。

实例创建后在平台终端核对：

```bash
nvidia-smi
lscpu
free -h
df -h
python --version
```

若使用 PyTorch，检查版本和实际 CUDA 算子：

```python
import torch
print(torch.__version__, torch.version.cuda, torch.cuda.is_available())
print(torch.cuda.get_device_name(0))
x = torch.randn(256, 256, device='cuda')
y = x @ x
torch.cuda.synchronize()
print(y.shape)
```

仿真环境按 `requirements-simulation.txt` 安装，使用 Python 3.11；另执行 `python -m metadrive.pull_asset`。GPU 计算可用不等于 Panda3D 的 EGL 图形渲染可用，还需运行 `scripts/run_updated_simulation.py --offscreen` 验证；查看引擎日志确认渲染后端。开发工作台用 `python scripts/run_simulation_platform.py --port 8093 --no-browser`，跨容器访问需要 `--host 0.0.0.0` 和 `SIMULATION_CONTROL_KEY`，详见 `public_simulation.md`。

当前物理步进和交通规则主要受 CPU 单进程性能影响；增加 GPU 不能解开循环等待。先验证单场景吞吐和内存，再用独立进程并行不同种子；从 2–4 个工作进程起量测，不能仅凭 24 核就开 24 份场景。所有结果注明仿真来源、种子、规则、耗时、碰撞与停滞指标。SUMO 对照及条件 VAE/Transformer 仍是后续候选，没有训练成果，研究对象为背景交通，不替代同学的 ego Planning。

## 填完之后

1. 只保留一个数据挂载和一条服务端口，不必点“添加文件存储”或“添加端口号”重复添加。当前已有存储时不必再新建一份。
2. 提交创建后，等待运行状态就绪，再进入平台提供的终端。外部端口是否允许、是否占用由平台校验；截图没有给出允许范围，不能保证任意指定值可用。
3. 确认 `ls -ld /ai/map_update` 能看到挂载目录，再把仓库克隆到 `/ai/map_update/AIE1903-map-update66`；若该目录已经是本仓库，进入目录执行 `git pull --ff-only origin main`，不要重复克隆或清空已有存储。Python环境、模型缓存、结果和日志也放在持久盘。
4. Python3.11环境内从仓库根目录安装 `requirements-simulation.txt`，再执行 `python -m metadrive.pull_asset`。系统镜像的Python版本可能不同，不能直接把依赖装进原PyTorch环境。
5. 在实例环境中设置随机长字符串 `SIMULATION_CONTROL_KEY`（至少32字符，不发到聊天或GitHub），启动：

```bash
python scripts/run_simulation_platform.py --host 0.0.0.0 --port 8093 --no-browser
```

6. 先在实例终端用 `curl -f http://127.0.0.1:8093/api/config` 检查服务，再打开平台为这一端口提供的访问地址。浏览器不能使用 `0.0.0.0`，Mac上的 `127.0.0.1` 也不是云实例。优先使用平台有认证的HTTPS访问入口；若平台只提供公网IP/裸HTTP，按 `public_simulation.md` 配置HTTPS代理后再传送控制密钥。
7. 在工作台“连接与房间设置”中，若直接打开的是云服务页面，“仿真服务器”留空；“房主启动密钥”填自己设置的 `SIMULATION_CONTROL_KEY`，点击连接，再启动场景。服务终端需要保持运行，不能把GitHub Pages静态地址当作运行中的云仿真后端。

这里只指导配置，没有代用户在宽恒云创建实例、核验存储或部署。持久盘的容量/费用及平台端口协议以实际界面为准。
