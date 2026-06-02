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
          <div class="truncate text-sm font-medium text-slate-900">{{ document?.title || 'CAD preview' }}</div>
          <div class="truncate text-xs text-slate-500">
            {{ document ? `${document.entities.length} entities · version ${document.version} · ${document.units}` : statusText }}
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
            :disabled="!document"
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
            class="min-w-[180px] rounded-md border bg-white px-3 py-2"
            :class="step.status === 'done'
              ? 'border-emerald-200'
              : step.status === 'running'
                ? 'border-blue-300'
                : 'border-slate-200'">
            <div class="truncate text-xs font-medium text-slate-900">{{ step.title }}</div>
            <div class="mt-1 truncate text-[11px] text-slate-500">{{ step.status }}</div>
          </div>
        </div>
      </div>

      <div class="relative min-h-0 flex-1">
        <CADCanvas :document="document" />
        <div
          v-if="isBusy && !document"
          class="absolute inset-0 flex items-center justify-center bg-white/60 text-sm text-slate-600">
          <LoadingIndicator text="Preparing CAD drawing" />
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { Download, Plus, RotateCcw, X } from 'lucide-vue-next';
import CADCanvas from '@/components/CADCanvas.vue';
import LoadingIndicator from '@/components/ui/LoadingIndicator.vue';
import { useResizeObserver } from '@/composables/useResizeObserver';
import type { CADPlanStep, MechanicalCADDocument } from '@/api/cad';

const panelRef = ref<HTMLElement>();
const { size: parentSize } = useResizeObserver(panelRef, {
  target: 'parent',
  property: 'width',
});

const props = defineProps<{
  document?: MechanicalCADDocument | null;
  planSteps: Array<CADPlanStep & { status: 'pending' | 'running' | 'done' }>;
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
</script>
