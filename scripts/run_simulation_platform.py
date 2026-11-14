"""Run the local scene platform with real MetaDrive playback and planning exports."""
import argparse
import os
from pathlib import Path
import sys
import threading
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from map_update.simulation_platform import PlatformServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8091)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--control-key-env', default='SIMULATION_CONTROL_KEY')
    parser.add_argument('--allow-origin', action='append', default=[])
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if not 0 <= args.port < 65536:
        parser.error('port must be between 0 and 65535')
    try:
        server = PlatformServer((args.host, args.port), ROOT,
                                control_key=os.environ.get(args.control_key_env), allowed_origins=args.allow_origin)
    except (OSError, ValueError) as error:
        parser.exit(2, f'无法启动平台：{error}；可用 --port 8092 选择其他端口。\n')
    url = f'http://{args.host if args.host not in {"0.0.0.0", "::"} else "127.0.0.1"}:{server.server_port}/'
    print('场景与地图更新平台：' + url, flush=True)
    print('选择场景后可审阅地图或回放记录；右侧「交通实验」可启动自动交通与在线状态观察。Ctrl+C 关闭。', flush=True)
    if not args.no_browser:
        threading.Timer(.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
