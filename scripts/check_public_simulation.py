"""Verify the static scene platform in Chromium with no running simulation API."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--chromium', default=shutil.which('chromium') or shutil.which('chromium-browser'))
    parser.add_argument('--simulation-python', default=str(ROOT/'.venv-sim/bin/python') if (ROOT/'.venv-sim/bin/python').exists() else sys.executable)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    report = {'url': args.url, 'checks': [], 'page_errors': [], 'api_network_requests': []}

    def check(name, condition):
        report['checks'].append({'name': name, 'passed': bool(condition)})
        print(('PASS ' if condition else 'FAIL ') + name, flush=True)
        if not condition:
            raise AssertionError(name)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=args.chromium, headless=True,
            args=['--no-sandbox', '--enable-unsafe-swiftshader', '--disable-dev-shm-usage'])
        page = browser.new_page(viewport={'width': 1600, 'height': 1000})
        page.set_default_timeout(60000)
        page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
        page.on('request', lambda request: report['api_network_requests'].append(request.url) if '/api/' in request.url else None)
        try:
            page.goto(args.url, wait_until='networkidle')
            page.wait_for_function('window.MAP_PLATFORM?.state.snapshot?.step===0')
            check('static site starts without a backend', page.evaluate('SIMULATION_TRANSPORT.isStatic() && SIMULATION_TRANSPORT.needsBackend()'))
            check('all three source scenes are available', page.evaluate('MAP_PLATFORM.state.catalog.length') == 3)
            initial = page.evaluate('({objects:MAP_PLATFORM.state.scene.state_overview.objects.length,statics:MAP_PLATFORM.state.scene.static_overview_actors.length})')
            await_js = '([key,step,version])=>SIMULATION_TRANSPORT.request(`/api/scenes/${key}/planning?step=${step}&version=${version}`)'
            for key, step, version in [('nuscenes-scene0980', 0, 'updated'),
                                       ('nuscenes-scene0980', 137, 'original'),
                                       ('argoverse-winter2021', 200, 'original'),
                                       ('argoverse-winter2021', 200, 'updated'),
                                       ('nuscenes-combined-demo', 137, 'updated')]:
                page.evaluate('(key)=>MAP_PLATFORM.selectScene(key)', key)
                page.evaluate('(step)=>MAP_PLATFORM.seek(step)', step)
                page.wait_for_function('(step)=>MAP_PLATFORM.state.snapshot?.step===step', arg=step)
                actual = page.evaluate(await_js, [key, step, version])
                command = ('import json,sys; from map_update.simulation_platform_data import planning_bundle; '
                           'print(json.dumps(planning_bundle(sys.argv[1],sys.argv[2],int(sys.argv[3]),sys.argv[4]),allow_nan=False))')
                truth = subprocess.run([args.simulation_python, '-c', command, str(ROOT), key, str(step), version],
                                       capture_output=True, text=True, check=True, timeout=90)
                expected = json.loads(truth.stdout)
                check(f'{key} frame {step} {version}: static Planning equals verified Python export', actual == expected)
                check(f'{key} frame {step}: no future tracks or recorded route in export',
                      'tracks' not in actual and actual['route'] is None and actual['requested_goal'] is None)
            page.evaluate("MAP_PLATFORM.selectScene('nuscenes-scene0980')")
            page.evaluate('MAP_PLATFORM.seek(137)')
            check('full-scene fixed previews persist while source clock advances', page.evaluate(
                '({objects:MAP_PLATFORM.state.scene.state_overview.objects.length,statics:MAP_PLATFORM.state.scene.static_overview_actors.length})') == initial)
            validation = page.evaluate("SIMULATION_TRANSPORT.request('/api/scenes/nuscenes-scene0980/validation?version=updated')")
            check('source validation report is available', validation['integrity']['passed'])
            rejected = page.evaluate("SIMULATION_TRANSPORT.request('/api/interactive/open',{method:'POST',body:'{}'}).then(()=>false,()=>true)")
            check('static site does not pretend to start native simulation', rejected)
            check('no request depended on a Python API', not report['api_network_requests'])
            check('no browser exceptions', not report['page_errors'])
            page.screenshot(path=str(args.out/'public-review.png'), full_page=True)
        finally:
            (args.out/'checks.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
            browser.close()


if __name__ == '__main__':
    main()
