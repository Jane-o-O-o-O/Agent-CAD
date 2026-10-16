import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const vue = require('vue');
const source = readFileSync(new URL('../src/composables/useCADV2Workspace.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const baseRevision = { id: 'rev-1', document_id: 'doc-1', created_at: '2026-10-14T00:00:00Z', ir: { parameters: {}, features: [] }, report: {}, artifacts: {} };
const nextRevision = { ...baseRevision, id: 'rev-2', parent_revision: 'rev-1' };
const baseJob = { id: 'job-1', document_id: 'doc-1', status: 'running', stage: 'building', progress: 40, created_at: '2026-10-14T00:01:00Z', updated_at: '2026-10-14T00:01:00Z', repair_history: [] };
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

function harness(storage = new Map(), requestPrefix = 'request') {
  const record = { callbacks: null, unmount: null, cancelCount: 0, createRequests: [], editRequests: [], restores: [], sessionData: storage, location: 'http://localhost/chat/cad-v2' };
  const data = { head: 'rev-1', history: [baseRevision], getRevision: async (_doc, id) => id === 'rev-2' ? nextRevision : baseRevision };
  const api = {
    listCADV2Documents: async () => [{ id: 'doc-1', name: 'Plate', current_revision: data.head }],
    getCADV2Document: async () => ({ id: 'doc-1', name: 'Plate', current_revision: data.head }),
    listCADRevisions: async () => data.history,
    getCADRevision: (...args) => data.getRevision(...args),
    createCADJob: async (...args) => { record.createRequests.push(args); return { ...baseJob }; },
    getCADJob: async () => ({ ...baseJob }),
    cancelCADJob: async () => ({ ...baseJob, status: 'cancelled' }),
    subscribeCADJob: async (_id, callbacks) => { record.callbacks = callbacks; return () => { record.cancelCount++; }; },
    editCADV2Document: async (...args) => { record.editRequests.push(args); return { ...baseJob }; },
    restoreCADRevision: async (...args) => { record.restores.push(args); return { ...baseJob }; },
  };
  const window = { location: { get href() { return record.location; }, get search() { return new URL(record.location).search; } }, history: { state: {}, replaceState(_state, _unused, url) { record.location = String(url); } },
    sessionStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) } };
  const timers = new Map(); let timerId = 0;
  const module = { exports: {} };
  let requestSequence = 0;
  new Function('require', 'module', 'exports', 'window', 'setTimeout', 'clearTimeout', 'crypto', compiled)(
    name => name === 'vue' ? { ...vue, onBeforeUnmount: callback => { record.unmount = callback; } } : api,
    module, module.exports, window, callback => { timers.set(++timerId, callback); return timerId; }, id => timers.delete(id),
    { randomUUID: () => `${requestPrefix}-${++requestSequence}` },
  );
  return { state: module.exports.useCADV2Workspace(), api, data, record, timers };
}

test('failed rebuild keeps the last valid geometry and exposes the real error', async () => {
  const h = harness(); await h.state.openDocument('doc-1');
  await h.state.edit({ parameters: { hole_diameter: 8 } });
  assert.equal(h.state.revision.value.id, 'rev-1'); assert.equal(h.state.busy.value, true);
  h.record.callbacks.onMessage({ event: 'job', data: { ...baseJob, status: 'failed', error: { message: '孔超出边界' }, updated_at: '2026-10-14T00:02:00Z' } });
  await flush();
  assert.equal(h.state.revision.value.id, 'rev-1'); assert.equal(h.state.busy.value, false);
  assert.equal(h.state.error.value, '孔超出边界'); assert.equal(h.record.cancelCount, 1);
  assert.deepEqual(h.record.editRequests[0], ['doc-1', 'rev-1', { parameters: { hole_diameter: 8 } }]);
});

