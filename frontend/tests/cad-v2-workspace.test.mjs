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

function harness() {
  const record = { callbacks: null, unmount: null, cancelCount: 0, editRequests: [], restores: [], location: 'http://localhost/chat/cad-v2' };
  const data = { head: 'rev-1', history: [baseRevision], getRevision: async (_doc, id) => id === 'rev-2' ? nextRevision : baseRevision };
  const api = {
    listCADV2Documents: async () => [{ id: 'doc-1', name: 'Plate', current_revision: data.head }],
    getCADV2Document: async () => ({ id: 'doc-1', name: 'Plate', current_revision: data.head }),
    listCADRevisions: async () => data.history,
    getCADRevision: (...args) => data.getRevision(...args),
    createCADJob: async () => ({ ...baseJob }),
    getCADJob: async () => ({ ...baseJob }),
    cancelCADJob: async () => ({ ...baseJob, status: 'cancelled' }),
    subscribeCADJob: async (_id, callbacks) => { record.callbacks = callbacks; return () => { record.cancelCount++; }; },
    editCADV2Document: async (...args) => { record.editRequests.push(args); return { ...baseJob }; },
    restoreCADRevision: async (...args) => { record.restores.push(args); return { ...baseJob }; },
  };
  const window = { location: { get href() { return record.location; }, get search() { return new URL(record.location).search; } }, history: { state: {}, replaceState(_state, _unused, url) { record.location = String(url); } } };
  const timers = new Map(); let timerId = 0;
  const module = { exports: {} };
  new Function('require', 'module', 'exports', 'window', 'setTimeout', 'clearTimeout', compiled)(
    name => name === 'vue' ? { ...vue, onBeforeUnmount: callback => { record.unmount = callback; } } : api,
    module, module.exports, window, callback => { timers.set(++timerId, callback); return timerId; }, id => timers.delete(id),
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
