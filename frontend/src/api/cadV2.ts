import { apiClient, createSSEConnection, type ApiResponse, type SSECallbacks } from './client';

export type CADJobStatus = 'created' | 'queued' | 'running' | 'validating' | 'repairing' | 'completed' | 'failed' | 'cancelled';
export type CADArtifactFormat = 'step' | 'stl' | 'glb' | 'ir' | 'python' | 'dxf' | 'svg' | 'bom_json' | 'bom_csv';
export interface CADInstance { id: string; part_id: string; name?: string | null; editable: boolean; transform: { translation: Array<number | string>; rotation: Array<number | string> } }
export interface CADEditRequest { prompt?: string; parameters?: Record<string, number>; clarification_job_id?: string; instance_patches?: Record<string, { transform?: { translation?: number[]; rotation?: number[] } }> }
export interface CADV2Error { code?: string; type?: string; message: string; feature_id?: string; details?: unknown }
export interface CADParameter {
  kind: 'length' | 'angle' | 'integer' | 'scalar';
  value: number | null;
  expression: string | null;
  editable: boolean;
  label?: string;
  min?: number;
  max?: number;
}
export interface CADIR {
  schema_version: string;
  document_id: string;
  units: string;
  parameters: Record<string, CADParameter>;
  features: Array<{ id?: string; feature_id?: string; type?: string; [key: string]: unknown }>;
  assembly?: { name?: string; instances: CADInstance[]; parts: Array<{ id: string; name?: string; material?: string; color?: number[] }> } | null;
  [key: string]: unknown;
}
export interface CADValidationCheck {
  name?: string;
  id?: string;
  status?: string;
  expected?: unknown;
  actual?: unknown;
  message?: string;
  [key: string]: unknown;
}
export interface CADReport {
  status?: string;
  geometry?: Record<string, unknown>;
  validation?: { status?: string; checks?: CADValidationCheck[] };
  trace?: unknown[];
  error?: CADV2Error;
  [key: string]: unknown;
}
export interface CADJob {
  id: string;
  document_id: string | null;
  status: CADJobStatus;
  stage: string;
  progress: number;
  base_revision?: string | null;
  revision_id?: string | null;
  created_at: string;
  updated_at: string;
  error?: CADV2Error | null;
  report?: CADReport | null;
  repair_history: unknown[];
}
export interface CADV2Document {
  id: string;
  name: string;
  current_revision: string | null;
  created_at: string;
  updated_at: string;
}
export interface CADRevision {
  id: string;
  document_id: string;
  parent_revision?: string | null;
  created_at: string;
  ir: CADIR;
  report: CADReport;
  artifacts: Record<string, { filename: string; sha256: string; size: number }>;
}

const root = '/cad-v2';
const segment = (value: string) => encodeURIComponent(value);
const unwrap = <T>(response: { data: ApiResponse<T> }) => response.data.data;

export async function createCADJob(prompt: string): Promise<CADJob> {
  return unwrap(await apiClient.post<ApiResponse<CADJob>>(`${root}/jobs`, { prompt, request_id: crypto.randomUUID() }));
}
export async function getCADJob(id: string): Promise<CADJob> {
  return unwrap(await apiClient.get<ApiResponse<CADJob>>(`${root}/jobs/${segment(id)}`));
}
export async function cancelCADJob(id: string): Promise<CADJob> {
  return unwrap(await apiClient.post<ApiResponse<CADJob>>(`${root}/jobs/${segment(id)}/cancel`));
}
export async function subscribeCADJob(id: string, callbacks: SSECallbacks<CADJob>): Promise<() => void> {
  return createSSEConnection<CADJob>(`${root}/jobs/${segment(id)}/events`, { retry: false }, callbacks);
}
export async function listCADV2Documents(): Promise<CADV2Document[]> {
  return unwrap(await apiClient.get<ApiResponse<CADV2Document[]>>(`${root}/documents`));
}
export async function getCADV2Document(id: string): Promise<CADV2Document> {
  return unwrap(await apiClient.get<ApiResponse<CADV2Document>>(`${root}/documents/${segment(id)}`));
}
export async function listCADRevisions(id: string): Promise<CADRevision[]> {
  return unwrap(await apiClient.get<ApiResponse<CADRevision[]>>(`${root}/documents/${segment(id)}/revisions`));
}
export async function getCADRevision(documentId: string, revisionId: string): Promise<CADRevision> {
  return unwrap(await apiClient.get<ApiResponse<CADRevision>>(`${root}/documents/${segment(documentId)}/revisions/${segment(revisionId)}`));
}
export async function editCADV2Document(documentId: string, expectedRevision: string, edit: CADEditRequest): Promise<CADJob> {
  return unwrap(await apiClient.post<ApiResponse<CADJob>>(`${root}/documents/${segment(documentId)}/edits`, {
    ...edit, expected_revision: expectedRevision, request_id: crypto.randomUUID(),
  }));
}
export async function restoreCADRevision(documentId: string, revisionId: string, expectedRevision: string): Promise<CADJob> {
  return unwrap(await apiClient.post<ApiResponse<CADJob>>(`${root}/documents/${segment(documentId)}/restore`, {
    revision_id: revisionId, expected_revision: expectedRevision, request_id: crypto.randomUUID(),
  }));
}
export async function downloadCADArtifact(documentId: string, revisionId: string, format: CADArtifactFormat, signal?: AbortSignal): Promise<Blob> {
  const response = await apiClient.get(`${root}/documents/${segment(documentId)}/revisions/${segment(revisionId)}/artifacts/${format}`, {
    responseType: 'blob', signal,
  });
  return response.data;
}