test('completed job loads its exact revision, even when document head advanced elsewhere', async () => {
  const h = harness(); await h.state.openDocument('doc-1'); await h.state.edit({ parameters: { diameter: 8 } });
  h.data.head = 'rev-3'; h.data.history = [baseRevision, nextRevision];
  h.record.callbacks.onMessage({ event: 'job', data: { ...baseJob, status: 'completed', revision_id: 'rev-2', progress: 100, updated_at: '2026-10-14T00:02:00Z' } });
  await flush();
  assert.equal(h.state.revision.value.id, 'rev-2'); assert.equal(h.state.document.value.current_revision, 'rev-3');
  assert.equal(h.state.historical.value, true); assert.equal(h.state.loadingDocument.value, false);
});

test('historical view cannot be edited; restore uses current revision for conflict protection', async () => {
  const h = harness(); h.data.head = 'rev-2'; h.data.history = [baseRevision, nextRevision];
  await h.state.openDocument('doc-1'); await h.state.selectRevision('rev-1');
  await h.state.edit({ parameters: { diameter: 8 } }); assert.equal(h.record.editRequests.length, 0);
  await h.state.restore('rev-1'); assert.deepEqual(h.record.restores[0], ['doc-1', 'rev-1', 'rev-2']);
});

test('stale SSE snapshots cannot rewind progress or resurrect a terminal job', async () => {
  const h = harness(); await h.state.generate('plate');
  const callbacks = h.record.callbacks;
  callbacks.onMessage({ event: 'job', data: { ...baseJob, progress: 75, updated_at: '2026-10-14T00:03:00Z' } });
  callbacks.onMessage({ event: 'job', data: { ...baseJob, progress: 15 } });
  assert.equal(h.state.job.value.progress, 75);
  callbacks.onMessage({ event: 'job', data: { ...baseJob, status: 'cancelled', updated_at: '2026-10-14T00:04:00Z' } });
  callbacks.onMessage({ event: 'job', data: { ...baseJob, updated_at: '2026-10-14T00:05:00Z' } });
  assert.equal(h.state.job.value.status, 'cancelled'); assert.equal(h.timers.size, 0);
});

test('unmount prevents delayed revision response from updating the workspace', async () => {
  const h = harness(); await h.state.openDocument('doc-1');
  let resolveRevision; h.data.getRevision = () => new Promise(resolve => { resolveRevision = resolve; });
  const loading = h.state.selectRevision('rev-2'); await flush();
  h.record.unmount(); resolveRevision(nextRevision); await loading;
  assert.equal(h.state.revision.value.id, 'rev-1');
});

test('SSE failure retains the job and polling resolves a later completion', async () => {
  const h = harness(); await h.state.generate('plate');
  h.record.callbacks.onError(new Error('offline'));
  assert.match(h.state.connectionIssue.value, /定期查询/);
  h.api.getCADJob = async () => ({ ...baseJob, status: 'completed', revision_id: 'rev-2', updated_at: '2026-10-14T00:03:00Z' });
  const poll = [...h.timers.values()][0]; await poll(); await flush();
  assert.equal(h.state.revision.value.id, 'rev-2'); assert.equal(h.state.job.value.status, 'completed');
});

test('failure opening SSE still starts bounded polling', async () => {
  const h = harness(); h.api.subscribeCADJob = async () => { throw new Error('connection unavailable'); };
  await h.state.generate('plate');
  assert.equal(h.state.busy.value, true); assert.equal(h.timers.size, 1);
  assert.match(h.state.connectionIssue.value, /定期查询/);
});

test('workbench deep link restores the exact requested historical revision', async () => {
  const h = harness(); h.data.head = 'rev-2'; h.data.history = [baseRevision, nextRevision];
  h.record.location = 'http://localhost/chat/cad-v2?document=doc-1&revision=rev-1';
  await h.state.initialize();
  assert.equal(h.state.revision.value.id, 'rev-1'); assert.equal(h.state.historical.value, true);
  assert.equal(new URL(h.record.location).searchParams.get('revision'), 'rev-1');
});

const waitingJob = {
  ...baseJob, status: 'awaiting_input', stage: 'awaiting_input', base_revision: null,
  error: { code: 'RequirementError', message: '安装板厚度是多少？' }, updated_at: '2026-10-14T00:02:00Z',
};

