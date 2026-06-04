<template>
  <div class="flex h-full w-full min-w-0 bg-[#f3f5f4]">
    <section class="flex h-full w-[430px] min-w-[340px] flex-col border-r border-slate-200 bg-white">
      <header class="flex h-14 items-center justify-between border-b border-slate-200 px-4">
        <div class="min-w-0">
          <h1 class="truncate text-[15px] font-semibold text-slate-900">Agent-CAD</h1>
          <p class="truncate text-xs text-slate-500">Mechanical 2D drawing</p>
        </div>
        <button
          class="inline-flex h-8 w-8 items-center justify-center rounded-md border border-slate-200 text-slate-600 hover:bg-slate-50"
          title="New CAD document"
          @click="resetWorkspace">
          <SquarePen :size="17" />
        </button>
      </header>

      <div class="flex-1 overflow-auto px-4 py-4">
        <div class="space-y-3">
          <div
            v-for="message in messages"
            :key="message.id"
            class="rounded-md border px-3 py-2 text-sm leading-6"
            :class="message.role === 'user'
              ? 'border-slate-200 bg-slate-50 text-slate-800'
              : 'border-emerald-200 bg-emerald-50 text-emerald-950'">
            {{ message.content }}
          </div>
        </div>

        <div v-if="planSteps.length > 0" class="mt-4 border-t border-slate-200 pt-4">
          <div class="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">CAD process</div>
          <div class="space-y-2">
            <div
              v-for="step in planSteps"
              :key="step.id"
              class="rounded-md border px-3 py-2"
              :class="step.status === 'done'
                ? 'border-emerald-200 bg-emerald-50'
                : step.status === 'running'
                  ? 'border-blue-200 bg-blue-50'
                  : 'border-slate-200 bg-white'">
              <div class="flex items-center justify-between gap-2">
                <div class="truncate text-sm font-medium text-slate-900">{{ step.title }}</div>
                <div class="text-xs text-slate-500">{{ step.status }}</div>
              </div>
              <div class="mt-1 text-xs leading-5 text-slate-600">{{ step.description }}</div>
            </div>
          </div>
        </div>
      </div>

      <form class="border-t border-slate-200 p-3" @submit.prevent="handleGenerate">
        <ChatBoxFiles ref="cadFilesRef" :attachments="attachments" :liteUpload="true" />
        <textarea
          v-model="prompt"
          class="h-28 w-full resize-none rounded-md border border-slate-300 bg-white px-3 py-2 text-sm leading-6 text-slate-900 outline-none focus:border-slate-500"
          placeholder="Draw a 120x80x10 mounting plate, R5 corners, four M6 holes, edge offset 12mm" />
        <div class="mt-2 flex items-center justify-between gap-2">
          <div class="flex items-center gap-2">
            <button
              type="button"
              class="inline-flex h-9 w-9 items-center justify-center rounded-md border border-slate-300 text-slate-700 hover:bg-slate-50"
              title="Attach reference file"
              @click="uploadFile">
              <Paperclip :size="16" />
            </button>
            <button
              type="button"
              class="inline-flex h-9 items-center gap-2 rounded-md border border-slate-300 px-3 text-sm text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
              :disabled="!document"
              @click="addCenterSlot">
              <Plus :size="16" />
              Add slot
            </button>
          </div>
          <button
            type="submit"
            class="inline-flex h-9 items-center gap-2 rounded-md bg-slate-900 px-3 text-sm font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-400"
            :disabled="isBusy || !prompt.trim() || !allFilesUploaded">
            <Send :size="16" />
            Generate
          </button>
        </div>
      </form>
    </section>

    <section class="flex min-w-0 flex-1 flex-col">
      <header class="flex h-14 items-center justify-between border-b border-slate-200 bg-white px-4">
        <div class="min-w-0">
          <div class="truncate text-sm font-medium text-slate-900">{{ document?.title || 'No drawing' }}</div>
          <div class="truncate text-xs text-slate-500">
            {{ document ? `${document.entities.length} entities · version ${document.version} · ${document.units}` : 'Generate a drawing to begin' }}
          </div>
        </div>
        <div class="flex items-center gap-2">
          <button
            class="inline-flex h-9 items-center gap-2 rounded-md border border-slate-300 px-3 text-sm text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="!document"
            @click="downloadDxf">
            <Download :size="16" />
            DXF
          </button>
        </div>
      </header>

      <div class="min-h-0 flex-1">
        <CADCanvas :document="document" />
      </div>

      <footer class="grid h-24 grid-cols-3 border-t border-slate-200 bg-white text-xs text-slate-600">
        <div class="border-r border-slate-200 px-4 py-3">
          <div class="font-medium text-slate-900">Brief</div>
          <div class="mt-1 truncate">{{ briefSummary }}</div>
        </div>
        <div class="border-r border-slate-200 px-4 py-3">
          <div class="font-medium text-slate-900">Supported</div>
          <div class="mt-1 truncate">Plate, holes, slots, centerlines, dimensions, notes</div>
        </div>
        <div class="px-4 py-3">
          <div class="font-medium text-slate-900">Boundary</div>
          <div class="mt-1 truncate">Mechanical 2D MVP, DXF export only</div>
        </div>
      </footer>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { Download, Paperclip, Plus, Send, SquarePen } from 'lucide-vue-next';
