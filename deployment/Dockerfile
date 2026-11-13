# syntax=docker/dockerfile:1
FROM python:3.10-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LIBGL_ALWAYS_SOFTWARE=1 \
    SDL_VIDEODRIVER=dummy
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libegl1 libgl1 libgl1-mesa-dri libglib2.0-0 libgomp1 \
    libsm6 libxext6 libxrender1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-simulation.txt ./
COPY ["AIE1903 原始资料/simulation/metadrive", "AIE1903 原始资料/simulation/metadrive"]
COPY ["AIE1903 原始资料/simulation/scenarionet", "AIE1903 原始资料/simulation/scenarionet"]
# Optional BuildKit CA mount supports managed build proxies without retaining
# session credentials or certificates in image layers. Normal servers omit it.
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then \
      export PIP_CERT=/run/secrets/proxy_ca SSL_CERT_FILE=/run/secrets/proxy_ca \
             REQUESTS_CA_BUNDLE=/run/secrets/proxy_ca; \
    fi; \
    python -m pip install --upgrade pip 'setuptools<81' wheel \
    && python -m pip install -r requirements-simulation.txt \
    && python -m metadrive.pull_asset

COPY . ./
RUN chmod -R a+rX /app && mkdir -p /app/outputs && chown 10001:10001 /app/outputs
USER 10001:10001
EXPOSE 8091
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8091/api/config', timeout=4).read()"
CMD ["python", "scripts/run_simulation_platform.py", "--host", "0.0.0.0", "--port", "8091", "--no-browser", "--control-key-env", "SIMULATION_CONTROL_KEY", "--allow-origin", "https://yugongshit.github.io"]
