"""Create physically aligned display textures from actual joint model exports.

Scientific NIfTI exports stay untouched. All browser volumes share a centered
192mm cube sampled at 128^3 (1.5mm), with one attenuation window per case.
"""
import argparse,hashlib,json,shutil
from pathlib import Path
import numpy as np
import nibabel as nib
import torch
import torch.nn.functional as F
from PIL import Image,ImageDraw
from matplotlib import colormaps

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--exports',required=True);p.add_argument('--samples',required=True)
p.add_argument('--input-assets',required=True);p.add_argument('--output',required=True)
a=p.parse_args();exports,inputs,out=Path(a.exports),Path(a.input_assets),Path(a.output)
out.mkdir(parents=True,exist_ok=True);torch.set_num_threads(2)
source=json.loads((exports/'manifest.json').read_text())
selection=json.loads(Path(a.samples).read_text())['cases']
assert len(selection)==5 and {x['sample'] for x in selection}=={x['name'] for x in source['cases']}
palette=colormaps['viridis'];records=[];validation=[]

def load(path,size):
 image=nib.load(path);volume=np.asarray(image.dataobj,dtype=np.float32)
 assert volume.shape==(size,)*3 and np.isfinite(volume).all() and volume.min()>=0
 expected=np.eye(4);expected[:3,3]=-(size-1)/2
 assert np.allclose(image.affine,expected,atol=1e-5),(path,image.affine)
 return volume

def texture(volume):
 if volume.shape==(128,)*3:volume=np.pad(volume,32)
 return F.interpolate(torch.from_numpy(volume.copy())[None,None],size=(128,)*3,mode='trilinear',align_corners=False)[0,0].numpy()

def slices(path,fixed,ct=None):
 planes=[]
 for dim in [2,1,0]:
  x=np.rot90(np.take(fixed,64,axis=dim))
  rgb=palette(x)[...,:3]
  rgb[x<.03]=0
  if ct is not None:
   y=np.rot90(np.take(ct,64,axis=dim))
   warm=np.array([.91,.24,.42])*(1-y[...,None])+np.array([1.,.77,.56])*y[...,None]
   wa=np.maximum(x-.12,0);wb=np.maximum(y-.12,0);den=np.maximum(wa+wb,1e-8)
   rgb=(rgb*wa[...,None]+warm*wb[...,None])/den[...,None]
  planes.append(np.uint8(np.clip(rgb,0,1)*255))
 strip=Image.fromarray(np.concatenate(planes,axis=1))
 panel=Image.new('RGB',(384,150),(8,15,27));panel.paste(strip,(0,22))
 draw=ImageDraw.Draw(panel)
 for i,label in enumerate(['Axial','Coronal','Sagittal']):draw.text((128*i+8,5),label,fill='#b8c6d7')
 panel.save(path,quality=90)

def centroid(volume):
 weights=[volume.sum(axis=tuple(j for j in range(3) if j!=i),dtype=np.float64) for i in range(3)]
 return np.array([np.dot(w,np.arange(len(w))-(len(w)-1)/2)/w.sum() for w in weights])

