<template>
  <div class="v2-workspace flex h-full min-w-0 flex-1 flex-col bg-[#f7f9f8] text-slate-800">
    <header class="flex min-h-[64px] flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white px-5 py-3">
      <div class="flex min-w-0 items-center gap-3">
        <div class="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-emerald-50 text-emerald-800"><Box :size="21" :stroke-width="1.7" /></div>
        <div class="min-w-0"><h1 class="truncate text-sm font-semibold">{{ document?.name || 'CAD 三维工作台' }}</h1><p class="mt-0.5 text-[11px] text-slate-500">参数化建模 · 精确几何 · 版本管理</p></div>
        <span class="rounded border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-800">V2</span>
      </div>
      <div class="flex items-center gap-2">
        <RouterLink v-if="!standalone" to="/chat/cad" class="text-xs text-slate-500 hover:text-slate-800">二维 DXF</RouterLink>
        <button class="secondary-button" :disabled="busy" @click="startNew"><Plus :size="14" />新建零件</button>
        <select v-model="exportFormat" class="rounded-md border border-slate-200 bg-white px-2 py-2 text-xs" aria-label="导出格式">
          <option value="step">STEP 精确实体</option><option value="stl">STL 网格</option><option value="glb">GLB 预览</option><option value="ir">CAD IR 定义</option><option value="python">Python 重建脚本</option>
        </select>
        <button class="primary-button" :disabled="!revision || downloading || loadingDocument" @click="download"><Download :size="14" />{{ downloading ? '下载中' : '导出' }}</button>
      </div>
    </header>

    <div class="v2-body grid min-h-0 flex-1 grid-cols-[300px_minmax(280px,1fr)_280px]">
      <aside class="flex min-h-0 flex-col border-r border-slate-200 bg-white">
        <div class="border-b border-slate-100 p-4">
          <label class="section-label" for="document-select">零件文档</label>
          <select id="document-select" class="mt-2 w-full rounded-lg border border-slate-200 bg-white px-2 py-2 text-xs" :value="document?.id || ''" :disabled="busy" @change="chooseDocument">
            <option value="" disabled>选择已保存的零件</option><option v-for="item in documents" :key="item.id" :value="item.id">{{ item.name }}</option>
          </select>
        </div>
        <div class="min-h-0 flex-1 overflow-y-auto p-4">
          <div class="flex items-center justify-between"><h2 class="section-label">设计需求</h2><span class="text-[10px] text-slate-400">尺寸单位 {{ revision?.ir.units || 'mm' }}</span></div>
          <div v-if="document" class="mt-3 flex rounded-lg bg-slate-100 p-1">
            <button class="mode-button" :class="intent === 'edit' ? 'is-active' : ''" :disabled="busy" @click="intent = 'edit'">修改当前零件</button>
            <button class="mode-button" :class="intent === 'create' ? 'is-active' : ''" :disabled="busy" @click="intent = 'create'">生成新零件</button>
          </div>
          <form class="mt-3" @submit.prevent="submitPrompt">
            <label for="cad-v2-prompt" class="sr-only">零件需求或修改要求</label>
            <textarea id="cad-v2-prompt" v-model="prompt" class="h-44 w-full resize-y rounded-xl border border-slate-200 bg-slate-50/60 px-3 py-3 text-sm leading-6 outline-none focus:border-emerald-700 focus:bg-white" :placeholder="intent === 'edit' && document ? '例如：将四个孔的直径改成 8 mm，其他保持不变。' : defaultPrompt" :disabled="busy" />
            <p v-if="historical && intent === 'edit'" class="mt-2 text-xs leading-5 text-amber-700">正在查看历史版本。请先恢复该版本，或选择当前版本再修改。</p>
            <button type="submit" class="primary-button mt-3 w-full justify-center" :disabled="busy || !prompt.trim() || (historical && intent === 'edit')"><LoaderCircle v-if="busy" :size="15" class="animate-spin" /><Send v-else :size="15" />{{ busy ? '任务进行中' : intent === 'edit' && document ? '修改并重新建模' : '生成三维零件' }}</button>
          </form>
          <div v-if="!document" class="mt-4">
            <p class="text-[11px] text-slate-400">试试这些完整需求</p>
            <button v-for="example in examples" :key="example.name" class="mt-2 block w-full rounded-lg border border-slate-200 px-3 py-2 text-left text-xs text-slate-600 hover:border-emerald-300 hover:bg-emerald-50/40" :disabled="busy" @click="prompt = example.prompt">{{ example.name }}<ArrowUpRight :size="12" class="float-right text-slate-400" /></button>
          </div>

          <section v-if="job" class="mt-6 border-t border-slate-100 pt-4" aria-label="真实任务状态">
            <div class="flex items-center justify-between gap-2"><h2 class="section-label">执行过程</h2><span class="status-chip" :class="statusClass(job.status)">{{ statusLabel(job.status) }}</span></div>
            <p class="mt-3 text-xs text-slate-600">{{ stageLabel(job.stage) }}</p>
            <div class="mt-2 h-1.5 overflow-hidden rounded bg-slate-100" role="progressbar" :aria-valuenow="progress" aria-valuemin="0" aria-valuemax="100" aria-label="后端任务进度"><div class="h-full rounded bg-emerald-700 transition-[width]" :style="{ width: `${progress}%` }" /></div>
            <div class="mt-2 flex justify-between text-[10px] text-slate-400"><span>任务 {{ job.id.slice(0, 8) }}</span><span>{{ progress }}%</span></div>
            <ol class="mt-3 space-y-2">
              <li v-for="(item, index) in observedStages" :key="`${item.at}-${index}`" class="flex items-start gap-2 text-xs text-slate-500"><span class="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-300" /><span>{{ stageLabel(item.stage) }} <span class="text-[10px] text-slate-400">· {{ statusLabel(item.status) }}</span></span></li>
            </ol>
            <div v-if="!terminalCADJob(job)" class="mt-3 flex gap-2"><button class="secondary-button" @click="cancel">取消任务</button><button v-if="connectionIssue" class="secondary-button" @click="reconnect">重新连接</button></div>
            <p v-if="job.repair_history?.length" class="mt-3 text-xs text-amber-700">已记录 {{ job.repair_history.length }} 次修复尝试，可在验证面板查看详情。</p>
          </section>
        </div>
        <div class="border-t border-slate-100 px-4 py-3 text-[11px] leading-5 text-slate-400">参数化定义和工程文件保存在同一模型版本中。</div>
      </aside>

      <main class="relative flex min-h-0 min-w-0 flex-col">
        <div v-if="error" class="mx-3 mt-3 flex shrink-0 items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs leading-5 text-red-700" role="alert"><AlertCircle :size="15" class="mt-0.5 shrink-0" /><span class="flex-1">{{ error }}</span><button aria-label="关闭错误提示" @click="error = ''">×</button></div>
        <div v-if="connectionIssue" class="mx-3 mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800" role="status">{{ connectionIssue }}</div>
        <div v-if="historical" class="mx-3 mt-2 flex items-center justify-between gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800"><span>正在查看历史版本 {{ revision?.id.slice(0, 8) }}</span><button class="font-medium underline" :disabled="busy" @click="restore(revision!.id)">恢复为新版本</button></div>
        <div class="min-h-[360px] flex-1"><CAD3DViewer :revision="revision" /></div>
        <footer class="flex min-h-[46px] flex-wrap items-center justify-between gap-2 border-t border-slate-200 bg-white px-4 py-2 text-[11px] text-slate-500">
          <span v-if="revision">{{ geometrySize }} <span v-if="volume">· {{ volume }} mm³</span></span><span v-else>等待生成真实几何模型</span>
          <span v-if="revision" :class="revision.report?.geometry?.valid === true ? 'text-emerald-700' : 'text-amber-700'">{{ revision.report?.geometry?.valid === true ? 'B-Rep 有效' : '几何状态待确认' }} · {{ revision.ir.units }}</span>
        </footer>
      </main>

      <aside class="flex min-h-0 flex-col border-l border-slate-200 bg-white">
        <div class="grid grid-cols-3 border-b border-slate-200" role="tablist" aria-label="零件详情">
          <button v-for="tab in tabs" :key="tab.id" class="border-b-2 px-2 py-3 text-xs" :class="activeTab === tab.id ? 'border-emerald-700 font-semibold text-emerald-800' : 'border-transparent text-slate-500'" role="tab" :aria-selected="activeTab === tab.id" @click="activeTab = tab.id">{{ tab.name }}</button>
        </div>
        <div class="min-h-0 flex-1 overflow-auto p-4">
          <div v-if="!revision && !(activeTab === 'validation' && job)" class="py-6 text-center text-xs leading-6 text-slate-400">生成完成后可查看参数、<br>几何验证与版本历史。</div>
          <template v-else-if="activeTab === 'parameters' && revision">
            <h2 class="section-label">可编辑参数</h2>
            <form class="mt-4 space-y-3" @submit.prevent="applyParameters">
              <div v-for="[name, parameter] in editableParameters" :key="name">
                <label :for="`param-${name}`" class="flex justify-between gap-2 text-xs text-slate-600"><span class="truncate" :title="name">{{ parameter.label || name }}</span><span class="text-slate-400">{{ parameter.kind === 'length' ? revision?.ir.units : parameter.kind === 'angle' ? '°' : '' }}</span></label>
                <input :id="`param-${name}`" v-model="parameterDraft[name]" type="number" :step="parameter.kind === 'integer' ? 1 : 'any'" :min="parameter.min" :max="parameter.max" :disabled="busy || historical" class="mt-1.5 w-full rounded-md border border-slate-200 px-2 py-2 text-sm outline-none focus:border-emerald-700 disabled:bg-slate-50" />
              </div>
              <p v-if="!editableParameters.length" class="text-xs text-slate-400">此模型没有直接可编辑的数值参数。</p>
              <button v-if="editableParameters.length" class="primary-button w-full justify-center" :disabled="busy || historical || !parametersChanged"><RefreshCw :size="14" />应用并重建</button>
            </form>
            <div v-if="derivedParameters.length" class="mt-5 border-t border-slate-100 pt-4"><h3 class="section-label">关联参数</h3><div v-for="[name, parameter] in derivedParameters" :key="name" class="mt-3 text-xs"><div class="text-slate-600">{{ parameter.label || name }}</div><code class="mt-1 block break-all text-[11px] text-slate-400">{{ parameter.expression || parameter.value }}</code></div></div>
            <div class="mt-5 border-t border-slate-100 pt-4"><h3 class="section-label">建模特征 · {{ revision.ir.features?.length || 0 }}</h3><ol class="mt-3 space-y-2"><li v-for="(feature, index) in revision.ir.features" :key="feature.id || feature.feature_id || index" class="rounded-md border border-slate-100 px-2 py-2 text-xs"><span class="mr-2 text-slate-400">{{ index + 1 }}</span>{{ feature.type }}<span class="mt-1 block truncate pl-4 text-[10px] text-slate-400">{{ feature.id || feature.feature_id }}</span></li></ol></div>
          </template>
          <template v-else-if="activeTab === 'validation'">
            <h2 class="section-label">{{ job?.status === 'failed' ? '最近失败任务的诊断' : '实际几何检查' }}</h2>
            <div v-if="job?.status === 'failed' && job.error" class="mt-3 rounded-lg border border-red-200 bg-red-50 p-3 text-xs leading-5 text-red-700"><p>{{ job.error.message }}</p><p v-if="job.error.feature_id" class="mt-1">特征：{{ job.error.feature_id }}</p><details v-if="job.error.details" class="mt-2"><summary class="cursor-pointer">详细诊断</summary><pre class="mt-2 whitespace-pre-wrap break-all text-[10px]">{{ JSON.stringify(job.error.details, null, 2) }}</pre></details></div>
            <div class="mt-3 rounded-lg bg-slate-50 p-3 text-xs leading-6"><div>实体：{{ activeReport?.geometry?.solids ?? '待确认' }}</div><div>表面积：{{ formatNumber(activeReport?.geometry?.area) }} mm²</div><div>体积：{{ formatNumber(activeReport?.geometry?.volume) }} mm³</div></div>
            <div v-for="(check, index) in activeReport?.validation?.checks || []" :key="`${check.name}-${index}`" class="mt-3 rounded-lg border border-slate-200 p-3"><div class="flex items-start justify-between gap-2"><h3 class="break-all text-xs text-slate-700">{{ check.name || check.id || `检查 ${index + 1}` }}</h3><span class="shrink-0 text-[10px]" :class="statusClass(check.status || '')">{{ statusLabel(check.status || 'unverified') }}</span></div><div class="mt-2 space-y-1 break-all text-[11px] leading-5 text-slate-500"><p v-if="check.expected !== undefined">预期：{{ formatValue(check.expected) }}</p><p v-if="check.actual !== undefined">实际：{{ formatValue(check.actual) }}</p><p v-if="check.message">{{ check.message }}</p><p v-if="check.evidence">{{ formatValue(check.evidence) }}</p></div></div>
            <p v-if="!activeReport?.validation?.checks?.length" class="mt-3 text-xs text-slate-400">尚无逐项验证数据。</p>
            <details v-if="job?.repair_history?.length" class="mt-4 text-xs"><summary class="cursor-pointer font-medium">修复记录</summary><pre class="mt-2 whitespace-pre-wrap break-all text-[10px] leading-5 text-slate-500">{{ JSON.stringify(job.repair_history, null, 2) }}</pre></details>
          </template>
          <template v-else-if="revision">
            <h2 class="section-label">模型历史</h2><p class="mt-2 text-[11px] leading-5 text-slate-400">查看与下载始终对应所选版本。恢复会创建一个新版本。</p>
            <button v-for="(item, index) in sortedRevisions" :key="item.id" class="mt-3 w-full rounded-lg border p-3 text-left disabled:opacity-60" :class="revision.id === item.id ? 'border-emerald-300 bg-emerald-50/50' : 'border-slate-200 hover:bg-slate-50'" :disabled="busy" @click="selectRevision(item.id)"><div class="flex items-center justify-between text-xs"><span class="font-medium">版本 {{ sortedRevisions.length - index }}</span><span v-if="item.id === document?.current_revision" class="text-[10px] text-emerald-700">当前</span></div><p class="mt-1 text-[10px] text-slate-400">{{ formatTime(item.created_at) }}</p><code class="mt-1 block text-[10px] text-slate-500">{{ item.id.slice(0, 12) }}</code></button>
          </template>
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { AlertCircle, ArrowUpRight, Box, Download, LoaderCircle, Plus, RefreshCw, Send } from 'lucide-vue-next';
import CAD3DViewer from '@/components/CAD3DViewer.vue';
import { downloadCADArtifact, type CADArtifactFormat } from '@/api/cadV2';
import { getCachedClientConfig } from '@/api/config';
import { cadErrorMessage, terminalCADJob, useCADV2Workspace } from '@/composables/useCADV2Workspace';

