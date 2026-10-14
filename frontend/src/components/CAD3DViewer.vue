<template>
  <div class="cad-3d relative h-full min-h-[360px] w-full overflow-hidden bg-[#f5f7f6]">
    <div ref="host" class="h-full w-full" aria-label="三维 CAD 模型，可拖动旋转、滚轮缩放、右键平移" />
    <div class="absolute left-3 right-3 top-3 flex flex-wrap items-start justify-between gap-2 pointer-events-none">
      <div class="pointer-events-auto flex flex-wrap gap-1 rounded-lg border border-slate-200 bg-white/95 p-1 shadow-sm" aria-label="模型视角">
        <button v-for="view in views" :key="view.id" class="viewer-button" :title="view.label" @click="setView(view.id)">{{ view.label }}</button>
        <button class="viewer-button" title="适配模型" @click="fitModel()">适配</button>
      </div>
      <div class="pointer-events-auto flex gap-1 rounded-lg border border-slate-200 bg-white/95 p-1 shadow-sm">
        <button class="viewer-button" :aria-pressed="orthographic" @click="toggleProjection">{{ orthographic ? '正交' : '透视' }}</button>
        <button class="viewer-button" :aria-pressed="wireframe" @click="toggleWireframe">{{ wireframe ? '线框' : '实体' }}</button>
        <button class="viewer-button" :aria-pressed="gridVisible" @click="toggleGrid">网格</button>
        <button class="viewer-button" :aria-pressed="axesVisible" @click="toggleAxes">坐标轴</button>
      </div>
    </div>
    <div v-if="!revision && !error" class="pointer-events-none absolute inset-0 flex items-center justify-center px-6">
      <div class="max-w-xs text-center">
        <BoxIcon :size="40" class="mx-auto mb-4 text-emerald-700/50" :stroke-width="1.2" />
        <h2 class="text-base font-medium text-slate-700">从工程需求开始</h2>
        <p class="mt-2 text-sm leading-6 text-slate-500">生成后，在这里旋转、查看和检查真实零件。</p>
      </div>
    </div>
    <div v-if="loading" class="absolute bottom-12 left-3 rounded-md border border-slate-200 bg-white/95 px-3 py-2 text-xs text-slate-600" role="status">正在加载此版本的三维预览…</div>
    <div v-if="error" class="absolute bottom-12 left-3 right-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs leading-5 text-red-700" role="alert">{{ error }}</div>
    <div class="absolute bottom-3 left-3 right-3 flex flex-wrap justify-between gap-2 text-[11px] text-slate-500 pointer-events-none">
      <span class="rounded bg-white/85 px-2 py-1">拖动旋转 · 右键平移 · 滚轮缩放 · 点击选中</span>
      <span v-if="loadedRevision" class="rounded bg-white/85 px-2 py-1">预览版本 {{ loadedRevision.slice(0, 8) }} <span v-if="selected">· 已选中实体</span></span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { Box as BoxIcon } from 'lucide-vue-next';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { downloadCADArtifact, type CADRevision } from '@/api/cadV2';

const props = defineProps<{ revision: CADRevision | null }>();
const host = ref<HTMLDivElement | null>(null);
const loading = ref(false);
const error = ref('');
const orthographic = ref(true);
const wireframe = ref(false);
const gridVisible = ref(true);
const axesVisible = ref(true);
const selected = ref(false);
const loadedRevision = ref('');
const views = [{ id: 'iso', label: '等轴' }, { id: 'front', label: '前' }, { id: 'back', label: '后' }, { id: 'left', label: '左' }, { id: 'right', label: '右' }, { id: 'top', label: '上' }, { id: 'bottom', label: '下' }];
let renderer: THREE.WebGLRenderer | null = null;
let scene: THREE.Scene | null = null;
let camera: THREE.PerspectiveCamera | THREE.OrthographicCamera;
let controls: OrbitControls | null = null;
let root: THREE.Group | null = null;
let grid: THREE.GridHelper | null = null;
let axes: THREE.AxesHelper | null = null;
let resizeObserver: ResizeObserver | null = null;
let selectedMesh: THREE.Mesh | null = null;
let request: AbortController | null = null;
let loadEpoch = 0;
let frame = 0;
let modelRadius = 50;
let pointerStart = { x: 0, y: 0 };
const bounds = new THREE.Box3();
const raycaster = new THREE.Raycaster();

