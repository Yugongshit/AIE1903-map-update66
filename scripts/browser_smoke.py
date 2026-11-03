"""Optional real-browser smoke check for the synthetic demo (Playwright + Chromium)."""
import argparse
from pathlib import Path
import shutil
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import quote

from playwright.sync_api import sync_playwright


parser = argparse.ArgumentParser()
parser.add_argument("html")
parser.add_argument("--screenshot")
parser.add_argument("--chromium", default=shutil.which("chromium"))
parser.add_argument("--mode", choices=["demo", "intake", "scene"], default="demo")
args = parser.parse_args()


@contextmanager
def local_server(directory):
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


html = Path(args.html).resolve()
with local_server(html.parent) as url, sync_playwright() as p:
    browser = p.chromium.launch(executable_path=args.chromium, headless=True, args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url+quote(html.name))
    if args.mode == "scene":
        assert page.locator("#total").inner_text() == "29"
        assert page.locator("#map-count").inner_text() == "157"
        assert page.locator("#time").get_attribute("max") == "40"
        assert page.locator("#reference path").count() == 157
        if page.locator("#m3-panel").is_visible():
            assert "M3 已接入" in page.locator("#notice").inner_text()
            assert "7 个真实复核对象均证据不足" in page.locator("#notice").inner_text()
            assert "T004_26_40_LOCAL" in page.locator("#m3-cases").inner_text()
            assert "不覆盖 LD003 前段" in page.locator("#m3-cases").inner_text()
        assert page.locator('[data-kind="occupancy"]').count() > 0
        page.locator("#time").fill("11")
        page.locator("#search").fill("LD-001")
        assert page.locator("#candidate-list .item").count() == 1
        assert "有序观测折线" in page.locator("#detail").inner_text()
        if page.locator("#m3-panel").is_visible():
            assert "证据不足（已接入）" in page.locator("#detail").inner_text()
        page.locator("#time").fill("13")
        assert page.locator("#candidate-list .item").count() == 0
        assert "不代表道路恢复" in page.locator("#detail").inner_text()
        page.locator("#active-only").uncheck()
        assert page.locator("#candidate-list .item").count() == 1
        assert "无该候选观测" in page.locator("#detail").inner_text()
        page.locator("#show-map").uncheck()
        assert page.locator("#reference path").count() == 0
        page.locator("#show-map").check()
        page.locator("#search").fill("")
        page.locator("#active-only").check()
        page.locator("#time").fill("18")
        page.locator("#next").click()
        assert page.locator("#time").input_value() == "19"
        page.locator("#prev").click()
        assert page.locator("#time").input_value() == "18"
    elif args.mode == "intake":
        assert page.locator("#total").inner_text() == "29"
        assert page.locator("#occ").inner_text() == "23"
        assert page.locator("#ld").inner_text() == "6"
        assert page.locator("#candidate-list .item").count() == 29
        assert page.locator("#drawing circle").count() > 50
        page.locator("#family").select_option("line_discrepancy")
        assert page.locator("#candidate-list .item").count() == 6
        page.locator("#search").fill("LD-004")
        assert page.locator("#candidate-list .item").count() == 1
        assert "0.9249" in page.locator("#detail").inner_text()
        assert "未提供" in page.locator("#detail").inner_text()
        page.locator("#show-ld").uncheck()
        assert page.locator("#drawing circle").count() == 0
        page.locator("#show-ld").check()
        page.locator("#search").fill("")
        page.locator("#family").select_option("all")
    else:
        assert page.locator("#n-events").inner_text() == "4"
        assert page.locator("#n-applied").inner_text() == "1"
        assert page.locator("#n-temporary").inner_text() == "0"
        assert page.locator("#n-review").inner_text() == "2"
        assert "受控合成" in page.locator("#dataset").inner_text()
        count = page.locator("#drawing path").count()
        assert count >= 8
        page.locator('[data-layer="prior"]').uncheck()
        assert page.locator("#drawing path").count() == count-4
        page.locator('[data-layer="prior"]').check()
        page.locator("#time").fill("0")
        assert page.locator("#n-applied").inner_text() == "0"
        assert page.locator("#n-events").inner_text() == "1"
        assert page.locator("#version").inner_text() == "版本 v0"
        page.locator("#time").fill("1")
        assert page.locator("#n-temporary").inner_text() == "1"
        page.locator("#events .event").nth(1).click()
        assert "occupancy" in page.locator("#detail").inner_text()
        page.locator("#time").fill(page.locator("#time").get_attribute("max"))
    with page.expect_download() as download:
        page.locator("#download").click()
    expected_name = {"demo": "map-v0.u", "intake": "scene0980-evidence-intake", "scene": "scene0980-map-evidence"}[args.mode]
    assert download.value.suggested_filename.startswith(expected_name)
    page.locator("#fit").click()
    if args.screenshot:
        page.screenshot(path=args.screenshot, full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not errors, errors
    browser.close()
    print(f"PASS ({args.mode}): real-browser rendering, controls, evidence details, JSON export, mobile layout; no JS errors")
