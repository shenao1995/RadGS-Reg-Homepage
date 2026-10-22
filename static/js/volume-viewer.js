import * as THREE from './vendor/three.module.js';
import { OrbitControls } from './vendor/OrbitControls.js';

const root = document.querySelector('#reconstruction-demo');
const stage = document.querySelector('#volume-stage');
const canvas = document.querySelector('#volume-canvas');
const status = document.querySelector('#volume-status');
const fallback = document.querySelector('#volume-fallback');
const picker = document.querySelector('#sample-picker');
const cutoff = document.querySelector('#density-cutoff');
const opacity = document.querySelector('#volume-opacity');
const rotation = document.querySelector('#auto-rotate');
const reset = document.querySelector('#reset-volume-view');
const assetRoot = new URL('../volumes/', import.meta.url);
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const volumeCache = new Map();
const cameraLocal = new THREE.Vector3();
let manifest, activeSample, renderer, scene, camera, controls, mesh, material;
let palette, volumeTexture, pendingRequest, rendererReady, selectionVersion = 0;
let inViewport = false, needsRender = true, failed = false, hasStarted = false;

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
  uniform sampler2D uPalette;
  uniform vec3 uCamera;
  uniform float uCutoff;
  uniform float uOpacity;
  in vec3 vLocalPosition;
  out vec4 fragColor;

  vec2 intersectBox(vec3 origin, vec3 direction) {
    vec3 inv = 1.0 / direction;
    vec3 a = (-0.5 - origin) * inv;
    vec3 b = (0.5 - origin) * inv;
    vec3 nearPlane = min(a, b);
    vec3 farPlane = max(a, b);
    return vec2(max(max(nearPlane.x, nearPlane.y), nearPlane.z),
                min(min(farPlane.x, farPlane.y), farPlane.z));
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
      float value = texture(uVolume, position).r;
      if (value > uCutoff) {
        float density = (value - uCutoff) * smoothstep(uCutoff, uCutoff + 0.025, value);
        float sampleAlpha = 1.0 - exp(-density * uOpacity * stepSize * 150.0);
        vec3 gradient = vec3(
          texture(uVolume, position + vec3(texel, 0.0, 0.0)).r - texture(uVolume, position - vec3(texel, 0.0, 0.0)).r,
          texture(uVolume, position + vec3(0.0, texel, 0.0)).r - texture(uVolume, position - vec3(0.0, texel, 0.0)).r,
          texture(uVolume, position + vec3(0.0, 0.0, texel)).r - texture(uVolume, position - vec3(0.0, 0.0, texel)).r
        );
        float lighting = 0.75;
        if (length(gradient) > 0.0001) {
          lighting = 0.55 + 0.45 * abs(dot(normalize(gradient), -direction));
        }
        vec3 color = texture(uPalette, vec2(value, 0.5)).rgb * lighting;
        accumulated += (1.0 - alpha) * sampleAlpha * color;
        alpha += (1.0 - alpha) * sampleAlpha;
      }
      position += direction * stepSize;
    }
    if (alpha < 0.005) discard;
    fragColor = vec4(accumulated / alpha, alpha);
  }