function disposeObject(object: THREE.Object3D) {
  const disposed = new Set<unknown>();
  object.traverse(child => {
    const mesh = child as THREE.Mesh;
    if (mesh.geometry && !disposed.has(mesh.geometry)) { mesh.geometry.dispose(); disposed.add(mesh.geometry); }
    const materials = mesh.material ? (Array.isArray(mesh.material) ? mesh.material : [mesh.material]) : [];
    for (const material of materials) {
      if (disposed.has(material)) continue;
      for (const value of Object.values(material)) if (value instanceof THREE.Texture && !disposed.has(value)) { value.dispose(); disposed.add(value); }
      material.dispose(); disposed.add(material);
    }
  });
}
function size() { return { width: Math.max(host.value?.clientWidth || 1, 1), height: Math.max(host.value?.clientHeight || 1, 1) }; }
function updateProjection() {
  if (!renderer || !camera) return;
  const { width, height } = size();
  renderer.setSize(width, height, false);
  const aspect = width / height;
  if (camera instanceof THREE.PerspectiveCamera) camera.aspect = aspect;
  else { const span = modelRadius * 1.35; camera.left = -span * aspect; camera.right = span * aspect; camera.top = span; camera.bottom = -span; }
  camera.near = Math.max(modelRadius / 10000, 0.00001);
  camera.far = Math.max(modelRadius * 1000, 1000);
  camera.updateProjectionMatrix();
}
function createCamera(position?: THREE.Vector3, target?: THREE.Vector3) {
  controls?.dispose();
  camera = orthographic.value ? new THREE.OrthographicCamera(-100, 100, 100, -100, 0.01, 100000) : new THREE.PerspectiveCamera(40, 1, 0.01, 100000);
  camera.up.set(0, 0, 1);
  camera.position.copy(position || new THREE.Vector3(100, -100, 85));
  if (!renderer) return;
  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.1;
  controls.screenSpacePanning = true;
  if (target) controls.target.copy(target);
  controls.update(); updateProjection();
}
function fitModel(direction?: THREE.Vector3) {
  if (!camera || !controls) return;
  const center = root ? bounds.getCenter(new THREE.Vector3()) : new THREE.Vector3();
  const vector = direction || camera.position.clone().sub(controls.target).normalize();
  if (vector.lengthSq() < 0.01) vector.set(1, -1, 0.8).normalize();
  const aspect = size().width / size().height;
  const distance = modelRadius * 3.4 / Math.min(aspect, 1);
  camera.position.copy(center).addScaledVector(vector.normalize(), distance);
  if (camera instanceof THREE.OrthographicCamera) camera.zoom = Math.min(aspect, 1);
  controls.target.copy(center); updateProjection(); controls.update();
}
function setView(view: string) {
  if (!camera) return;
  const directions: Record<string, [number, number, number]> = { iso: [1, -1, 0.8], front: [0, -1, 0], back: [0, 1, 0], left: [-1, 0, 0], right: [1, 0, 0], top: [0, 0, 1], bottom: [0, 0, -1] };
  camera.up.set(0, view === 'top' || view === 'bottom' ? 1 : 0, view === 'top' || view === 'bottom' ? 0 : 1);
  fitModel(new THREE.Vector3(...(directions[view] || directions.iso)));
}
function toggleProjection() { const position = camera?.position.clone(); const target = controls?.target.clone(); orthographic.value = !orthographic.value; createCamera(position, target); fitModel(); }
function toggleGrid() { gridVisible.value = !gridVisible.value; if (grid) grid.visible = gridVisible.value; }
function toggleAxes() { axesVisible.value = !axesVisible.value; if (axes) axes.visible = axesVisible.value; }
function toggleWireframe() {
  wireframe.value = !wireframe.value;
  root?.traverse(child => { if (child instanceof THREE.Mesh) for (const material of Array.isArray(child.material) ? child.material : [child.material]) if (material instanceof THREE.MeshStandardMaterial) material.wireframe = wireframe.value; });
}
function unselect() {
  if (selectedMesh) for (const material of Array.isArray(selectedMesh.material) ? selectedMesh.material : [selectedMesh.material]) if (material instanceof THREE.MeshStandardMaterial) material.emissive.setHex(0);
  selectedMesh = null; selected.value = false;
}
function onPointerDown(event: PointerEvent) { pointerStart = { x: event.clientX, y: event.clientY }; }
function onPointerUp(event: PointerEvent) {
  if (event.button !== 0 || !root || !renderer || !camera || Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y) > 5) return;
  const rect = renderer.domElement.getBoundingClientRect();
  raycaster.setFromCamera(new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1), camera);
  const hit = raycaster.intersectObject(root, true).find(item => item.object instanceof THREE.Mesh);
  unselect();
  if (hit) { selectedMesh = hit.object as THREE.Mesh; selected.value = true; for (const material of Array.isArray(selectedMesh.material) ? selectedMesh.material : [selectedMesh.material]) if (material instanceof THREE.MeshStandardMaterial) material.emissive.setHex(0x214f37); }
}
function updateHelpers() {
  if (!scene) return;
  if (grid) { scene.remove(grid); disposeObject(grid); }
  if (axes) { scene.remove(axes); disposeObject(axes); }
  const extent = Math.max(modelRadius * 4, 1);
  grid = new THREE.GridHelper(extent, 20, 0xb7c9bf, 0xdce5df);
  grid.rotation.x = Math.PI / 2;
  grid.position.z = root ? bounds.min.z - extent * 0.0001 : 0;
  grid.visible = gridVisible.value;
  axes = new THREE.AxesHelper(Math.max(modelRadius * 1.2, 1)); axes.visible = axesVisible.value;
  scene.add(grid, axes);
}
async function loadRevision() {
  const epoch = ++loadEpoch;
  request?.abort(); request = new AbortController();
  const revision = props.revision;
  if (!renderer || !scene) return;
  error.value = ''; loading.value = false;
  if (!revision) { if (root) { scene.remove(root); disposeObject(root); root = null; } unselect(); loadedRevision.value = ''; modelRadius = 50; updateHelpers(); fitModel(); return; }
  loading.value = true;
  try {
    const blob = await downloadCADArtifact(revision.document_id, revision.id, 'glb', request.signal);
    if (epoch !== loadEpoch) return;
    const gltf = await new GLTFLoader().parseAsync(await blob.arrayBuffer(), '');
    if (epoch !== loadEpoch) { disposeObject(gltf.scene); return; }
    const candidate = gltf.scene;
    // build123d exports standard GLB in metres/Y-up; CAD views use mm/Z-up.
    candidate.scale.multiplyScalar(1000);
    candidate.rotateX(Math.PI / 2);
    candidate.updateMatrixWorld(true);
    const candidateBounds = new THREE.Box3().setFromObject(candidate);
    if (candidateBounds.isEmpty() || ![...candidateBounds.min.toArray(), ...candidateBounds.max.toArray()].every(Number.isFinite)) { disposeObject(candidate); throw new Error('预览文件未包含有效的三维几何。'); }
    candidate.traverse(child => {
      if (!(child instanceof THREE.Mesh)) return;
      const oldMaterials = Array.isArray(child.material) ? child.material : [child.material];
      child.material = new THREE.MeshStandardMaterial({ color: 0x83a797, roughness: 0.45, metalness: 0.2, side: THREE.DoubleSide, wireframe: wireframe.value });
      oldMaterials.forEach(material => {
        for (const value of Object.values(material)) if (value instanceof THREE.Texture) value.dispose();
        material.dispose();
      });
    });
    unselect();
    if (root) { scene.remove(root); disposeObject(root); }
    root = candidate; scene.add(root); bounds.copy(candidateBounds);
    modelRadius = Math.max(bounds.getSize(new THREE.Vector3()).length() / 2, 0.01);
    loadedRevision.value = revision.id;
    updateHelpers(); setView('iso');
  } catch (reason: unknown) {
    if (epoch === loadEpoch) error.value = reason instanceof Error ? reason.message : '无法加载模型预览，请重试或下载 STEP。';
  } finally { if (epoch === loadEpoch) loading.value = false; }
}
onMounted(() => {
  if (!host.value) return;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    host.value.appendChild(renderer.domElement);
    scene = new THREE.Scene(); scene.background = new THREE.Color('#f5f7f6');
    scene.add(new THREE.HemisphereLight(0xffffff, 0x75857c, 2.2));
    const key = new THREE.DirectionalLight(0xffffff, 2.5); key.position.set(100, -120, 160); scene.add(key);
    const fill = new THREE.DirectionalLight(0xffffff, 1); fill.position.set(-100, 40, 50); scene.add(fill);
    createCamera(); updateHelpers();
    renderer.domElement.addEventListener('pointerdown', onPointerDown);
    renderer.domElement.addEventListener('pointerup', onPointerUp);
    resizeObserver = new ResizeObserver(updateProjection); resizeObserver.observe(host.value);
    const animate = () => { frame = requestAnimationFrame(animate); controls?.update(); if (renderer && scene && camera) renderer.render(scene, camera); }; animate();
    void loadRevision();
  } catch { error.value = '当前浏览器无法启动 WebGL 三维预览。你仍可查看参数、验证报告和下载工程文件。'; }
});
watch(() => [props.revision?.document_id, props.revision?.id], () => { void loadRevision(); });
onBeforeUnmount(() => {
  loadEpoch += 1; request?.abort(); cancelAnimationFrame(frame); resizeObserver?.disconnect(); controls?.dispose();
  renderer?.domElement.removeEventListener('pointerdown', onPointerDown); renderer?.domElement.removeEventListener('pointerup', onPointerUp);
  if (scene) disposeObject(scene);
  renderer?.dispose(); renderer?.forceContextLoss(); renderer?.domElement.remove(); renderer = null; scene = null; root = null;
});
</script>

<style scoped>
.viewer-button { border-radius: 5px; padding: 5px 8px; color: #475569; font-size: 11px; line-height: 18px; }
.viewer-button:hover { background: #f1f5f3; color: #0f5132; }
.viewer-button[aria-pressed="true"] { background: #e7f0eb; color: #166534; }
.cad-3d :deep(canvas) { display: block; touch-action: none; }
</style>
