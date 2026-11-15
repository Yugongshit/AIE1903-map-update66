# 宽恒云选择与验证

本轮依据用户提供的配置截图，尚未收到可读取的快速入门 PDF，也未登录或部署宽恒云。

| 配置 | 建议 |
| --- | --- |
| 镜像 | pytorch-gpu → `cuda_13.2_26.05-py3`；用户提供的两个候选中选择此项，具体 PyTorch/Python 版本仍须实例内核验 |
| Python | 独立 Python 3.11 环境；仓库 ScenarioNet 不支持 Python 3.12，Python 3.8 不满足当前 NumPy 版本 |
| GPU/CPU/内存 | 截图可选 RTX 5090 ×1、24 核 CPU、32GB 主机内存；先用一个节点 |
| CUDA | 若训练使用 RTX 5090，选择支持 Blackwell 的 PyTorch/CUDA 组合（例如 CUDA 12.8 或更新的兼容构建），实际执行 GPU 算子验证 |
| 存储 | 创建/选择持久文件存储，挂载例如 `/ai/map-update`；仓库、数据、结果、检查点存这里，实际路径以平台为准 |
| TensorBoard | 当前关闭；有真实训练日志后再开 |
| 端口 | 平台需 8093；公网开放前配置平台认证和应用控制密钥，不直接暴露无认证控制接口 |

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