test('incomplete creation stops monitoring, accepts multiple answers, then loads the generated revision', async () => {
  const h = harness(); h.data.head = null; h.data.history = [];
  await h.state.generate('安装板长 80 mm、宽 50 mm');
  h.record.callbacks.onMessage({ event: 'job', data: waitingJob }); await flush();
  assert.equal(h.state.busy.value, false); assert.equal(h.state.error.value, ''); assert.equal(h.timers.size, 0);
  assert.equal(h.state.clarificationJob.value.id, 'job-1'); assert.equal(h.state.document.value.current_revision, null);
  h.api.createCADJob = async (...args) => {
    h.record.createRequests.push(args);
    return { ...waitingJob, id: 'job-2', error: { code: 'RequirementError', message: '孔径是多少？' } };
  };
  await h.state.answerClarification('厚度 8 mm');
  assert.deepEqual(h.record.createRequests[1], ['厚度 8 mm', 'job-1', 'request-1']);
  assert.equal(h.state.clarificationJob.value.id, 'job-2'); assert.equal(h.timers.size, 0);
  h.api.createCADJob = async (...args) => { h.record.createRequests.push(args); return { ...baseJob, id: 'job-3' }; };
  await h.state.answerClarification('孔径 6 mm');
  assert.deepEqual(h.record.createRequests[2], ['孔径 6 mm', 'job-2', 'request-2']);
  h.data.head = 'rev-2'; h.data.history = [nextRevision];
  h.record.callbacks.onMessage({ event: 'job', data: { ...baseJob, id: 'job-3', status: 'completed', revision_id: 'rev-2', updated_at: '2026-10-14T00:03:00Z' } });
  await flush();
  assert.equal(h.state.revision.value.id, 'rev-2'); assert.equal(h.state.clarificationJob.value, null);
});

test('refresh restores a pending creation question and its continuation id', async () => {
  const h = harness(); h.data.head = null; h.data.history = [];
  h.record.location = 'http://localhost/chat/cad-v2?document=doc-1&job=job-1';
  h.api.getCADJob = async () => waitingJob;
  await h.state.initialize();
  assert.equal(h.state.clarificationJob.value.id, 'job-1'); assert.equal(h.state.busy.value, false);
  assert.equal(new URL(h.record.location).searchParams.get('job'), 'job-1');
  assert.equal(h.record.callbacks, null); assert.equal(h.timers.size, 0);
  await h.state.answerClarification('厚度 8 mm');
  assert.deepEqual(h.record.createRequests, [['厚度 8 mm', 'job-1', 'request-1']]);
});

test('edit clarification preserves the base revision and supports legacy requirement failures', async () => {
  const h = harness(); await h.state.openDocument('doc-1'); await h.state.edit({ prompt: '孔加大一些' });
  h.record.callbacks.onMessage({ event: 'job', data: { ...waitingJob, status: 'failed', base_revision: 'rev-1' } }); await flush();
  assert.equal(h.state.error.value, ''); assert.equal(h.state.clarificationJob.value.id, 'job-1');
  await h.state.answerClarification('改为 8 mm');
  assert.deepEqual(h.record.editRequests[1], ['doc-1', 'rev-1', { prompt: '改为 8 mm', clarification_job_id: 'job-1', request_id: 'request-1' }]);
});

test('new requirements and workspace switches cannot submit an old answer', async () => {
  const h = harness(); h.data.head = null; h.data.history = [];
  h.api.createCADJob = async (...args) => { h.record.createRequests.push(args); return waitingJob; };
  await h.state.generate('安装板'); await flush();
  h.state.dismissClarification(); await h.state.answerClarification('厚度 8 mm');
  assert.equal(h.record.createRequests.length, 1); assert.equal(h.state.clarificationJob.value, null);
  await h.state.generate('新需求'); await flush();
  h.state.newDocument(); await h.state.answerClarification('厚度 8 mm');
  assert.equal(h.record.createRequests.length, 2); assert.equal(h.state.job.value, null);
  assert.equal(new URL(h.record.location).searchParams.has('job'), false);
});