for sample in selection:
 name=sample['sample'];prefix=sample['id'];folder=exports/name
 rec=load(folder/'reconstruction.nii.gz',128);moving=load(folder/'moving_ct.nii.gz',192);registered=load(folder/'registered_ct.nii.gz',192)
 transform=json.loads((folder/'transform.json').read_text());matrix=np.array(transform['moving_to_fixed_ras_mm'])
 assert np.allclose(matrix[:3,:3].T@matrix[:3,:3],np.eye(3),atol=2e-5)
 assert abs(np.linalg.det(matrix[:3,:3])-1)<2e-5
 centroid_error=float(np.linalg.norm(matrix[:3,:3]@centroid(moving)+matrix[:3,3]-centroid(registered)))
 assert centroid_error<.5,centroid_error
 window=max(float(np.percentile(v[v>0],99.8)) for v in [rec,moving,registered])
 displays={key:np.clip(texture(v)/window,0,1) for key,v in [('reconstruction',rec),('moving',moving),('registered',registered)]}
 row=dict(id=prefix,label=sample['label'],vertebra=sample['vertebra'],sample=name,originalTestIndex=sample['originalTestIndex'],shape=[128]*3,spacingMm=[1.5]*3,displayMaxAttenuation=window)
 hashes={}
 for key,volume in displays.items():
  filename=f'{prefix}.bin' if key=='reconstruction' else f'{prefix}-{key}.bin'
  path=out/filename;np.ascontiguousarray(volume.transpose(2,1,0),dtype='<f2').tofile(path)
  assert path.stat().st_size==128**3*2
  row['volume' if key=='reconstruction' else key]=filename
  hashes[key]=hashlib.sha256(path.read_bytes()).hexdigest()
 for key in ['ap','lateral']:
  filename=f'{prefix}-{key}.png';shutil.copyfile(inputs/sample[key],out/filename);row[key]=filename
 row.update(fallback=f'{prefix}-slices.webp',movingFallback=f'{prefix}-moving-slices.webp',registrationFallback=f'{prefix}-registration-slices.webp',movingPreview=f'{prefix}-moving-preview.webp',sha256=hashes)
 slices(out/row['fallback'],displays['reconstruction'])
 slices(out/row['movingFallback'],displays['moving'])
 slices(out/row['registrationFallback'],displays['reconstruction'],displays['registered'])
 # Temporary thumbnail; browser verification replaces it with the actual WebGL rendering.
 mip=np.rot90(displays['moving'].max(axis=1));rgb=palette(mip)[...,:3];rgb[mip<.03]=0
 Image.fromarray(np.uint8(rgb*255)).save(out/row['movingPreview'],quality=90)
 portable={k:transform[k] for k in ['moving_to_fixed_ras_mm','ground_truth_moving_to_fixed_ras_mm','metrics','affine128_voxel_to_ras_mm','affine192_voxel_to_ras_mm']}
 row['transform']=f'{prefix}-transform.json';(out/row['transform']).write_text(json.dumps(portable,indent=2)+'\n')
 records.append(row);validation.append(dict(sample=name,grid_aligned=True,centroid_transform_error_mm=centroid_error,model_mTRE_mm=transform['metrics']['mTRE_mm']))
 print('Exported',prefix,name,flush=True)
(out/'viridis.rgba').write_bytes(np.rint(palette(np.linspace(0,1,256))*255).astype(np.uint8).tobytes())
manifest=dict(schema='radgs-web-registration-v2',checkpoint='joint_best.pth',checkpointEpoch=source['epoch'],checkpointSha256=source['checkpoint_sha256'],model='https://huggingface.co/shenao1995/RadGS-Reg-RegM',modelRevision='9fa2f61d9ad1183286ba099cf140bd5b57814ee5',dataset='https://huggingface.co/datasets/shenao1995/RadGS-Reg-Test',split='test',selection='Same five test cases selected before prediction at original test indices 0,48,96,287,430',sourceVolumeAxes='XYZ',physicalFrame='centered RAS millimetres',physicalCanvasMm=[192]*3,textureAffineRasMm=[[1.5,0,0,-95.25],[0,1.5,0,-95.25],[0,0,1.5,-95.25],[0,0,0,1]],textureStorage='little-endian IEEE float16, x-fastest, then y, then z',displayWindow='Shared positive P99.8 attenuation maximum across reconstruction, moving CT and registered CT, per sample; display only',displayResampling='Reconstruction padded from 128mm to 192mm; all 1mm exports trilinearly resampled to 128^3 at 1.5mm for the browser',registration='Predicted moving-to-fixed rigid transform; registered CT is actually resampled, not the GT pose',inputViews=dict(ap=25,lateral=0),inputResize='area to 128x128',palette='viridis.rgba',cases=records)
(out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
(out.parent/'asset_validation.json').write_text(json.dumps(dict(status='passed',cases=validation,shared_grid=True,shared_attenuation_window=True),indent=2)+'\n')