import CADCanvas from '@/components/CADCanvas.vue';
import ChatBoxFiles from '@/components/ChatBoxFiles.vue';
import {
  applyCADOperation,
  createCADDocument,
  createCADPlanFromPrompt,
  downloadCADDocumentDxf,
  type MechanicalCADDocument,
  type CADPlanStep,
} from '@/api/cad';
import type { FileInfo } from '@/api/file';
import { showErrorToast, showSuccessToast } from '@/utils/toast';

const prompt = ref('Draw a 120x80x10 mounting plate, R5 corners, four M6 holes, edge offset 12mm');
const document = ref<MechanicalCADDocument | null>(null);
const isBusy = ref(false);
const lastSubmittedPrompt = ref('');
const lastSubmittedAttachmentIds = ref('');
const attachments = ref<FileInfo[]>([]);
const cadFilesRef = ref<InstanceType<typeof ChatBoxFiles> | null>(null);
const planSteps = ref<Array<CADPlanStep & { status: 'pending' | 'running' | 'done' }>>([]);
const messages = ref([
  {
    id: 'welcome',
    role: 'assistant',
    content: 'Describe a mechanical 2D part. This MVP supports plate size, corner radius, holes, slots, notes, and DXF export.',
  },
]);

const briefSummary = computed(() => {
  const brief = document.value?.brief;
  if (!brief) return 'No design brief yet';
  return `${brief.part_type || 'part'} · ${brief.features.length} parsed features`;
});
const allFilesUploaded = computed(() => cadFilesRef.value?.isAllUploaded ?? true);

onMounted(() => {
  const initialPrompt = window.history.state?.prompt;
  if (typeof initialPrompt === 'string' && initialPrompt.trim()) {
    prompt.value = initialPrompt;
    void handleGenerate();
  }
});

async function handleGenerate() {
  const text = prompt.value.trim();
  if (!text || isBusy.value) return;
  if (!allFilesUploaded.value) {
    showErrorToast('Please wait for file uploads to finish');
    return;
  }
  const files = cadFilesRef.value?.getFiles?.() ?? attachments.value;
  const attachmentIds = files.map(file => file.file_id).join(',');
  if (text === lastSubmittedPrompt.value && attachmentIds === lastSubmittedAttachmentIds.value) {
    showErrorToast('This CAD request has already been generated');
    return;
  }
  isBusy.value = true;
  lastSubmittedPrompt.value = text;
  lastSubmittedAttachmentIds.value = attachmentIds;
  messages.value.push({
    id: crypto.randomUUID(),
    role: 'user',
    content: files.length > 0 ? `${text}\n\nAttached files: ${files.map(file => file.filename).join(', ')}` : text,
  });
  try {
    const plan = await createCADPlanFromPrompt(text, files);
    planSteps.value = plan.steps.map(step => ({ ...step, status: 'pending' }));
    messages.value.push({ id: crypto.randomUUID(), role: 'assistant', content: `I parsed the request into ${plan.steps.length} CAD operations and will draw them step by step.` });

    const created = await createCADDocument(plan.title);
    document.value = {
      ...created.document,
      brief: plan.brief,
    };

    for (const step of planSteps.value) {
      step.status = 'running';
      await delay(180);
      const result = await applyCADOperation(document.value.id, step.operation);
      document.value = {
        ...result.document,
        brief: plan.brief,
      };
      step.status = 'done';
      await delay(120);
    }
    messages.value.push({ id: crypto.randomUUID(), role: 'assistant', content: plan.message });
    showSuccessToast('CAD drawing generated');
  } catch (error: any) {
    showErrorToast(error?.message || 'Failed to generate CAD drawing');
  } finally {
    isBusy.value = false;
  }
}

function delay(ms: number) {
  return new Promise(resolve => window.setTimeout(resolve, ms));
}

function uploadFile() {
  cadFilesRef.value?.uploadFile();
}

async function addCenterSlot() {
  if (!document.value) return;
  const plate = document.value.brief?.features.find(feature => feature.type === 'base_plate');
  const width = Number(plate?.width || 120);
  const height = Number(plate?.height || 80);
  try {
    const result = await applyCADOperation(document.value.id, {
      operation: 'add_slot',
      params: {
        center: [width / 2, height / 2],
        length: Math.min(width, height) * 0.38,
        width: Math.min(width, height) * 0.12,
        rotation: 0,
      },
    });
    document.value = result.document;
    messages.value.push({ id: crypto.randomUUID(), role: 'assistant', content: 'Added a centered slot to the active drawing.' });
  } catch (error: any) {
    showErrorToast(error?.message || 'Failed to add slot');
  }
}

async function downloadDxf() {
  if (!document.value) return;
  try {
    const blob = await downloadCADDocumentDxf(document.value.id);
    const url = URL.createObjectURL(blob);
    const link = window.document.createElement('a');
    link.href = url;
    link.download = `${document.value.title.replace(/\s+/g, '_').toLowerCase() || document.value.id}.dxf`;
    link.click();
    URL.revokeObjectURL(url);
  } catch (error: any) {
    showErrorToast(error?.message || 'Failed to download DXF');
  }
}

function resetWorkspace() {
  document.value = null;
  attachments.value = [];
  planSteps.value = [];
  lastSubmittedPrompt.value = '';
  lastSubmittedAttachmentIds.value = '';
  messages.value = [
    {
      id: 'welcome',
      role: 'assistant',
      content: 'Describe a mechanical 2D part. This MVP supports plate size, corner radius, holes, slots, notes, and DXF export.',
    },
  ];
}
</script>