test('complete first requirement proceeds without a clarification step', async () => {
  const h = harness(); await h.state.generate('安装板长 80 mm、宽 50 mm、厚 8 mm');
  assert.equal(h.state.clarificationJob.value, null); assert.equal(h.state.busy.value, true);
  assert.deepEqual(h.record.createRequests, [['安装板长 80 mm、宽 50 mm、厚 8 mm']]);
});

test('stale edit questions and non-requirement failures cannot be resumed', async () => {
  const h = harness(); await h.state.openDocument('doc-1'); await h.state.edit({ prompt: '孔加大一些' });
  h.record.callbacks.onMessage({ event: 'job', data: { ...waitingJob, base_revision: 'old-revision' } }); await flush();
  await h.state.answerClarification('改为 8 mm');
  assert.equal(h.state.clarificationJob.value, null); assert.equal(h.record.editRequests.length, 1);
  h.api.createCADJob = async () => ({ ...baseJob, status: 'failed', error: { code: 'PlanningError', message: '模型未配置' } });
  await h.state.generate('轴套');
  assert.equal(h.state.clarificationJob.value, null); assert.equal(h.state.error.value, '模型未配置');
});

for (const kind of ['create', 'edit']) {
  test(`${kind} clarification retries reuse the request id after a lost response`, async () => {
    const h = harness();
    if (kind === 'create') {
      h.data.head = null; h.data.history = [];
      h.api.createCADJob = async () => waitingJob;
      await h.state.generate('安装板'); await flush();
    } else {
      await h.state.openDocument('doc-1'); await h.state.edit({ prompt: '孔加大一些' });
      h.record.callbacks.onMessage({ event: 'job', data: { ...waitingJob, base_revision: 'rev-1' } }); await flush();
    }
    const requests = [];
    const submit = async (...args) => { requests.push(args); throw new Error('network timeout'); };
    if (kind === 'create') h.api.createCADJob = submit;
    else h.api.editCADV2Document = submit;
    await h.state.answerClarification('  8 mm  ');
    assert.equal(h.state.error.value, 'network timeout'); assert.equal(h.state.clarificationJob.value.id, 'job-1');
    await h.state.answerClarification('8 mm');
    const key = args => kind === 'create' ? args[2] : args[2].request_id;
    assert.equal(key(requests[0]), key(requests[1]));
    await h.state.answerClarification('10 mm');
    assert.notEqual(key(requests[1]), key(requests[2]));
  });
}

test('workspace remount reuses the persisted request id after a lost answer response', async () => {
  const first = harness(); first.data.head = null; first.data.history = [];
  first.api.createCADJob = async () => waitingJob;
  await first.state.generate('安装板'); await flush();
  const submitted = [];
  first.api.createCADJob = async (...args) => { submitted.push(args); throw new Error('network timeout'); };
  await first.state.answerClarification('厚度 8 mm');
  const refreshed = harness(first.record.sessionData, 'remounted');
  refreshed.data.head = null; refreshed.data.history = [];
  refreshed.record.location = first.record.location;
  refreshed.api.getCADJob = async () => waitingJob;
  await refreshed.state.initialize();
  await refreshed.state.answerClarification('  厚度 8 mm  ');
  assert.equal(refreshed.record.createRequests[0][2], submitted[0][2]);
  assert.equal(first.record.sessionData.size, 1);
});

test('disabled session storage does not prevent an answer or its in-memory retry', async () => {
  const storage = { get() { throw new Error('storage disabled'); }, set() { throw new Error('storage disabled'); } };
  const h = harness(storage); h.data.head = null; h.data.history = [];
  h.api.createCADJob = async () => waitingJob;
  await h.state.generate('安装板'); await flush();
  const submitted = [];
  h.api.createCADJob = async (...args) => { submitted.push(args); throw new Error('network timeout'); };
  await h.state.answerClarification('厚度 8 mm'); await h.state.answerClarification('厚度 8 mm');
  assert.equal(submitted.length, 2); assert.equal(submitted[0][2], submitted[1][2]);
});
