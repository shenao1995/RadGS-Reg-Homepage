import * as THREE from './vendor/three.module.js';
import { OrbitControls } from './vendor/OrbitControls.js';

const root = document.querySelector('#reconstruction-demo');
const picker = document.querySelector('#sample-picker');
const cutoff = document.querySelector('#density-cutoff');
const opacity = document.querySelector('#volume-opacity');
const blend = document.querySelector('#overlay-blend');
const rotation = document.querySelector('#auto-rotate');
const reset = document.querySelector('#reset-volume-view');
const modeButtons = [...document.querySelectorAll('[data-registration-mode]')];
const assetRoot = new URL('../volumes/', import.meta.url);
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const cache = new Map();
const views = ['moving', 'reconstruction', 'registration'].map(kind => ({
  kind, stage: document.querySelector(`[data-volume-view="${kind}"]`),
  cameraLocal: new THREE.Vector3()
}));
for (const view of views) {
  view.canvas = view.stage.querySelector('canvas');
  view.status = view.stage.querySelector('.volume-status');
  view.fallback = view.stage.querySelector('.volume-fallback');
}
let manifest, activeSample, rendererReady, palette, textures;
let pendingRequest, version = 0, started = false, inViewport = false;
let failed = false, loaded = false, dirty = true, synchronizing = false;
let driver, registrationMode = 'after';

const vertexShader = /* glsl */`
  out vec3 vLocalPosition;
  void main() {
    vLocalPosition = position;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;
const fragmentShader = /* glsl */`
  precision highp float;
  precision highp sampler3D;
  uniform sampler3D uVolume;
  uniform sampler3D uCT;
  uniform sampler2D uPalette;
  uniform vec3 uCamera;
  uniform float uCutoff;
  uniform float uOpacity;
  uniform float uBlend;
  uniform bool uOverlay;
  in vec3 vLocalPosition;
  out vec4 fragColor;

  vec2 intersectBox(vec3 origin, vec3 direction) {
    vec3 inverse = 1.0 / direction;
    vec3 a = (-0.5 - origin) * inverse;
    vec3 b = (0.5 - origin) * inverse;
    vec3 nearPlane = min(a, b), farPlane = max(a, b);
    return vec2(max(max(nearPlane.x, nearPlane.y), nearPlane.z),
                min(min(farPlane.x, farPlane.y), farPlane.z));
  }
  float field(vec3 p) {
    float a = texture(uVolume, p).r;
    return uOverlay ? mix(a, texture(uCT, p).r, uBlend) : a;
  }
  void main() {
    vec3 direction = normalize(vLocalPosition - uCamera);
    vec2 bounds = intersectBox(uCamera, direction);
    bounds.x = max(bounds.x, 0.0);
    if (bounds.x >= bounds.y) discard;
    float rayLength = bounds.y - bounds.x;
    int steps = int(clamp(ceil(rayLength * 192.0), 1.0, 384.0));
    float stepSize = rayLength / float(steps);
    vec3 position = uCamera + direction * (bounds.x + stepSize * 0.5) + 0.5;
    vec3 accumulated = vec3(0.0);
    float alpha = 0.0;
    float texel = 1.5 / 128.0;
    for (int i = 0; i < 384; ++i) {
      if (i >= steps || alpha > 0.985) break;
      float a = texture(uVolume, position).r;
      float b = uOverlay ? texture(uCT, position).r : 0.0;
      float da = max(a-uCutoff, 0.0) * smoothstep(uCutoff, uCutoff+0.025, a);
      float db = max(b-uCutoff, 0.0) * smoothstep(uCutoff, uCutoff+0.025, b);
      float wa = uOverlay ? da*(1.0-uBlend) : da;
      float wb = uOverlay ? db*uBlend : 0.0;
      float density = wa+wb;
      if (density > 0.00001) {
        float sampleAlpha = 1.0-exp(-density*uOpacity*stepSize*150.0);
        vec3 gradient = vec3(
          field(position+vec3(texel,0,0))-field(position-vec3(texel,0,0)),
          field(position+vec3(0,texel,0))-field(position-vec3(0,texel,0)),
          field(position+vec3(0,0,texel))-field(position-vec3(0,0,texel))
        );
        float lighting = length(gradient) > 0.0001
          ? 0.55+0.45*abs(dot(normalize(gradient), -direction)) : 0.75;
        vec3 fixedColor = texture(uPalette, vec2(a,0.5)).rgb;
        vec3 ctColor = mix(vec3(0.91,0.24,0.42), vec3(1.0,0.77,0.56), b);
        vec3 color = (fixedColor*wa+ctColor*wb)/density*lighting;
        accumulated += (1.0-alpha)*sampleAlpha*color;
        alpha += (1.0-alpha)*sampleAlpha;
      }
      position += direction*stepSize;
    }
    if (alpha < 0.005) discard;
    fragColor = vec4(accumulated/alpha, alpha);
  }
