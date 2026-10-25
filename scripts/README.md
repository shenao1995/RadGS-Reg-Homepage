# Qualitative reconstruction and registration viewer

The current visualization uses five original test indices: **0, 48, 96, 287, 430**.
The matched RecM + point RegM checkpoint is epoch168, SHA256
`b265bc369a06e69583c2f27a883699ce25e3898356173a0604c807a11a6b9b71`.
It is published as `joint_best.pth` in
[RadGS-Reg-RegM](https://huggingface.co/shenao1995/RadGS-Reg-RegM/tree/9fa2f61d9ad1183286ba099cf140bd5b57814ee5).
The original five cases were retained, without selecting them by prediction quality.

`export_registration_assets.py` consumes actual `joint_infer.py` NIfTI exports:
`reconstruction.nii.gz`, `moving_ct.nii.gz`, `registered_ct.nii.gz`, and
`transform.json`. The moving CT is generated with the dataset's fixed sampled
pose. The registered CT is resampled using the **predicted** moving-to-fixed
transform. Ground-truth transforms are provenance only and are not used to
make the registration overlay look aligned.

All three browser volumes use the same centered RAS 192 mm cube. The 128 mm
reconstruction is zero-padded first; all original 1 mm volumes are then
trilinearly resampled to 128³ at 1.5 mm for browser display. One attenuation
window is shared across the three volumes in each case. Browser textures use
little-endian float16 in X-fastest order. The scientific source exports are
unchanged. Exact grids, transforms, windows and texture SHA256 values are
recorded in `static/volumes/manifest.json` and the per-case transform JSONs.

```bash
python scripts/export_registration_assets.py \
  --exports /path/to/joint_exports \
  --samples /path/to/original_five_case_manifest.json \
  --input-assets /path/to/input_projection_images \
  --output static/volumes
```

The exporter requires PyTorch, NumPy, nibabel, Pillow and matplotlib. Existing
AP/LAT images preserve the original training-loader area resize to 128².
`export_volume_assets.py` is the earlier reconstruction-only exporter; it is
not the exporter for the current three-view demo.

The WebGL viewer shares camera rotation, pan and zoom between moving CT,
reconstruction and registration. Before/After switches the CT layer between
its initial and predicted registered poses. Viridis encodes reconstruction
and moving CT attenuation; a warm CT layer distinguishes the overlay.
The CT overlay slider can isolate either layer. Density and opacity use the
same settings across views. No WebGL2 support falls back to actual slices.

Run browser validation with Playwright/Chromium, Pillow and NumPy installed:

```bash
python scripts/check_viewer.py --site . --output /tmp/radgs-viewer-checks \
  --capture-thumbnails
```

`--capture-thumbnails` replaces the exporter's temporary moving-CT MIPs with
screenshots of the actual interactive WebGL volumes. The check covers all
five samples and three renderers, before/after, linked cameras, zoom, sliders,
rapid switching, network recovery, mobile touch rotation and slice fallbacks.
It also checks that the visualization stays before Abstract and that the
AP/LAT labels have no expanded direction text.
