"""Exercise the real evidence dashboard and capture an actual browser-state tour."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
from threading import Thread

from playwright.sync_api import sync_playwright


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('directory', type=Path)
parser.add_argument('--record', action='store_true')
args = parser.parse_args()
root = args.directory.resolve()


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(root)))
thread = Thread(target=server.serve_forever, daemon=True)
thread.start()
checks = []


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    checks.append(name)


try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1600, 'height': 1200}, device_scale_factor=1)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'http://127.0.0.1:{server.server_port}/index.html')
        check('real candidate count', page.locator('#candidate-count').inner_text() == '29')
        check('reference geometry present', page.locator('#map-reference path').count() == 157)
        check('actual geometry observations present', page.locator('[data-kind="observed-line"]').count() == 2)
        check('real LD004 values', '2.30' in page.locator('#difference-value').inner_text()
              and page.locator('#score-value').inner_text() == '0.9249')
        check('no real map writes claimed', page.locator('#update-count').inner_text() == '0')
        page.screenshot(path=str(root/'scene_preview.png'), full_page=True)
        page.locator('#frame').fill('34')
        check('missing frame is not clearance', '不等于恢复' in page.locator('#current-observation').inner_text())
        page.locator('#candidate').select_option('S0980-LD-003')
        check('LD003 exact earlier window', '帧 18–25' in page.locator('#case-sub').inner_text())
        page.locator('#candidate').select_option('T004_26_40_LOCAL')
        check('T004 later window remains inconclusive', '帧 26–40' in page.locator('#case-sub').inner_text()
              and '证据不足' in page.locator('#decision-title').inner_text())
        page.locator('#candidate').select_option('S0980-OCC-scene-0980-O001')
        page.locator('#frame').fill('8')
        check('no association line beyond distance gate', page.locator('#map-links path').count() == 0)
        page.locator('#frame').fill('13')
        check('exact frame association is visible', page.locator('#map-links path').count() == 1
              and '1.79 m' in page.locator('#association-context').inner_text())
        page.screenshot(path=str(root/'occupancy_preview.png'), full_page=True)
        page.locator('[data-layer="map"]').click()
        check('map layer toggle', page.locator('#map-reference path').count() == 0)
        page.locator('[data-layer="map"]').click()
        page.locator('#open-source').click()
        check('evidence sources visible', 'processed/objects.csv' in page.locator('#source-content').inner_text())
        with page.expect_download() as info:
            page.locator('#export-evidence').click()
        dest = root/'exported_evidence.json'
        info.value.save_as(str(dest))
        evidence = json.loads(dest.read_text())
        check('export matches selected real frame', evidence['frame']['frame_index'] == 13
              and evidence['selected_id'].endswith('O001') and evidence['base_map_modified'] is False)
        page.locator('#close-source').click()
        page.locator('.tabs [data-tab="evaluation"]').click()
        method = page.locator('#method-cards .method').nth(2)
        check('denominators visible alongside F1', all(v in method.inner_text() for v in ['94.1%', '28.6%', '22.2%', '36/126']))
        page.screenshot(path=str(root/'evaluation_preview.png'), full_page=True)
        page.locator('#stratum').select_option('low')
        check('unavailable conditional score shown as unknown', '—' in method.inner_text() and '0/42' in method.inner_text())
        with page.expect_download() as info:
            page.locator('#export-current').click()
        dest = root/'exported_evaluation.json'
        info.value.save_as(str(dest))
        evaluation = json.loads(dest.read_text())
        check('evaluation export preserves strata and real unknown accuracy', evaluation['stratum'] == 'low'
              and evaluation['controlled_metrics'][2]['f1_resolved'] is None
              and evaluation['real_accuracy']['f1'] is None)
        page.locator('#stratum').select_option('all')
        page.locator('.tabs [data-tab="evidence"]').click()
        check('all seven review units visible', page.locator('#review-table tr').count() == 7)
        page.screenshot(path=str(root/'evidence_preview.png'), full_page=True)
        page.locator('#review-table tr').filter(has_text='LD004').locator('button').click()
        check('review table links back to candidate', page.locator('#scene-page').is_visible()
              and page.locator('#candidate').input_value() == 'S0980-LD-004')
        page.locator('#tour').click()
        page.wait_for_function("!document.getElementById('tour-banner').hidden")
        check('guided tour starts', '停止演示' in page.locator('#tour').inner_text())
        page.locator('#tour-stop').click()
        check('guided tour stops', page.locator('#tour-banner').is_hidden())
        page.set_viewport_size({'width': 390, 'height': 844})
        for tab in ('scene', 'evaluation', 'evidence'):
            page.locator(f'.tabs [data-tab="{tab}"]').click()
            check('mobile layout '+tab, page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        check('no browser errors', not errors)
        print(f'PASS: {len(checks)} browser checks', flush=True)
        (root/'browser_checks.json').write_text(json.dumps({'status': 'PASS', 'checks': checks,
            'javascript_errors': errors}, ensure_ascii=False, indent=2)+'\n')
        if args.record:
            page.set_viewport_size({'width': 1600, 'height': 1200})
            frames = root/'tour_frames'
            frames.mkdir(exist_ok=True)
            sequence = []

            def capture(duration, caption):
                page.locator('#tour-banner').evaluate('(el)=>el.hidden=false')
                page.locator('#tour-step').evaluate('(el)=>el.textContent="实际界面演示"')
                page.locator('#tour-copy').evaluate('(el,text)=>el.textContent=text', caption)
                name = f'{len(sequence):03d}.png'
                page.screenshot(path=str(frames/name))
                sequence.append((name, duration))

            page.locator('.tabs [data-tab="scene"]').click()
            page.locator('#candidate').select_option('S0980-LD-004')
            page.locator('#frame').fill('30')
            page.locator('#focus').click()
            capture(3, '真实 scene-0980：地图、感知曲线与占用轮廓已经按帧连接。')
            for i in range(27, 34):
                page.locator('#frame').fill(str(i))
                capture(.45, 'LD004 的连续观测：有多帧支持，仍不能替代可靠性与长期证据。')
            page.locator('#candidate').select_option('S0980-LD-003')
            page.locator('#frame').fill('21')
            capture(2.5, 'LD003 属于第18–25帧，单独保留为优先复核对象。')
            page.locator('#candidate').select_option('T004_26_40_LOCAL')
            page.locator('#frame').fill('30')
            capture(2.5, '同一 T004 的后段局部匹配，不能取消前段的 LD003 差异。')
            page.locator('#candidate').select_option('S0980-OCC-scene-0980-O001')
            for i in range(10, 15):
                page.locator('#frame').fill(str(i))
                capture(.5, '占用保留真实轮廓；灰线仅表示本帧3米以内的已关联车辆。')
            page.locator('#frame').fill('13')
            capture(2, '当前距离1.79米。空间邻近并不证明车辆因障碍改变了行为。')
            page.locator('#open-source').click()
            capture(2.5, '每个判断可以展开：原始观测 → 多帧支持 → 可靠性 → 维护决定。')
            page.locator('#close-source').click()
            page.locator('.tabs [data-tab="evaluation"]').click()
            capture(4, '成员交付的受控对比：同时报告F1、判定覆盖率和全部正例检出率。')
            page.locator('#stratum').select_option('low')
            capture(2.5, '低覆盖组全部保留未决；缺少明确判断时，F1显示为未知。')
            page.locator('#stratum').select_option('all')
            page.locator('.tabs [data-tab="evidence"]').click()
            capture(3.5, '最后汇总证据：七个M3对象未决，基础地图不写入未经确认的变化。')
            manifest = frames/'frames.txt'
            manifest.write_text(''.join(f"file '{name}'\nduration {duration}\n" for name, duration in sequence)
                                +f"file '{sequence[-1][0]}'\n")
            subprocess.run(['ffmpeg','-y','-loglevel','error','-f','concat','-safe','0','-i',str(manifest),
                '-vf','format=yuv420p','-r','20','-c:v','libx264','-preset','fast','-crf','21',
                '-movflags','+faststart',str(root/'evidence_demo_tour.mp4')],check=True)
            subprocess.run(['ffmpeg','-y','-loglevel','error','-i',str(root/'evidence_demo_tour.mp4'),
                '-filter_complex','fps=5,scale=1120:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer',
                '-loop','0',str(root/'evidence_demo_preview.gif')],check=True)
            print('Saved actual browser-state tour: MP4 and GIF',flush=True)
        browser.close()
finally:
    server.shutdown()
    server.server_close()
    thread.join()
