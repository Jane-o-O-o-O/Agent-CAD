import { computed, onBeforeUnmount, ref } from 'vue';
import {
  cancelCADJob, createCADJob, editCADV2Document, getCADJob, getCADRevision,
  getCADV2Document, listCADRevisions, listCADV2Documents, restoreCADRevision, subscribeCADJob,
  type CADJob, type CADRevision, type CADV2Document,
} from '@/api/cadV2';

export const terminalCADJob = (job: CADJob | null) => !!job && ['completed', 'failed', 'cancelled'].includes(job.status);
export function cadErrorMessage(reason: unknown): string {
  const error = reason as { message?: string; details?: { detail?: { message?: string } | string; msg?: string } };
  const detail = error?.details?.detail;
  return (typeof detail === 'string' ? detail : detail?.message) || error?.details?.msg || error?.message || '请求失败，请稍后重试。';
}

export function useCADV2Workspace() {
  const documents = ref<CADV2Document[]>([]);
  const document = ref<CADV2Document | null>(null);
  const revision = ref<CADRevision | null>(null);
  const revisions = ref<CADRevision[]>([]);
  const job = ref<CADJob | null>(null);
  const observedStages = ref<Array<{ stage: string; status: string; at: string }>>([]);
  const error = ref('');
  const connectionIssue = ref('');
  const submitting = ref(false);
  const loadingDocument = ref(false);
  const busy = computed(() => submitting.value || loadingDocument.value || (!!job.value && !terminalCADJob(job.value)));
  const historical = computed(() => !!revision.value && !!document.value && revision.value.id !== document.value.current_revision);
  let workspaceEpoch = 0;
  let monitorEpoch = 0;
  let stopStream: (() => void) | null = null;
  let pollTimer: ReturnType<typeof setTimeout> | null = null;
  let disposed = false;

  function stopMonitoring() {
    monitorEpoch += 1;
    stopStream?.(); stopStream = null;
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = null;
  }
  function updateLocation(documentId?: string | null, jobId?: string | null, revisionId?: string | null) {
    const url = new URL(window.location.href);
    documentId ? url.searchParams.set('document', documentId) : url.searchParams.delete('document');
    jobId ? url.searchParams.set('job', jobId) : url.searchParams.delete('job');
    revisionId ? url.searchParams.set('revision', revisionId) : url.searchParams.delete('revision');
    window.history.replaceState(window.history.state, '', url);
  }
  async function refreshDocuments() {
    const result = await listCADV2Documents();
    if (!disposed) documents.value = result;
  }
  async function loadDocument(documentId: string, revisionId?: string, epoch = workspaceEpoch) {
    loadingDocument.value = true;
    try {
      const [loaded, history] = await Promise.all([getCADV2Document(documentId), listCADRevisions(documentId)]);
      const targetRevision = revisionId || loaded.current_revision;
      const selected = targetRevision ? await getCADRevision(documentId, targetRevision) : null;
      if (epoch !== workspaceEpoch || disposed) return;
      document.value = loaded; revisions.value = history; revision.value = selected;
      updateLocation(documentId, job.value && !terminalCADJob(job.value) ? job.value.id : undefined, selected?.id);
    } finally { if (epoch === workspaceEpoch) loadingDocument.value = false; }
  }
  async function finishJob(snapshot: CADJob, epoch: number) {
    stopMonitoring();
    connectionIssue.value = '';
    if (snapshot.status === 'completed' && snapshot.document_id && snapshot.revision_id) {
      try { await loadDocument(snapshot.document_id, snapshot.revision_id, epoch); await refreshDocuments(); }
      catch (reason) { if (epoch === workspaceEpoch) error.value = `建模已完成，但读取结果失败：${cadErrorMessage(reason)}`; }
    } else if (snapshot.status === 'failed') {
      error.value = snapshot.error?.message || snapshot.report?.error?.message || '建模失败，最后一个有效版本已保留。';
    }
  }
  async function followJob(initial: CADJob) {
    stopMonitoring();
    const token = monitorEpoch;
    const epoch = workspaceEpoch;
    const deadline = Date.now() + 10 * 60 * 1000;
    let lastStreamAt = 0;
    let failures = 0;
    job.value = initial;
    observedStages.value = [];
    updateLocation(document.value?.id || initial.document_id, initial.id, revision.value?.id);
    const current = () => token === monitorEpoch && epoch === workspaceEpoch && !disposed;
    const accept = (snapshot: CADJob) => {
      if (!current() || snapshot.id !== initial.id) return;
      if (job.value && new Date(snapshot.updated_at).getTime() < new Date(job.value.updated_at).getTime()) return;
      if (terminalCADJob(job.value) && !terminalCADJob(snapshot)) return;
      job.value = snapshot;
      const previous = observedStages.value[observedStages.value.length - 1];
      if (!previous || previous.stage !== snapshot.stage || previous.status !== snapshot.status) {
        observedStages.value.push({ stage: snapshot.stage, status: snapshot.status, at: snapshot.updated_at });
        if (observedStages.value.length > 50) observedStages.value.shift();
      }
      if (terminalCADJob(snapshot)) void finishJob(snapshot, epoch);
    };
    accept(initial);
    if (!current()) return;
    try {
      const cancel = await subscribeCADJob(initial.id, {
        onMessage: event => { if (current() && event.event === 'job') { lastStreamAt = Date.now(); connectionIssue.value = ''; accept(event.data); } },
        onError: () => { if (current()) connectionIssue.value = '实时连接暂时中断，正在定期查询任务状态。'; },
        onClose: () => { if (current() && !terminalCADJob(job.value)) connectionIssue.value = '实时连接已断开，正在定期查询任务状态。'; },
      });
      if (!current()) { cancel(); return; }
      stopStream = cancel;
    } catch {
      if (!current()) return;
      connectionIssue.value = '无法建立实时连接，正在定期查询任务状态。';
    }
    const poll = async () => {
      if (!current()) return;
      if (Date.now() > deadline) { stopMonitoring(); connectionIssue.value = '已停止长时间状态查询。任务可能仍在运行，请点击“重新连接”查看。'; return; }
      try { const snapshot = await getCADJob(initial.id); if (!current()) return; failures = 0; accept(snapshot); }
      catch (reason) { if (!current()) return; failures += 1; connectionIssue.value = `状态查询失败（${failures} 次）：${cadErrorMessage(reason)}`; }
      if (current()) pollTimer = setTimeout(poll, lastStreamAt > Date.now() - 10000 ? 10000 : Math.min(3000 + failures * 1000, 15000));
    };
    pollTimer = setTimeout(poll, 2000);
  }
  async function run(request: () => Promise<CADJob>) {
    if (busy.value) return;
    const epoch = workspaceEpoch;
    error.value = ''; connectionIssue.value = ''; submitting.value = true;
    try { const created = await request(); if (epoch === workspaceEpoch && !disposed) await followJob(created); }
    catch (reason) { if (epoch === workspaceEpoch) error.value = cadErrorMessage(reason); }
    finally { if (epoch === workspaceEpoch) submitting.value = false; }
  }
  async function generate(prompt: string) { await run(() => createCADJob(prompt)); }
  async function edit(edit: { prompt?: string; parameters?: Record<string, number> }) {
    if (!document.value?.current_revision || historical.value) return;
    const id = document.value.id, expected = document.value.current_revision;
    await run(() => editCADV2Document(id, expected, edit));
  }
  async function restore(revisionId: string) {
    if (!document.value?.current_revision) return;
    const id = document.value.id, expected = document.value.current_revision;
    await run(() => restoreCADRevision(id, revisionId, expected));
  }
  async function selectRevision(revisionId: string) {
    if (busy.value || !document.value) return;
    const epoch = ++workspaceEpoch; error.value = '';
    try { await loadDocument(document.value.id, revisionId, epoch); }
    catch (reason) { if (epoch === workspaceEpoch) error.value = cadErrorMessage(reason); }
  }
  async function openDocument(id: string, revisionId?: string) {
    if (busy.value) return;
    stopMonitoring(); const epoch = ++workspaceEpoch;
    error.value = ''; connectionIssue.value = ''; job.value = null; observedStages.value = [];
    try { await loadDocument(id, revisionId, epoch); }
    catch (reason) { if (epoch === workspaceEpoch) error.value = cadErrorMessage(reason); }
  }
  function newDocument() {
    if (busy.value) return;
    stopMonitoring(); workspaceEpoch += 1;
    document.value = null; revision.value = null; revisions.value = []; job.value = null;
    observedStages.value = []; error.value = ''; connectionIssue.value = ''; updateLocation();
  }
  async function cancel() {
    if (!job.value || terminalCADJob(job.value)) return;
    try { const snapshot = await cancelCADJob(job.value.id); if (snapshot.id === job.value?.id) { job.value = snapshot; if (terminalCADJob(snapshot)) await finishJob(snapshot, workspaceEpoch); } }
    catch (reason) { error.value = cadErrorMessage(reason); }
  }
  async function reconnect() {
    if (!job.value) return;
    try { await followJob(await getCADJob(job.value.id)); }
    catch (reason) { error.value = cadErrorMessage(reason); }
  }
  async function initialize() {
    try { await refreshDocuments(); } catch (reason) { error.value = cadErrorMessage(reason); }
    const params = new URLSearchParams(window.location.search);
    const documentId = params.get('document'), jobId = params.get('job'), revisionId = params.get('revision');
    if (documentId) await openDocument(documentId, revisionId || undefined);
    if (jobId && !disposed) {
      try { await followJob(await getCADJob(jobId)); }
      catch (reason) { error.value = cadErrorMessage(reason); }
    }
  }
  onBeforeUnmount(() => { disposed = true; workspaceEpoch += 1; stopMonitoring(); });
  return { documents, document, revision, revisions, job, observedStages, error, connectionIssue, submitting, loadingDocument, busy, historical,
    generate, edit, restore, selectRevision, openDocument, newDocument, cancel, reconnect, initialize };
}
