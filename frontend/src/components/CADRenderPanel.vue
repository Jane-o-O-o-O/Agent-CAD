<template>
  <div
    ref="panelRef"
    class="h-full w-full top-0 ltr:right-0 rtl:left-0 z-50 fixed sm:sticky sm:top-0 sm:right-0 sm:h-[100vh] sm:ml-3 sm:py-3 sm:mr-4"
    :style="{ width: `${Math.max(parentSize / 2, 420)}px`, transition: '0.2s ease-in-out' }">
    <section class="flex h-full min-w-0 flex-col overflow-hidden rounded-xl border border-[var(--border-main)] bg-white shadow-[0px_8px_32px_0px_var(--shadow-S)]">
      <button
        v-if="!document && planSteps.length === 0 && !isBusy"
        class="absolute right-6 top-6 z-10 inline-flex h-8 w-8 items-center justify-center rounded-md bg-white/80 text-slate-600 shadow-sm hover:bg-white"
        title="Close CAD preview"
        @click="$emit('hide')">
        <X :size="16" />
      </button>

      <header v-if="document || planSteps.length > 0 || isBusy" class="flex h-14 items-center justify-between border-b border-slate-200 px-4">
        <div class="min-w-0">
          <div class="truncate text-sm font-medium text-slate-900">{{ document?.title || dxfFile?.filename || 'DXF generation' }}</div>
          <div class="truncate text-xs text-slate-500">
            {{ document ? `${document.entities.length} entities | version ${document.version} | ${document.units}` : briefSummary || statusText }}
          </div>
        </div>
        <div class="flex items-center gap-1">
          <button
            class="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-600 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
            title="Add center slot"
            :disabled="!document || isBusy"
            @click="$emit('addCenterSlot')">
            <Plus :size="16" />
          </button>
          <button
            class="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-600 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
            title="Download DXF"
            :disabled="!document && !dxfFile"
            @click="$emit('downloadDxf')">
            <Download :size="16" />
          </button>
          <button
            class="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-600 hover:bg-slate-100"
            title="Reset CAD preview"
            @click="$emit('reset')">
            <RotateCcw :size="16" />
          </button>
          <button
            class="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-600 hover:bg-slate-100"
            title="Close CAD preview"
            @click="$emit('hide')">
            <X :size="16" />
          </button>
        </div>
      </header>

      <div v-if="planSteps.length > 0" class="border-b border-slate-200 bg-slate-50 px-4 py-3">
        <div class="flex gap-2 overflow-x-auto">
          <div
            v-for="step in planSteps"
            :key="step.id"
            class="min-w-[190px] rounded-md border bg-white px-3 py-2"
            :class="stepCardClass(step.status)">
            <div class="flex items-center gap-2">
              <component
                :is="stepIcon(step.status)"
                class="size-3.5 shrink-0"
                :class="stepIconClass(step.status)" />
              <div class="truncate text-xs font-medium text-slate-900">{{ step.title }}</div>
            </div>
            <div class="mt-1 truncate pl-5 text-[11px] text-slate-500">{{ step.description || stepStatusText(step.status) }}</div>
          </div>
        </div>
      </div>

      <div class="relative min-h-0 flex-1">
        <CADCanvas :document="document" :dxfFile="dxfFile" />
        <div
          v-if="isBusy && !document && !dxfFile"
          class="absolute inset-0 flex items-center justify-center bg-white/60 text-sm text-slate-600">
          <LoadingIndicator text="Preparing CAD drawing" />
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { CheckCircle2, Circle, Download, LoaderCircle, Plus, RotateCcw, X, XCircle } from 'lucide-vue-next';
import CADCanvas from '@/components/CADCanvas.vue';
import LoadingIndicator from '@/components/ui/LoadingIndicator.vue';
import { useResizeObserver } from '@/composables/useResizeObserver';
import type { CADPlanStep, MechanicalCADDocument } from '@/api/cad';
import type { FileInfo } from '@/api/file';

const panelRef = ref<HTMLElement>();
const { size: parentSize } = useResizeObserver(panelRef, {
  target: 'parent',
  property: 'width',
});

const props = defineProps<{
  document?: MechanicalCADDocument | null;
  dxfFile?: FileInfo | null;
  planSteps: Array<CADPlanStep & { status: 'pending' | 'running' | 'done' | 'failed' }>;
  isBusy: boolean;
  briefSummary: string;
}>();

defineEmits<{
  (e: 'hide'): void;
  (e: 'reset'): void;
  (e: 'addCenterSlot'): void;
  (e: 'downloadDxf'): void;
}>();

const statusText = computed(() => (props.isBusy ? 'Generating drawing' : 'CAD mode is ready'));

function stepCardClass(status: 'pending' | 'running' | 'done' | 'failed') {
  if (status === 'done') return 'border-emerald-200';
  if (status === 'running') return 'border-blue-300 shadow-[0_0_0_1px_rgba(59,130,246,0.12)]';
  if (status === 'failed') return 'border-red-200';
  return 'border-slate-200';
}

function stepIcon(status: 'pending' | 'running' | 'done' | 'failed') {
  if (status === 'done') return CheckCircle2;
  if (status === 'running') return LoaderCircle;
  if (status === 'failed') return XCircle;
  return Circle;
}

function stepIconClass(status: 'pending' | 'running' | 'done' | 'failed') {
  if (status === 'done') return 'text-emerald-600';
  if (status === 'running') return 'animate-spin text-blue-600';
  if (status === 'failed') return 'text-red-600';
  return 'text-slate-300';
}

function stepStatusText(status: 'pending' | 'running' | 'done' | 'failed') {
  if (status === 'done') return 'Completed';
  if (status === 'running') return 'Running';
  if (status === 'failed') return 'Failed';
  return 'Pending';
}
</script>
