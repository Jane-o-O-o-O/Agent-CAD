<template>
  <div class="relative flex h-full min-h-[360px] flex-col bg-slate-100" aria-label="二维工程图预览">
    <div class="flex shrink-0 items-center justify-between border-b border-slate-200 bg-white px-4 py-2 text-xs text-slate-600">
      <span>三视图 · 第三角投影 · 单位 mm</span>
      <div class="flex items-center gap-2"><button aria-label="缩小工程图" @click="zoom = Math.max(.5, zoom - .25)">−</button><span>{{ Math.round(zoom * 100) }}%</span><button aria-label="放大工程图" @click="zoom = Math.min(4, zoom + .25)">＋</button><button class="ml-2" @click="zoom = 1">适配图纸</button></div>
    </div>
    <div class="min-h-0 flex-1 overflow-auto p-4">
      <p v-if="loading" role="status" class="p-6 text-sm text-slate-500">正在加载此版本的工程图…</p>
      <p v-else-if="error" role="alert" class="p-6 text-sm text-red-700">{{ error }}</p>
      <img v-else-if="url" :src="url" :style="{ width: `${zoom * 100}%`, maxWidth: 'none' }" class="mx-auto block bg-white shadow-sm" alt="当前模型版本的前视、俯视、右视工程图，包含可见线、隐藏线、中心线和实测尺寸" />
      <p v-else class="p-6 text-center text-sm text-slate-500">此历史版本尚无二维工程图。重建零件后即可查看。</p>
    </div>
    <p v-if="loadedRevision" class="border-t border-slate-200 bg-white px-4 py-2 text-[11px] text-slate-500">图纸版本 {{ loadedRevision.slice(0, 8) }} · DXF 保留可编辑的图层与尺寸标注</p>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import { downloadCADArtifact, type CADRevision } from '@/api/cadV2';
const props = defineProps<{ revision: CADRevision | null }>();
const url = ref(''), error = ref(''), loading = ref(false), loadedRevision = ref(''), zoom = ref(1);
let epoch = 0, request: AbortController | null = null;
function release() { if (url.value) URL.revokeObjectURL(url.value); url.value = ''; loadedRevision.value = ''; }
watch(() => props.revision?.id, async () => {
  const token = ++epoch; request?.abort(); request = new AbortController(); release(); error.value = ''; loading.value = false;
  const revision = props.revision;
  if (!revision?.artifacts.svg) return;
  loading.value = true;
  try {
    const blob = await downloadCADArtifact(revision.document_id, revision.id, 'svg', request.signal);
    if (token !== epoch) return;
    // Loading SVG as an image disables active scripts; never inject model markup into the DOM.
    url.value = URL.createObjectURL(new Blob([blob], { type: 'image/svg+xml' }));
    loadedRevision.value = revision.id; zoom.value = 1;
  } catch (reason) { if (token === epoch) error.value = reason instanceof Error ? reason.message : '工程图加载失败'; }
  finally { if (token === epoch) loading.value = false; }
}, { immediate: true });
onBeforeUnmount(() => { epoch++; request?.abort(); release(); });
</script>
