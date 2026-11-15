"""Exercise real clock/wait diagnostics and the Planning export boundary."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8093')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--chromium', default='/usr/bin/chromium')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    checks, errors = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=args.chromium, headless=True,
                                     args=['--no-sandbox', '--enable-unsafe-swiftshader'])
        page = browser.new_page(viewport={'width': 1600, 'height': 1050}, accept_downloads=True)
        page.on('pageerror', lambda error: errors.append(str(error)))
        def check(name, ok):
            assert ok, name
            checks.append(name)
            print('PASS ' + name, flush=True)
        page.goto(args.url)
        page.wait_for_function('window.MAP_PLATFORM?.state.snapshot', timeout=60000)
        page.locator('#driving-tab').click()
        page.locator('#traffic-event').select_option('none')
        page.locator('#lane-density').fill('0')
        page.locator('#traffic-count').fill('12')
        page.locator('#start-interactive').click()
        try:
            page.wait_for_function('MAP_PLATFORM.interactive.frame?.interactive?.traffic_diagnostics', timeout=180000)
            check('paused clock is explicit', '服务器暂停' in page.locator('#playback-note').inner_text())
            check('physical actors have wait diagnostics', page.evaluate('Object.values(MAP_PLATFORM.interactive.frame.interactive.vehicles).every(v=>v.wait_diagnostic)'))
            page.evaluate('MAP_PLATFORM.interactiveControl("play")')
            page.wait_for_function('MAP_PLATFORM.interactive.frame?.interactive?.sim_time_s>=3', timeout=180000)
            check('running clock and traffic are distinguished', '辆运动' in page.locator('#playback-note').inner_text())
            check('stationary ego shows elapsed stop time', '已停' in page.locator('[data-field="status"]').first.inner_text())
            with page.expect_download() as info:
                page.locator('#export-planning').click()
            data = json.loads(Path(info.value.path()).read_text())
            encoded = json.dumps(data)
            check('Planning excludes debugging waits and generator futures', all(k not in encoded for k in ('wait_diagnostic', 'waiting_cycles', 'waiting_for', 'exit_lane', 'entry_lane', 'patrol_goals')))
            # Hold a real pre-pause poll response, then deliver it after the
            # control request. It must not restore the stale running status.
            page.evaluate("""() => {
              const original = window.fetch;
              let hold = true;
              window.fetch = async (...args) => {
                const response = await original(...args);
                if (hold && String(args[0]).includes('/api/interactive/state?')) {
                  hold = false;
                  await new Promise(resolve => window.releaseStalePoll = resolve);
                }
                return response;
              };
            }""")
            page.wait_for_function('typeof window.releaseStalePoll === "function"', timeout=15000)
            page.evaluate('MAP_PLATFORM.interactiveControl("pause")')
            page.wait_for_function("!MAP_PLATFORM.interactive.room.playing && document.querySelector('#playback-note').textContent.includes('服务器暂停')", timeout=15000)
            page.evaluate("""() => {
              window.staleRunningSeen = false;
              window.pauseMonitor = setInterval(() => {
                if (MAP_PLATFORM.interactive.room.playing) window.staleRunningSeen = true;
              }, 5);
              window.releaseStalePoll();
            }""")
            page.wait_for_timeout(600)
            page.evaluate('clearInterval(window.pauseMonitor)')
            check('delayed pre-pause poll cannot restore running state', not page.evaluate('window.staleRunningSeen'))
            check('pause status is visible', '服务器暂停' in page.locator('#playback-note').inner_text())
            page.screenshot(path=str(args.out/'diagnostics.png'), full_page=True)
            check('no browser exceptions', not errors)
        finally:
            page.evaluate('MAP_PLATFORM.interactiveControl("close")')
            browser.close()
    (args.out/'checks.json').write_text(json.dumps(dict(passed=checks, browser_errors=errors), ensure_ascii=False, indent=2)+'\n')


if __name__ == '__main__':
    main()
