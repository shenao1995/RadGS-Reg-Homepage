"""Verify the three real WebGL views, alignment controls, all samples and mobile."""
import argparse,hashlib,io,json
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from PIL import Image
import numpy as np
from playwright.sync_api import sync_playwright

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--site',type=Path,default=Path(__file__).resolve().parents[1])
p.add_argument('--output',type=Path,required=True)
p.add_argument('--capture-thumbnails',action='store_true')
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True);site=a.site.resolve()
class Quiet(SimpleHTTPRequestHandler):
 def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(site.parent)))
Thread(target=server.serve_forever,daemon=True).start();origin=f'http://127.0.0.1:{server.server_port}';url=f'{origin}/{site.name}/'
results={}

def configure(page,errors,bad):
 page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(origin) else route.fulfill(status=200,body=''))
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.on('response',lambda r:bad.append([r.status,r.url]) if r.status>=400 else None)

def loaded(page,sample=None):
 page.wait_for_function("document.querySelector('#reconstruction-demo').dataset.state === 'ready'",polling=100,timeout=120000)
 if sample:assert page.locator('#reconstruction-demo').get_attribute('data-sample')==sample
 assert page.locator('.volume-stage canvas').count()==3
 assert page.locator('.volume-fallback:visible').count()==0
 assert page.locator('.volume-status:visible').count()==0

