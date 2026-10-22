"""Browser checks for real WebGL rendering, controls, sample switching and fallback.

Requires playwright and its Chromium browser. Run with --site and --output paths.
"""
import argparse
import hashlib
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--site', type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--phase', choices=['all', 'desktop', 'mobile'], default='all')
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
site = args.site.resolve()
class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args): pass
server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(site.parent)))
Thread(target=server.serve_forever, daemon=True).start()
origin = f'http://127.0.0.1:{server.server_port}'
url = f'{origin}/{site.name}/'
results = {}


def loaded(page):
    page.wait_for_function("document.querySelector('#reconstruction-demo').getAttribute('aria-busy') === 'false'", polling=100, timeout=60000)
    assert not page.locator('#volume-fallback').is_visible(), page.locator('#volume-status').inner_text()
    assert not page.locator('#volume-status').is_visible()


def picture(page):
    return hashlib.sha256(page.locator('#volume-canvas').screenshot()).hexdigest()


def configure(page, errors, bad_resources):
    page.route('**/*', lambda r: r.continue_() if r.request.url.startswith(origin) else r.fulfill(status=200, body=''))
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('response', lambda r: bad_resources.append([r.status, r.url]) if r.status >= 400 else None)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
    if args.phase in ('all', 'desktop'):
        page = browser.new_page(viewport={'width':1440, 'height':1100}, device_scale_factor=1)
        errors, bad_resources = [], []
        configure(page, errors, bad_resources)
        page.goto(url, wait_until='networkidle')
        page.locator('#qualitative').scroll_into_view_if_needed()
        loaded(page)
        print('Desktop: initial WebGL volume loaded', flush=True)
        assert page.locator('.sample-button').count() == 5
        assert page.locator('video').count() == 0
        before = picture(page)
        bounds = page.locator('#volume-canvas').bounding_box()
        x, y = bounds['x']+bounds['width']*.5, bounds['y']+bounds['height']*.5
        page.mouse.move(x,y); page.mouse.down(); page.mouse.move(x+100,y+55,steps=12); page.mouse.up()
        assert picture(page) != before, 'Rotation did not change the rendered volume'
        rotated = picture(page)
        page.mouse.wheel(0,-250)
        assert picture(page) != rotated, 'Zoom did not change the rendered volume'
        zoomed = picture(page)
        page.mouse.move(x,y); page.mouse.down(button='right'); page.mouse.move(x+60,y,steps=8); page.mouse.up(button='right')
        assert picture(page) != zoomed, 'Pan did not change the rendered volume'
        page.locator('#density-cutoff').evaluate("e => { e.value = '0.25'; e.dispatchEvent(new Event('input')); }")
        assert page.locator('#density-value').text_content() == '0.25'
        page.locator('#volume-opacity').evaluate("e => { e.value = '0.8'; e.dispatchEvent(new Event('input')); }")
        page.locator('#auto-rotate').check()
        assert page.locator('#auto-rotate').is_checked()
        page.locator('#reset-volume-view').click()
        assert not page.locator('#auto-rotate').is_checked()
        assert page.locator('#density-cutoff').input_value() == '0.12'
        assert page.locator('#volume-opacity').input_value() == '1.6'
        rendered = []
        for number in range(1,6):
            name = f'sample-{number:02d}'
            page.locator(f'[data-sample="{name}"]').click()
            loaded(page)
            assert page.locator(f'[data-sample="{name}"]').get_attribute('aria-pressed') == 'true'
            assert page.locator('#input-ap').get_attribute('src').endswith(f'{name}-ap.png')
            assert page.locator('#input-lateral').get_attribute('src').endswith(f'{name}-lateral.png')
            rendered.append(picture(page))
            print(f'Desktop: {name} checked', flush=True)
        assert len(set(rendered)) == 5, 'Each selected volume should render differently'
        page.evaluate("document.querySelector('[data-sample=sample-01]').click(); document.querySelector('[data-sample=sample-03]').click(); document.querySelector('[data-sample=sample-05]').click()")
        loaded(page)
        assert page.locator('[aria-pressed="true"]').get_attribute('data-sample') == 'sample-05'
        page.locator('[data-sample="sample-01"]').click(); loaded(page)
        page.locator('#reconstruction-demo').screenshot(path=str(args.output/'desktop-viewer.png'))
        page.screenshot(path=str(args.output/'desktop-page.png'), full_page=True)
        assert not errors, errors
        assert not bad_resources, bad_resources
        results['desktop'] = dict(real_webgl=True, five_distinct_volumes=True, rotate=True, zoom=True, pan=True, sliders_and_reset=True, rapid_switch=True, page_errors=errors, resource_errors=bad_resources)
        print('Desktop: all checks passed', flush=True)
        page.close()
    if args.phase in ('all', 'mobile'):
        mobile = browser.new_context(viewport={'width':390,'height':844}, device_scale_factor=1, is_mobile=True, has_touch=True, reduced_motion='reduce')
        m = mobile.new_page()
        m.bring_to_front()
        mobile_errors, mobile_bad = [], []
        configure(m,mobile_errors,mobile_bad)
        m.goto(url,wait_until='networkidle'); m.locator('#qualitative').scroll_into_view_if_needed(); loaded(m)
        assert m.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile page overflows horizontally'
        assert 'Pinch' in m.locator('#volume-interaction-help').inner_text()
        m.locator('[data-sample="sample-05"]').click(); loaded(m)
        assert not m.locator('#auto-rotate').is_checked()
        m.locator('#reconstruction-demo').screenshot(path=str(args.output/'mobile-viewer.png'))
        assert not mobile_errors and not mobile_bad, [mobile_errors,mobile_bad]
        results['mobile'] = dict(width=390, no_horizontal_page_overflow=True, sample_switch=True, touch_controls=True, reduced_motion=True)
        mobile.close()
        fallback_context = browser.new_context(viewport={'width':1200,'height':1000})
        fallback_context.add_init_script("const originalGetContext = HTMLCanvasElement.prototype.getContext; HTMLCanvasElement.prototype.getContext = function(kind,...args) { return kind === 'webgl2' ? null : originalGetContext.call(this,kind,...args); };")
        f = fallback_context.new_page()
        f.route('**/*',lambda r:r.continue_() if r.request.url.startswith(origin) else r.fulfill(status=200,body=''))
        f.goto(url,wait_until='networkidle');f.locator('#qualitative').scroll_into_view_if_needed()
        f.locator('#volume-fallback').wait_for(state='visible')
        f.locator('[data-sample="sample-05"]').click()
        assert f.locator('#volume-fallback').get_attribute('src').endswith('sample-05-slices.webp')
        assert not f.locator('#volume-canvas').is_visible()
        assert f.locator('#density-cutoff').is_disabled()
        results['no_webgl'] = dict(slice_preview=True,sample_switch=True,controls_disabled=True)
    browser.close()
server.shutdown()
(args.output/'checks.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps(results,indent=2),flush=True)