const { documents, document, revision, revisions, job, observedStages, error, connectionIssue, loadingDocument, busy, historical,
  generate, edit, restore, selectRevision, openDocument, newDocument, cancel, reconnect, initialize } = useCADV2Workspace();
const defaultPrompt = '生成一块长 80 mm、宽 50 mm、厚 8 mm 的安装板，在四角开四个直径 6 mm 的通孔，孔中心的水平距离为 60 mm，垂直距离为 30 mm。';
const examples = [
  { name: '四孔安装板 · 80 × 50 × 8', prompt: defaultPrompt },
  { name: '空心轴套 · Ø40 / Ø20 × 30', prompt: '生成一个外径 40 mm、内径 20 mm、长度 30 mm 的空心轴套。' },
  { name: '长方体 · 60 × 40 × 20', prompt: '生成一个长 60 mm、宽 40 mm、高 20 mm 的长方体。' },
];
const prompt = ref(defaultPrompt);
const intent = ref<'create' | 'edit'>('create');
const exportFormat = ref<CADArtifactFormat>('step');
const downloading = ref(false);
const standalone = ref(false);
const activeTab = ref('parameters');
const tabs = [{ id: 'parameters', name: '参数' }, { id: 'validation', name: '验证' }, { id: 'history', name: '历史' }];
const parameterDraft = ref<Record<string, string | number>>({});
const editableParameters = computed(() => Object.entries(revision.value?.ir.parameters || {}).filter(([, value]) => value.editable && value.value !== null && !value.expression));
const derivedParameters = computed(() => Object.entries(revision.value?.ir.parameters || {}).filter(([, value]) => !value.editable || !!value.expression));
const parametersChanged = computed(() => editableParameters.value.some(([name, value]) => String(parameterDraft.value[name]) !== String(value.value)));
const sortedRevisions = computed(() => [...revisions.value].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()));
const progress = computed(() => Math.round(Math.max(0, Math.min(100, Number(job.value?.progress) || 0))));
const activeReport = computed(() => job.value?.status === 'failed' ? job.value.report : revision.value?.report);
const volume = computed(() => formatNumber(revision.value?.report?.geometry?.volume, ''));
const geometrySize = computed(() => {
  const bbox = revision.value?.report?.geometry?.bbox as { size?: number[] } | undefined;
  return Array.isArray(bbox?.size) ? `${bbox.size.map(value => formatNumber(value)).join(' × ')} mm` : '尺寸待确认';
});
watch(() => revision.value?.id, () => {
  parameterDraft.value = Object.fromEntries(editableParameters.value.map(([name, value]) => [name, value.value as number]));
});
watch(() => document.value?.id, (next, previous) => { if (next && next !== previous) { intent.value = 'edit'; prompt.value = ''; } });
function startNew() { newDocument(); intent.value = 'create'; prompt.value = defaultPrompt; }
function chooseDocument(event: Event) { const id = (event.target as HTMLSelectElement).value; if (id) void openDocument(id); }
async function submitPrompt() { if (!prompt.value.trim()) return; if (document.value && intent.value === 'edit') await edit({ prompt: prompt.value.trim() }); else await generate(prompt.value.trim()); }
async function applyParameters() {
  const parameters: Record<string, number> = {};
  for (const [name, original] of editableParameters.value) {
    const raw = parameterDraft.value[name];
    const value = Number(raw);
    if (raw === '' || !Number.isFinite(value) || (original.kind === 'integer' && !Number.isInteger(value))) { error.value = `参数 ${original.label || name} 必须是有效${original.kind === 'integer' ? '整数' : '数值'}。`; return; }
    if (value !== original.value) parameters[name] = value;
  }
  if (Object.keys(parameters).length) await edit({ parameters });
}
async function download() {
  const selected = revision.value, format = exportFormat.value;
  if (!selected) return;
  downloading.value = true;
  try {
    const blob = await downloadCADArtifact(selected.document_id, selected.id, format);
    const url = URL.createObjectURL(blob), link = window.document.createElement('a');
    link.href = url; link.download = selected.artifacts[format]?.filename || `cad-${selected.id.slice(0, 8)}.${format === 'ir' ? 'json' : format === 'python' ? 'py' : format}`;
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (reason) { error.value = cadErrorMessage(reason); } finally { downloading.value = false; }
}
function formatNumber(value: unknown, fallback = '待确认') { return typeof value === 'number' && Number.isFinite(value) ? new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 3 }).format(value) : fallback; }
function formatValue(value: unknown) { return typeof value === 'string' ? value : JSON.stringify(value); }
function formatTime(value: string) { return new Date(value).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }
function statusLabel(value: string) {
  return ({ created: '已创建', queued: '排队中', running: '执行中', validating: '验证中', repairing: '修复中', completed: '已完成', failed: '失败', cancelled: '已取消', passed: '通过', unverified: '未确认', valid: '有效' } as Record<string, string>)[value] || value;
}
function statusClass(value: string) { return ['completed', 'passed', 'valid'].includes(value) ? 'text-emerald-700' : value === 'failed' ? 'text-red-700' : ['unverified', 'cancelled'].includes(value) ? 'text-amber-700' : 'text-slate-500'; }
function stageLabel(value: string) {
  return ({ created: '任务已创建', queued: '等待执行', parsing: '解析工程需求', requirement_parsing: '解析工程需求', planning: '规划建模特征', compiling: '编译参数化定义', executing: '构建真实几何', building: '构建真实几何', validating: '验证几何与尺寸', repairing: '尝试局部修复', exporting: '生成工程文件', publishing: '保存已验证版本', completed: '生成完成', failed: '任务失败', cancelled: '任务已取消' } as Record<string, string>)[value] || value;
}
onMounted(() => { void initialize(); void getCachedClientConfig().then(config => { standalone.value = config?.cad_v2_standalone ?? false; }); });
</script>

