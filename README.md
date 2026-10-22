# RadGS-Reg project homepage

Static GitHub Pages site for [RadGS-Reg](https://shenao1995.github.io/RadGS-Reg-Homepage/).

## Qualitative visualization

The viewer renders actual RecM predictions using WebGL 2 front-to-back volume ray
marching with a viridis transfer function. OrbitControls supports rotation, zoom,
and pan. Sample selection updates the AP/lateral inputs and volume together.
Density cutoff, opacity, auto rotation and a view reset are available. Rendering
pauses outside the viewport or in hidden tabs. Browser assets are served locally;
there is no inference server or runtime CDN dependency for the viewer.

Five samples come from the [released 10-case test subset](https://huggingface.co/datasets/shenao1995/RadGS-Reg-Test-10), original test indices
0, 48, 96, 287 and 430. Reconstructions use the [released RecM checkpoint](https://huggingface.co/shenao1995/RadGS-Reg-RecM), epoch 68, without CAL. Inputs are
simulated X-rays, with AP/lateral view indices 25/0 and the same area resize to
128×128 used by the network. Volumes preserve the query XYZ orientation; source
Z is the viewer's up axis.

For display only, each predicted 128³ attenuation volume is windowed between zero
and its positive P99.8, clipped to [0,1], then stored as little-endian IEEE float16
with X as the fastest texture axis. The downloadable scientific outputs retain
physical float32 values. Rendering settings do not alter the model predictions.
See `static/volumes/manifest.json` for sample IDs, windows and SHA256 values.

CTSpine1K source data and derivative data retain the upstream CC-BY-NC-SA terms;
see the linked dataset card for attribution and licensing. Three.js 0.160.1 and
OrbitControls are vendored under their MIT license in `static/js/vendor/`.
The website template retains its original CC BY-SA 4.0 notice.

## Local preview

Serve over HTTP, since browser modules and volumes cannot load through `file://`:

```bash
python -m http.server 8000
# Open http://localhost:8000/#qualitative
```

The old results animations have been removed. Quantitative result images and the
remaining project content are preserved.

## Export assets / verify

`scripts/export_volume_assets.py` builds the viewer assets from the native test
data and the evaluated prediction NPZ files, using NumPy, PyTorch, Pillow and
Matplotlib. It accepts either the full dataset or the released 10-case subset.

```bash
python scripts/export_volume_assets.py --dataset /path/to/test-data \
  --evaluation /path/to/RecM-evaluation --output static/volumes
```

Browser checks use Playwright and Chromium, and cover all five WebGL-rendered
samples, rotate/zoom/pan, density/opacity/reset controls, rapid sample switching,
mobile layout, resource errors, and a browser without WebGL 2:

```bash
pip install playwright
playwright install chromium
python scripts/check_viewer.py --output /tmp/radgs-browser-checks
```
