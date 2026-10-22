"""Export five actual RecM predictions and their exact 128x128 input projections."""
import argparse, hashlib, json, pickle
from pathlib import Path
import numpy as np
from PIL import Image
import torch
from matplotlib import colormaps

parser=argparse.ArgumentParser()
parser.add_argument('--dataset',required=True)
parser.add_argument('--evaluation',required=True)
parser.add_argument('--output',required=True)
args=parser.parse_args()
root,ev,out=Path(args.dataset),Path(args.evaluation),Path(args.output)
out.mkdir(parents=True,exist_ok=True)
paths=dict(enumerate((root/'splits/test.txt').read_text().splitlines()))
if (root/'selection.json').exists():
    paths={row['original_test_index']:row['file'] for row in json.loads((root/'selection.json').read_text())['cases']}
cases=[]
for number,index in enumerate([0,48,96,287,430],1):
    name=f'sample-{number:02d}'
    with (root/paths[index]).open('rb') as f:sample=pickle.load(f)
    with np.load(ev/'visualizations'/f'{index:03d}_arrays.npz') as arrays:
        pred=arrays['volume_prediction'].copy()
        assert str(arrays['sample'])==Path(paths[index]).stem
    positive=pred[pred>0]
    peak=float(np.percentile(positive,99.8))
    # WebGL texture storage is X-fastest; source volume is XYZ.
    tex=np.clip(pred/peak,0,1).astype('<f2')
    dest=out/f'{name}.bin'
    dest.write_bytes(np.ascontiguousarray(tex.transpose(2,1,0)).tobytes())
    for label,view in [('ap',25),('lateral',0)]:
        projection=sample['train']['projections'][view]
        small=torch.nn.functional.interpolate(torch.from_numpy(projection.copy())[None,None],size=(128,128),mode='area')[0,0].numpy()
        maximum=float(small.max())
        image=np.rint(np.clip(small/maximum,0,1)*255).astype(np.uint8)
        Image.fromarray(image).save(out/f'{name}-{label}.png')
    Image.open(ev/'visualizations'/f'{index:03d}_volume_slice.png').convert('RGB').save(out/f'{name}-slices.webp',quality=75)
    level=Path(paths[index]).stem.rsplit('_',1)[-1]
    cases.append(dict(id=name,label=f'Sample {number:02d}',vertebra=level,
        sample=Path(paths[index]).stem,originalTestIndex=index,
        volume=f'{name}.bin',ap=f'{name}-ap.png',lateral=f'{name}-lateral.png',
        fallback=f'{name}-slices.webp',shape=[128]*3,spacingMm=[1]*3,
        displayMaxAttenuation=peak,sha256=hashlib.sha256(dest.read_bytes()).hexdigest()))
rgba=np.rint(colormaps['viridis'](np.linspace(0,1,256))*255).astype(np.uint8)
(out/'viridis.rgba').write_bytes(rgba.tobytes())
manifest=dict(schema='radgs-web-volume-v1',checkpoint='best_detail.pth',checkpointEpoch=68,
    model='https://huggingface.co/shenao1995/RadGS-Reg-RecM',
    dataset='https://huggingface.co/datasets/shenao1995/RadGS-Reg-Test-10',
    sourceVolumeAxes='XYZ',textureStorage='little-endian IEEE float16, x-fastest, then y, then z',
    displayWindow='0 to per-prediction positive P99.8 attenuation; encoded as float16 for browser display only',
    inputViews=dict(ap=25,lateral=0),inputResize='area to 128x128, matching GSDataset Resize',
    palette='viridis.rgba',cases=cases)
(out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps(manifest,indent=2))
