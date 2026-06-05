<template>
  <div class="cad-canvas-surface relative h-full w-full overflow-hidden">
    <div ref="viewerRef" class="h-full w-full" aria-label="Professional DXF preview" />

    <div
      v-if="!document && !dxfFile"
      class="absolute inset-0 flex items-center justify-center bg-[#f5f7f6] text-sm text-slate-500">
      Generate a drawing to preview the DXF
    </div>

    <div
      v-else-if="status !== 'ready'"
      class="absolute left-3 top-3 rounded-md border border-slate-200 bg-white/90 px-3 py-2 text-xs text-slate-600 shadow-sm">
      {{ statusText }}
    </div>

    <div
      v-if="errorMessage"
      class="absolute inset-x-3 bottom-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 shadow-sm">
      {{ errorMessage }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { downloadCADDocumentDxf, type MechanicalCADDocument } from '@/api/cad';
import { downloadFile, type FileInfo } from '@/api/file';
// @ts-ignore dxf-viewer depends on three at runtime, but this app does not ship top-level three types.
import { Color } from 'three';
import type { DxfViewer as DxfViewerClass } from 'dxf-viewer';

const props = defineProps<{
  document?: MechanicalCADDocument | null;
  dxfFile?: FileInfo | null;
}>();

type ViewerStatus = 'idle' | 'exporting' | 'loading' | 'ready' | 'error';

const viewerRef = ref<HTMLElement | null>(null);
const status = ref<ViewerStatus>('idle');
const progressPhase = ref<string>('');
const errorMessage = ref('');

let viewer: DxfViewerClass | null = null;
let objectUrl: string | null = null;
let loadToken = 0;

const statusText = computed(() => {
  if (status.value === 'exporting') return 'Exporting DXF for preview...';
  if (status.value === 'loading') {
    return progressPhase.value ? `Rendering DXF: ${progressPhase.value}` : 'Rendering DXF...';
  }
  if (status.value === 'error') return 'DXF preview failed';
  return '';
});

watch(
  () => [props.document?.id, props.document?.version, props.dxfFile?.file_id] as const,
  () => {
    void renderDocument();
  },
  { immediate: true },
);

onMounted(() => {
  void renderDocument();
});

onBeforeUnmount(() => {
  loadToken += 1;
  cleanupViewer();
  revokeObjectUrl();
});

async function renderDocument() {
  const document = props.document;
  const dxfFile = props.dxfFile;
  const token = ++loadToken;
  errorMessage.value = '';
  progressPhase.value = '';

  if ((!document && !dxfFile) || !viewerRef.value) {
    status.value = 'idle';
    cleanupViewer();
    revokeObjectUrl();
    return;
  }

  status.value = 'exporting';
  await nextTick();

  try {
    const blob = dxfFile
      ? await downloadFile(dxfFile.file_id)
      : await downloadCADDocumentDxf(document!.id);
    if (token !== loadToken) return;

    revokeObjectUrl();
    objectUrl = URL.createObjectURL(blob);
    status.value = 'loading';

    const currentViewer = await ensureViewer();
    currentViewer.Clear();
    await currentViewer.Load({
      url: objectUrl,
      fonts: ['/fonts/cad-preview.ttf'],
      progressCbk: (phase) => {
        progressPhase.value = phase;
      },
    });

    if (token !== loadToken) return;
    currentViewer.Render();
    status.value = 'ready';
  } catch (error: any) {
    if (token !== loadToken) return;
    status.value = 'error';
    errorMessage.value = error?.message || 'Unable to render this DXF preview.';
  }
}

async function ensureViewer() {
  if (!viewerRef.value) {
    throw new Error('DXF viewer container is not ready');
  }

  if (!viewer) {
    const { DxfViewer } = await import('dxf-viewer');
    viewer = new DxfViewer(viewerRef.value, {
      autoResize: true,
      antialias: true,
      clearColor: new Color('#f8faf8'),
      clearAlpha: 0,
      canvasAlpha: true,
      colorCorrection: true,
      blackWhiteInversion: true,
      sceneOptions: {
        suppressPaperSpace: true,
        textOptions: {
          curveSubdivision: 4,
        },
      },
    });
  }

  return viewer;
}

function cleanupViewer() {
  if (!viewer) return;
  viewer.Destroy();
  viewer = null;
}

function revokeObjectUrl() {
  if (!objectUrl) return;
  URL.revokeObjectURL(objectUrl);
  objectUrl = null;
}
</script>

<style scoped>
.cad-canvas-surface {
  background-color: #f8faf8;
  background-image:
    linear-gradient(rgba(37, 99, 70, 0.08) 1px, transparent 1px),
    linear-gradient(90deg, rgba(37, 99, 70, 0.08) 1px, transparent 1px),
    linear-gradient(rgba(37, 99, 70, 0.14) 1px, transparent 1px),
    linear-gradient(90deg, rgba(37, 99, 70, 0.14) 1px, transparent 1px);
  background-position: center;
  background-size: 20px 20px, 20px 20px, 100px 100px, 100px 100px;
}
</style>