<style scoped>
.section-label { font-size: 11px; font-weight: 600; color: #475569; letter-spacing: .04em; }
.primary-button, .secondary-button { display: inline-flex; align-items: center; gap: 6px; border-radius: 7px; padding: 8px 11px; font-size: 12px; line-height: 18px; transition: background .15s; }
.primary-button { background: #176448; color: white; border: 1px solid #176448; }
.primary-button:hover { background: #12543c; }
.secondary-button { background: white; color: #475569; border: 1px solid #e2e8f0; }
.secondary-button:hover { background: #f8fafc; }
button:disabled { cursor: not-allowed; opacity: .45; }
.mode-button { flex: 1; padding: 6px 3px; border-radius: 5px; font-size: 11px; color: #64748b; }
.mode-button.is-active { background: white; color: #166534; box-shadow: 0 1px 3px #0000000a; }
.status-chip { padding: 2px 6px; border-radius: 5px; background: #f1f5f3; font-size: 10px; }
@media (max-width: 1250px) { .v2-body { grid-template-columns: 250px minmax(240px,1fr); } .v2-body > aside:last-child { grid-column: 1 / -1; max-height: 300px; border-top: 1px solid #e2e8f0; } .v2-body { overflow: auto; } }
@media (max-width: 760px) { .v2-body { display: flex; flex-direction: column; overflow-y: auto; } .v2-body > aside:first-child { max-height: none; min-height: 360px; flex-shrink: 0; } .v2-body > main { min-height: 470px; flex-shrink: 0; } .v2-body > aside:last-child { min-height: 300px; flex-shrink: 0; } }
</style>
