"""Frame projection previews around their foreground without altering source pixels."""
import argparse
import base64
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image


def crop_previews(root):
    manifest_path = root / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    for sample in manifest['cases']:
        for view in ('ap', 'lateral'):
            source = sample.get(f'{view}Source', sample[view])
            path = root / source
            with Image.open(path) as image:
                pixels = np.asarray(image.convert('L'))
            y, x = np.nonzero(pixels)
            if not len(x):
                raise ValueError(f'Empty projection: {source}')
            height, width = pixels.shape
            margin = max(3, math.ceil(max(np.ptp(x) + 1, np.ptp(y) + 1) * .08))
            left, top = max(0, int(x.min()) - margin), max(0, int(y.min()) - margin)
            right = min(width, int(x.max()) + margin + 1)
            bottom = min(height, int(y.max()) + margin + 1)
            roi_width, roi_height = right - left, bottom - top
            encoded = base64.b64encode(path.read_bytes()).decode('ascii')
            name = f'{sample["id"]}-{view}-roi.svg'
            # A self-contained SVG viewport crops the exact original raster;
            # keeping its aspect ratio prevents distortion in either preview size.
            (root / name).write_text(
                f'<svg xmlns="http://www.w3.org/2000/svg" '
                f'width="{roi_width}" height="{roi_height}" '
                f'viewBox="{left} {top} {roi_width} {roi_height}">'
                f'<image width="{width}" height="{height}" '
                f'href="data:image/png;base64,{encoded}"/></svg>\n'
            )
            sample[f'{view}Source'] = source
            sample[f'{view}Roi'] = [left, top, roi_width, roi_height]
            sample[view] = name
            print(f'{sample["id"]} {view}: {width}x{height} → {roi_width}x{roi_height}')
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets', type=Path, default=Path('static/volumes'))
    crop_previews(parser.parse_args().assets)