def picture(page,kind):
 raw=page.locator(f'[data-volume-view="{kind}"] canvas').screenshot(timeout=90000)
 image=Image.open(io.BytesIO(raw)).convert('RGB');w,h=image.size
 return np.asarray(image.crop((w//4,h//4,3*w//4,3*h//4)))

def digest(pixels):return hashlib.sha256(pixels.tobytes()).hexdigest()
def drag(page,kind):
 canvas=page.locator(f'[data-volume-view="{kind}"] canvas');canvas.scroll_into_view_if_needed();box=canvas.bounding_box()
 x,y=box['x']+box['width']*.5,box['y']+box['height']*.5
 page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+60,y+35,steps=8);page.mouse.up()

def slider(page,id,value):page.locator(id).evaluate('(e,v)=>{e.value=v;e.dispatchEvent(new Event("input"));}',str(value))

with sync_playwright() as pw:
 browser=pw.chromium.launch(headless=True,args=['--no-sandbox','--use-angle=swiftshader','--enable-unsafe-swiftshader'])
 context=browser.new_context(viewport=dict(width=1500,height=1100),device_scale_factor=1)
 page=context.new_page();errors=[];bad=[];configure(page,errors,bad)
 page.goto(url,wait_until='networkidle');page.locator('#qualitative').scroll_into_view_if_needed();loaded(page)
 assert page.locator('.sample-button').count()==5
 assert page.locator('.sample-image-triple img').count()==15
 assert page.locator('.xray-card figcaption').all_text_contents()==['AP','LAT']
 assert page.locator('#volume-interaction-help').count()==0
 assert page.evaluate("document.querySelector('#qualitative').compareDocumentPosition([...document.querySelectorAll('h2')].find(e=>e.textContent==='Abstract')) & Node.DOCUMENT_POSITION_FOLLOWING")
 boxes=[page.locator(f'[data-volume-view="{k}"]').bounding_box() for k in ['moving','reconstruction','registration']]
 assert boxes[0]['x']<boxes[1]['x']<boxes[2]['x']
 renders={}
 for i in range(1,6):
  sample=f'sample-{i:02d}';page.locator(f'button[data-sample="{sample}"]').click();loaded(page,sample)
  assert page.locator(f'button[data-sample="{sample}"]').get_attribute('aria-pressed')=='true'
  signatures=[]
  for kind in ['moving','reconstruction','registration']:
   pixels=picture(page,kind);signatures.append(digest(pixels))
   # The rendered anatomy contains green viridis pixels, absent from the blue background.
   assert int(((pixels[:,:,1]>pixels[:,:,0]*1.15)&(pixels[:,:,1]>pixels[:,:,2]*.95)).sum())>50,(sample,kind)
  renders[sample]=signatures
  if a.capture_thumbnails:
   raw=page.locator('#moving-canvas').screenshot();image=Image.open(io.BytesIO(raw)).convert('RGB')
   # Retain the actual WebGL view and omit the panel's overlaid title.
   w,h=image.size;image=image.crop((0,34,w,h));side=min(image.size)
   x=(image.width-side)//2;y=(image.height-side)//2
   image.crop((x,y,x+side,y+side)).resize((192,192)).save(site/'static/volumes'/f'{sample}-moving-preview.webp',quality=90)
  after=picture(page,'registration')
  page.locator('button[data-registration-mode="before"]').click()
  before=picture(page,'registration');assert digest(before)!=digest(after),(sample,'before/after unchanged')
  page.locator('button[data-registration-mode="after"]').click()
  print('Checked three views and before/after:',sample,flush=True)
 assert all(len({v[j] for v in renders.values()})==5 for j in range(3))
 page.locator('button[data-sample="sample-01"]').click();loaded(page)
 initial=[digest(picture(page,k)) for k in ['moving','reconstruction','registration']]
 drag(page,'reconstruction')
 rotated=[digest(picture(page,k)) for k in ['moving','reconstruction','registration']]
 assert all(a!=b for a,b in zip(initial,rotated)),'Cameras did not rotate together'
 canvas=page.locator('#volume-canvas');box=canvas.bounding_box();page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2);page.mouse.wheel(0,-180)
 assert digest(picture(page,'reconstruction'))!=rotated[1]
 slider(page,'#overlay-blend',0);fixed=picture(page,'registration');slider(page,'#overlay-blend',1);ct=picture(page,'registration');assert digest(fixed)!=digest(ct)
 slider(page,'#density-cutoff',.25);assert page.locator('#density-value').text_content()=='0.25'
 slider(page,'#volume-opacity',.8);page.locator('#auto-rotate').check();assert page.locator('#auto-rotate').is_checked()
 page.locator('#reset-volume-view').click();assert not page.locator('#auto-rotate').is_checked();assert page.locator('#overlay-blend').input_value()=='0.5'
 page.evaluate("document.querySelector('button[data-sample=sample-02]').click();document.querySelector('button[data-sample=sample-03]').click();document.querySelector('button[data-sample=sample-05]').click()")
 loaded(page,'sample-05')
 # A failed download must be recoverable, with no stale volumes exposed for the new inputs.
 page.route('**/sample-04-moving.bin*',lambda route:route.abort())
 page.locator('button[data-sample="sample-04"]').click()
 page.wait_for_function("document.querySelector('#reconstruction-demo').dataset.state==='error'",polling=100,timeout=90000)
 assert page.locator('.volume-status:visible').count()==3
 page.unroute('**/sample-04-moving.bin*');page.locator('button[data-sample="sample-01"]').click();loaded(page)
 page.locator('#reconstruction-demo').screenshot(path=str(a.output/'desktop-viewer.png'),timeout=120000)
 page.screenshot(path=str(a.output/'desktop-page.png'),full_page=True,timeout=120000)
 assert not errors and not bad,(errors,bad)
 results['desktop']=dict(three_real_webgl_views=True,five_cases=True,moving_thumbnails=True,predicted_before_after=True,shared_camera_rotation=True,zoom=True,overlay_blend=True,controls_and_reset=True,rapid_switch=True,network_recovery=True,page_errors=errors,resource_errors=bad)
 context.close()
 mobile=browser.new_context(viewport=dict(width=390,height=844),device_scale_factor=1,is_mobile=True,has_touch=True,reduced_motion='reduce')
 m=mobile.new_page();me=[];mb=[];configure(m,me,mb);m.bring_to_front()
 m.goto(url,wait_until='networkidle');m.locator('#qualitative').scroll_into_view_if_needed();loaded(m)
 assert m.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
 m.locator('button[data-sample="sample-05"]').click();loaded(m)
 m.locator('button[data-registration-mode="before"]').click();assert m.locator('#registration-ct-label').text_content()=='Moving CT'
 m.locator('button[data-registration-mode="after"]').click()
 canvas=m.locator('#volume-canvas');canvas.scroll_into_view_if_needed();box=canvas.bounding_box();cdp=mobile.new_cdp_session(m)
 old=digest(picture(m,'reconstruction'));x=box['x']+box['width']*.5;y=box['y']+box['height']*.5
 cdp.send('Input.dispatchTouchEvent',dict(type='touchStart',touchPoints=[dict(x=x,y=y,id=1)]))
 for step in range(1,7):cdp.send('Input.dispatchTouchEvent',dict(type='touchMove',touchPoints=[dict(x=x+step*7,y=y+step*3,id=1)]))
 cdp.send('Input.dispatchTouchEvent',dict(type='touchEnd',touchPoints=[]));assert digest(picture(m,'reconstruction'))!=old
 m.locator('#reset-volume-view').click()
 m.locator('#reconstruction-demo').screenshot(path=str(a.output/'mobile-viewer.png'),timeout=120000)
 assert not me and not mb,(me,mb)
 results['mobile']=dict(width=390,no_horizontal_overflow=True,three_volumes=True,sample_switch=True,before_after=True,actual_touch_rotation=True,reduced_motion=True)
 mobile.close()
 fallback=browser.new_context(viewport=dict(width=1200,height=1000))
 fallback.add_init_script("const original=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(k,...a){return k==='webgl2'?null:original.call(this,k,...a)}")
 f=fallback.new_page();configure(f,[],[]);f.goto(url,wait_until='networkidle');f.locator('#qualitative').scroll_into_view_if_needed()
 f.wait_for_function("document.querySelector('#reconstruction-demo').dataset.state==='fallback'",polling=100,timeout=30000)
 assert f.locator('.volume-fallback:visible').count()==3
 f.locator('button[data-sample="sample-05"]').click();assert 'sample-05-moving-slices.webp' in f.locator('[data-volume-view=moving] .volume-fallback').get_attribute('src')
 assert f.locator('#overlay-blend').is_disabled()
 results['fallback']=dict(three_slice_previews=True,sample_switch=True)
 fallback.close();browser.close()
server.shutdown();(a.output/'checks.json').write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results,indent=2),flush=True)