`;

function asset(name) {
  return new URL(name, assetRoot).href;
}

function setStatus(message) {
  status.textContent = message;
  status.hidden = false;
}

function showFallback(message) {
  failed = true;
  root.setAttribute('aria-busy', 'false');
  stage.classList.add('has-fallback');
  fallback.hidden = false;
  canvas.hidden = true;
  setStatus(message);
  for (const element of [cutoff, opacity, rotation, reset]) element.disabled = true;
}

function updateInputImages(sample) {
  document.querySelector('#active-sample-title').replaceChildren(
    document.createTextNode(`${sample.label} `),
    Object.assign(document.createElement('span'), { textContent: `· ${sample.vertebra}` })
  );
  for (const [view, id] of [['ap', '#input-ap'], ['lateral', '#input-lateral']]) {
    const img = document.querySelector(id);
    img.src = asset(sample[view]);
    img.alt = `${sample.label} ${sample.vertebra} ${view === 'ap' ? 'AP' : 'lateral'} input X-ray`;
  }
  fallback.src = asset(sample.fallback);
  fallback.alt = `${sample.label} reconstructed volume slice preview`;
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
    const pair = document.createElement('span');
    pair.className = 'sample-image-pair';
    for (const view of ['ap', 'lateral']) {
      const img = new Image(128, 128);
      img.src = asset(sample[view]);
      img.alt = '';
      img.loading = 'lazy';
      pair.append(img);
    }
    const label = document.createElement('span');
    label.className = 'sample-button-label';
    label.append(Object.assign(document.createElement('span'), { textContent: sample.label }),
      Object.assign(document.createElement('span'), { textContent: sample.vertebra }));
    button.append(pair, label);
    button.addEventListener('click', () => selectSample(sample));
    picker.append(button);
  }
}

function resetView() {
  if (!controls) return;
  controls.reset();
  cutoff.value = '0.12';
  opacity.value = '1.6';
  document.querySelector('#density-value').value = '0.12';
  rotation.checked = false;
  controls.autoRotate = false;
  material.uniforms.uCutoff.value = 0.12;
  material.uniforms.uOpacity.value = 1.6;
  needsRender = true;
}

function resize() {
  if (!renderer) return;
  const width = stage.clientWidth, height = stage.clientHeight;
  if (!width || !height) return;
  // Keep volume ray marching responsive on high-DPI and mobile screens.
  renderer.setPixelRatio(Math.min(devicePixelRatio, width < 600 ? 1 : 1.35));
  renderer.setSize(width, height, false);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
  needsRender = true;
}

async function initRenderer() {
  const gl = canvas.getContext('webgl2', { alpha: true, antialias: false, powerPreference: 'low-power' });
  if (!gl) throw new Error('WebGL 2 is unavailable');
  renderer = new THREE.WebGLRenderer({ canvas, context: gl, alpha: true, antialias: false });
  renderer.debug.onShaderError = () => { throw new Error('Volume shader could not be compiled'); };
  renderer.setClearColor(0x080f1b, 0);
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(36, 1, 0.01, 20);
  camera.position.set(1.2, 0.65, 1.2);
  controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.12;
  controls.minDistance = 0.65;
  controls.maxDistance = 4;
  controls.zoomSpeed = 0.65;
  controls.panSpeed = 0.65;
  controls.autoRotateSpeed = 0.65;
  controls.listenToKeyEvents(canvas);
  controls.saveState();
  controls.addEventListener('change', () => { needsRender = true; });
  canvas.addEventListener('contextmenu', event => event.preventDefault());
  canvas.addEventListener('pointerdown', () => canvas.focus({ preventScroll: true }));
  canvas.addEventListener('webglcontextlost', event => {
    event.preventDefault();
    showFallback('The 3D context was interrupted. Showing volume slices; reload to try again.');
  });
  const response = await fetch(asset(manifest.palette));
  if (!response.ok) throw new Error('Color map unavailable');
  const colors = new Uint8Array(await response.arrayBuffer());
  if (colors.length !== 256 * 4) throw new Error('Invalid color map');
  palette = new THREE.DataTexture(colors, 256, 1, THREE.RGBAFormat);
  palette.minFilter = palette.magFilter = THREE.LinearFilter;
  palette.needsUpdate = true;
  material = new THREE.ShaderMaterial({
    glslVersion: THREE.GLSL3, vertexShader, fragmentShader,
    uniforms: {
      uVolume: { value: null }, uPalette: { value: palette },
      uCamera: { value: cameraLocal }, uCutoff: { value: 0.12 }, uOpacity: { value: 1.6 }
    },
    side: THREE.BackSide, transparent: true, depthWrite: false
  });
  mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), material);
  // Source Z (superior) becomes the viewer's vertical axis.
  mesh.rotation.x = -Math.PI / 2;
  scene.add(mesh);
  mesh.updateMatrixWorld();
  new ResizeObserver(resize).observe(stage);
  resize();
  requestAnimationFrame(animate);
}

function draw() {
  mesh.updateMatrixWorld();
  cameraLocal.copy(camera.position);
  mesh.worldToLocal(cameraLocal);
  renderer.render(scene, camera);
  needsRender = false;
}

function animate() {
  requestAnimationFrame(animate);
  if (failed || !inViewport || document.hidden || !volumeTexture) return;
  controls.update();
  if (needsRender || controls.autoRotate) {
    try { draw(); }
    catch (error) { console.error(error); showFallback('Interactive rendering is unavailable. Showing reconstructed volume slices.'); }
  }
}

async function selectSample(sample) {
  activeSample = sample;
  updateInputImages(sample);
  if (failed) return;
  hasStarted = true;
  const version = ++selectionVersion;
  pendingRequest?.abort();
  pendingRequest = new AbortController();
  const signal = pendingRequest.signal;
  root.setAttribute('aria-busy', 'true');
  setStatus(`Loading ${sample.label.toLowerCase()}…`);
  try {
    if (!rendererReady) rendererReady = initRenderer();
    await rendererReady;
    // A rapid selection may occur while renderer setup awaits the palette.
    if (version !== selectionVersion) return;
    let bytes = volumeCache.get(sample.id);
    if (!bytes) {
      const response = await fetch(asset(sample.volume), { signal });
      if (!response.ok) throw new Error(`Volume download failed (${response.status})`);
      bytes = new Uint16Array(await response.arrayBuffer());
      if (bytes.length !== sample.shape.reduce((a, b) => a * b, 1)) throw new Error('Invalid volume size');
      if (version !== selectionVersion) return;
      volumeCache.set(sample.id, bytes);
    }
    volumeTexture?.dispose();
    volumeTexture = new THREE.Data3DTexture(bytes, ...sample.shape);
    volumeTexture.format = THREE.RedFormat;
    volumeTexture.type = THREE.HalfFloatType;
    volumeTexture.minFilter = volumeTexture.magFilter = THREE.LinearFilter;
    volumeTexture.unpackAlignment = 1;
    volumeTexture.needsUpdate = true;
    material.uniforms.uVolume.value = volumeTexture;
    resetView();
    try { draw(); }
    catch (error) {
      console.error(error);
      showFallback('Interactive rendering is unavailable. Showing reconstructed volume slices.');
      return;
    }
    status.hidden = true;
    root.setAttribute('aria-busy', 'false');
  } catch (error) {
    if (error.name === 'AbortError' || version !== selectionVersion) return;
    console.error(error);
    if (renderer && material) {
      // A network error should be recoverable by choosing another sample.
      setStatus('Could not load this volume. Select a sample to try again.');
      root.setAttribute('aria-busy', 'false');
    } else {
      showFallback('Interactive 3D is unavailable in this browser. Showing reconstructed volume slices.');
    }
  }
}

cutoff.addEventListener('input', () => {
  document.querySelector('#density-value').value = Number(cutoff.value).toFixed(2);
  if (material) material.uniforms.uCutoff.value = Number(cutoff.value);
  needsRender = true;
});
opacity.addEventListener('input', () => {
  if (material) material.uniforms.uOpacity.value = Number(opacity.value);
  needsRender = true;
});
rotation.addEventListener('change', () => {
  if (controls) controls.autoRotate = rotation.checked;
  needsRender = true;
});
reset.addEventListener('click', resetView);
reducedMotion.addEventListener('change', () => {
  if (reducedMotion.matches) {
    rotation.checked = false;
    if (controls) controls.autoRotate = false;
  }
});

try {
  const response = await fetch(asset('manifest.json'));
  if (!response.ok) throw new Error('Sample manifest unavailable');
  manifest = await response.json();
  buildPicker();
  activeSample = manifest.cases[0];
  updateInputImages(activeSample);
  const observer = new IntersectionObserver(entries => {
    inViewport = entries[0].isIntersecting;
    if (inViewport && !hasStarted) selectSample(activeSample);
  }, { rootMargin: '180px' });
  observer.observe(stage);
} catch (error) {
  console.error(error);
  showFallback('Sample data could not be loaded. Please reload the page to try again.');
}