`;

function asset(name) {
  const url = new URL(name, assetRoot);
  url.searchParams.set('v','registration-1');
  return url.href;
}
function setLoading(message) {
  root.setAttribute('aria-busy', 'true');
  root.dataset.state = 'loading';
  for (const view of views) {
    view.status.textContent = message;
    view.status.hidden = false;
  }
}
function showFallback(message) {
  failed = true;
  root.setAttribute('aria-busy', 'false');
  root.dataset.state = 'fallback';
  for (const view of views) {
    view.stage.classList.add('has-fallback');
    view.fallback.hidden = false;
    view.canvas.hidden = true;
    view.status.textContent = message;
    view.status.hidden = false;
  }
  for (const element of [cutoff, opacity, blend, rotation, reset, ...modeButtons]) element.disabled = true;
}
function updateInputs(sample) {
  root.dataset.sample = sample.id;
  document.querySelector('#active-sample-title').replaceChildren(
    document.createTextNode(`${sample.label} `),
    Object.assign(document.createElement('span'), { textContent: `· ${sample.vertebra}` })
  );
  for (const key of ['ap', 'lateral']) {
    const image = document.querySelector(`#input-${key}`);
    image.src = asset(sample[key]);
    image.alt = `${sample.label} ${key === 'ap' ? 'AP' : 'lateral'} X-ray`;
  }
  for (const view of views) {
    const field = view.kind === 'moving' ? 'movingFallback'
      : view.kind === 'registration' ? 'registrationFallback' : 'fallback';
    view.fallback.src = asset(sample[field]);
    view.fallback.alt = `${sample.label} ${view.kind} slice preview`;
  }
  for (const button of picker.querySelectorAll('button')) {
    button.setAttribute('aria-pressed', String(button.dataset.sample === sample.id));
  }
}
function buildPicker() {
  for (const sample of manifest.cases) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'sample-button';
    button.dataset.sample = sample.id;
    button.setAttribute('aria-label', `View ${sample.label}, ${sample.vertebra}`);
    button.setAttribute('aria-pressed', 'false');
    const images = document.createElement('span');
    images.className = 'sample-image-triple';
    for (const [field, label] of [['ap','AP'], ['lateral','LAT'], ['movingPreview','Moving CT']]) {
      const preview = document.createElement('span');
      preview.className = 'sample-preview';
      const image = new Image(128,128);
      image.src = asset(sample[field]);
      image.alt = '';
      image.loading = 'lazy';
      preview.append(image, Object.assign(document.createElement('span'), { textContent: label }));
      images.append(preview);
    }
    const label = document.createElement('span');
    label.className = 'sample-button-label';
    label.append(Object.assign(document.createElement('span'), { textContent: sample.label }),
      Object.assign(document.createElement('span'), { textContent: sample.vertebra }));
    button.append(images, label);
    button.addEventListener('click', () => selectSample(sample));
    picker.append(button);
  }
}
function synchronize(source) {
  if (synchronizing) return;
  synchronizing = true;
  driver = source;
  for (const view of views) {
    if (view === source || !view.controls) continue;
    view.camera.position.copy(source.camera.position);
    view.camera.up.copy(source.camera.up);
    view.controls.target.copy(source.controls.target);
    view.controls.update();
  }
  synchronizing = false;
  dirty = true;
}
function applyControls() {
  document.querySelector('#density-value').value = Number(cutoff.value).toFixed(2);
  document.querySelector('#blend-value').value = `${Math.round(Number(blend.value)*100)}%`;
  for (const view of views) {
    if (!view.material) continue;
    view.material.uniforms.uCutoff.value = Number(cutoff.value);
    view.material.uniforms.uOpacity.value = Number(opacity.value);
    view.material.uniforms.uBlend.value = Number(blend.value);
  }
  dirty = true;
}
function setRegistrationMode(mode) {
  registrationMode = mode;
  root.dataset.registrationMode = mode;
  for (const button of modeButtons) button.setAttribute('aria-pressed', String(button.dataset.registrationMode === mode));
  document.querySelector('#registration-ct-label').textContent = mode === 'after' ? 'Registered CT' : 'Moving CT';
  const view = views.find(v => v.kind === 'registration');
  if (view.material && textures) view.material.uniforms.uCT.value = textures[mode === 'after' ? 'registered' : 'moving'];
  dirty = true;
}
function resetView() {
  synchronizing = true;
  for (const view of views) {
    if (!view.controls) continue;
    view.camera.position.set(0.78,0.42,0.95);
    view.controls.target.set(0,0,0);
    view.controls.autoRotate = false;
    view.controls.update();
  }
  synchronizing = false;
  driver = views.find(v => v.kind === 'reconstruction');
  cutoff.value = '0.12';
  opacity.value = '1.6';
  blend.value = '0.5';
  rotation.checked = false;
  setRegistrationMode('after');
  applyControls();
}
function resize(view) {
  if (!view.renderer) return;
  const width = view.stage.clientWidth, height = view.stage.clientHeight;
  if (!width || !height) return;
  view.renderer.setPixelRatio(Math.min(devicePixelRatio, width < 400 ? 1 : 1.25));
  view.renderer.setSize(width,height,false);
  view.camera.aspect = width/height;
  view.camera.updateProjectionMatrix();
  dirty = true;
}
async function initRenderers() {
  const response = await fetch(asset(manifest.palette));
  if (!response.ok) throw new Error('Color map unavailable');
  const colors = new Uint8Array(await response.arrayBuffer());
  if (colors.length !== 1024) throw new Error('Invalid color map');
  palette = new THREE.DataTexture(colors,256,1,THREE.RGBAFormat);
  palette.minFilter = palette.magFilter = THREE.LinearFilter;
  palette.needsUpdate = true;
  for (const view of views) {
    const gl = view.canvas.getContext('webgl2', { alpha:true, antialias:false, powerPreference:'low-power' });
    if (!gl) throw new Error('WebGL 2 is unavailable');
    view.renderer = new THREE.WebGLRenderer({ canvas:view.canvas, context:gl, alpha:true, antialias:false });
    view.renderer.debug.onShaderError = () => { throw new Error('Volume shader could not be compiled'); };
    view.renderer.setClearColor(0x080f1b,0);
    view.scene = new THREE.Scene();
    view.camera = new THREE.PerspectiveCamera(36,1,0.01,20);
    view.camera.position.set(0.78,0.42,0.95);
    view.controls = new OrbitControls(view.camera,view.canvas);
    view.controls.enableDamping = false;
    view.controls.minDistance = 0.45;
    view.controls.maxDistance = 4;
    view.controls.zoomSpeed = 0.65;
    view.controls.panSpeed = 0.65;
    view.controls.autoRotateSpeed = 0.65;
    view.controls.listenToKeyEvents(view.canvas);
    view.controls.addEventListener('change', () => synchronize(view));
    view.controls.addEventListener('start', () => { driver = view; });
    view.canvas.addEventListener('contextmenu', event => event.preventDefault());
    view.canvas.addEventListener('pointerdown', () => view.canvas.focus({ preventScroll:true }));
    view.canvas.addEventListener('webglcontextlost', event => {
      event.preventDefault();
      showFallback('3D was interrupted. Showing volume slices. Reload to try again.');
    });
    view.material = new THREE.ShaderMaterial({
      glslVersion:THREE.GLSL3,vertexShader,fragmentShader,
      uniforms:{
        uVolume:{value:null}, uCT:{value:null}, uPalette:{value:palette},
        uCamera:{value:view.cameraLocal},uCutoff:{value:0.12},uOpacity:{value:1.6},
        uBlend:{value:0.5},uOverlay:{value:view.kind === 'registration'}
      },
      side:THREE.BackSide,transparent:true,depthWrite:false
    });
    view.mesh = new THREE.Mesh(new THREE.BoxGeometry(1,1,1),view.material);
    // Every volume shares the same centered RAS grid; source Z is the vertical axis.
    view.mesh.rotation.x = -Math.PI/2;
    view.scene.add(view.mesh);
    new ResizeObserver(() => resize(view)).observe(view.stage);
    resize(view);
  }
  driver = views.find(v => v.kind === 'reconstruction');
  requestAnimationFrame(animate);
}
function draw() {
  for (const view of views) {
    view.mesh.updateMatrixWorld();
    view.cameraLocal.copy(view.camera.position);
    view.mesh.worldToLocal(view.cameraLocal);
    view.renderer.render(view.scene,view.camera);
  }
  dirty = false;
}
function animate() {
  requestAnimationFrame(animate);
  if (failed || !loaded || !inViewport || document.hidden) return;
  if (rotation.checked && !reducedMotion.matches) {
    driver.controls.autoRotate = true;
    driver.controls.update();
    driver.controls.autoRotate = false;
  }
  if (dirty) {
    try { draw(); }
    catch (error) { console.error(error); showFallback('Interactive 3D is unavailable. Showing volume slices.'); }
  }
}
function makeTexture(bytes,shape) {
  const texture = new THREE.Data3DTexture(bytes,...shape);
  texture.format = THREE.RedFormat;
  texture.type = THREE.HalfFloatType;
  texture.minFilter = texture.magFilter = THREE.LinearFilter;
  texture.unpackAlignment = 1;
  texture.needsUpdate = true;
  return texture;
}
async function selectSample(sample) {
  activeSample = sample;
  updateInputs(sample);
  if (failed) return;
  started = true;
  loaded = false;
  const selection = ++version;
  pendingRequest?.abort();
  pendingRequest = new AbortController();
  setLoading(`Loading ${sample.label.toLowerCase()}…`);
  try {
    if (!rendererReady) rendererReady = initRenderers();
    await rendererReady;
    if (selection !== version) return;
    let arrays = cache.get(sample.id);
    if (!arrays) {
      const entries = await Promise.all([['reconstruction',sample.volume], ['moving',sample.moving], ['registered',sample.registered]].map(async ([key,file]) => {
        const response = await fetch(asset(file), {signal:pendingRequest.signal});
        if (!response.ok) throw new Error(`Volume download failed (${response.status})`);
        const bytes = new Uint16Array(await response.arrayBuffer());
        if (bytes.length !== sample.shape.reduce((a,b) => a*b,1)) throw new Error('Invalid volume size');
        return [key,bytes];
      }));
      arrays = Object.fromEntries(entries);
      if (selection !== version) return;
      cache.set(sample.id,arrays);
      while (cache.size > 2) cache.delete(cache.keys().next().value);
    }
    if (selection !== version) return;
    if (textures) for (const texture of Object.values(textures)) texture.dispose();
    textures = Object.fromEntries(Object.entries(arrays).map(([key,bytes]) => [key,makeTexture(bytes,sample.shape)]));
    for (const view of views) {
      view.material.uniforms.uVolume.value = textures[view.kind === 'moving' ? 'moving' : 'reconstruction'];
      view.material.uniforms.uCT.value = textures.registered;
    }
    resetView();
    loaded = true;
    draw();
    for (const view of views) view.status.hidden = true;
    root.setAttribute('aria-busy','false');
    root.dataset.state = 'ready';
  } catch (error) {
    if (error.name === 'AbortError' || selection !== version) return;
    console.error(error);
    if (views.every(v => v.material)) {
      root.setAttribute('aria-busy','false');
      root.dataset.state = 'error';
      for (const view of views) view.status.textContent = 'Could not load this sample. Select a sample to try again.';
    } else showFallback('Interactive 3D is unavailable. Showing volume slices.');
  }
}
for (const control of [cutoff,opacity,blend]) control.addEventListener('input',applyControls);
for (const button of modeButtons) button.addEventListener('click', () => setRegistrationMode(button.dataset.registrationMode));
rotation.addEventListener('change', () => { dirty = true; });
reset.addEventListener('click',resetView);
reducedMotion.addEventListener('change', () => { if (reducedMotion.matches) rotation.checked = false; });
setRegistrationMode('after');
try {
  const response = await fetch(asset('manifest.json?v=registration-1'));
  if (!response.ok) throw new Error('Sample manifest unavailable');
  manifest = await response.json();
  buildPicker();
  activeSample = manifest.cases[0];
  updateInputs(activeSample);
  new IntersectionObserver(entries => {
    inViewport = entries[0].isIntersecting;
    if (inViewport && !started) selectSample(activeSample);
  }, {rootMargin:'180px'}).observe(root);
} catch (error) {
  console.error(error);
  showFallback('Sample data could not be loaded. Reload to try again.');
}
