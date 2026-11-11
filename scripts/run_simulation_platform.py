"""Run the local scene platform with real MetaDrive playback and planning exports."""
import argparse
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
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if not 0 <= args.port < 65536:
        parser.error('port must be between 0 and 65535')
    try:
        server = PlatformServer(('127.0.0.1', args.port), ROOT)
    except OSError as error:
        parser.exit(2, f'无法启动平台：{error}；可用 --port 8092 选择其他端口。\n')
    url = f'http://127.0.0.1:{server.server_port}/'
    print('场景与地图更新平台：' + url, flush=True)
    print('选择场景后可审阅两层变化、启动 MetaDrive、拖动时间轴及导出 Planning 输入。Ctrl+C 关闭。', flush=True)
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
