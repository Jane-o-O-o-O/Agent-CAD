import { apiClient, ApiResponse } from './client';
import type { FileInfo } from './file';

export type CADUnit = 'mm' | 'inch';

export interface CADPoint {
  x: number;
  y: number;
}

export interface CADLayer {
  name: string;
  color?: number;
  line_type?: string;
  visible: boolean;
}

export interface CADLine {
  id: string;
  type: 'line';
  layer: string;
  start: CADPoint;
  end: CADPoint;
}

export interface CADCircle {
  id: string;
  type: 'circle';
  layer: string;
  center: CADPoint;
  radius: number;
}

export interface CADArc {
  id: string;
  type: 'arc';
  layer: string;
  center: CADPoint;
  radius: number;
  start_angle: number;
  end_angle: number;
}

export interface CADPolyline {
  id: string;
  type: 'polyline';
  layer: string;
  points: CADPoint[];
  closed: boolean;
}

export interface CADSlot {
  id: string;
  type: 'slot';
  layer: string;
  center: CADPoint;
  width: number;
  length: number;
  rotation: number;
}

export interface CADNote {
  id: string;
  type: 'note';
  layer: string;
  position: CADPoint;
  text: string;
  height: number;
}

export type CADEntity = CADLine | CADCircle | CADArc | CADPolyline | CADSlot | CADNote;

export interface CADDimension {
  id: string;
  type: 'linear' | 'diameter' | 'radius' | 'note';
  layer: string;
  start?: CADPoint;
  end?: CADPoint;
  position: CADPoint;
  text?: string;
}

export interface MechanicalDesignBrief {
  part_type?: string;
  units: CADUnit;
  features: Record<string, any>[];
  constraints: string[];
  unknowns: string[];
  manufacturing_notes: string[];
  source_references: Record<string, any>[];
}

export interface MechanicalCADDocument {
  id: string;
  session_id?: string;
  user_id?: string;
  title: string;
  units: CADUnit;
  layers: CADLayer[];
  entities: CADEntity[];
  dimensions: CADDimension[];
  constraints: Record<string, any>[];
  brief?: MechanicalDesignBrief;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface CADOperation {
  operation: string;
  params: Record<string, any>;
}

export interface CADDocumentResponse {
  document: MechanicalCADDocument;
}

export interface ApplyCADOperationResponse {
  document: MechanicalCADDocument;
  added_entity_ids: string[];
  message: string;
}

export interface CADPlanStep {
  id: string;
  title: string;
  description: string;
  operation: CADOperation;
}

export interface CADPlanFromPromptResponse {
  title: string;
  message: string;
  brief: MechanicalDesignBrief;
  steps: CADPlanStep[];
}

export async function createCADDocumentFromPrompt(prompt: string, attachments: FileInfo[] = []): Promise<ApplyCADOperationResponse> {
  const response = await apiClient.post<ApiResponse<ApplyCADOperationResponse>>('/cad/documents/from-prompt', {
    prompt,
    units: 'mm',
    attachments: attachments.map(file => ({
      file_id: file.file_id,
      filename: file.filename,
      content_type: file.content_type,
      size: file.size,
      upload_date: file.upload_date,
    })),
  });
  return response.data.data;
}

export async function createCADDocument(title = 'Mechanical drawing'): Promise<CADDocumentResponse> {
  const response = await apiClient.post<ApiResponse<CADDocumentResponse>>('/cad/documents', {
    title,
    units: 'mm',
  });
  return response.data.data;
}

export async function createCADPlanFromPrompt(prompt: string, attachments: FileInfo[] = []): Promise<CADPlanFromPromptResponse> {
  const response = await apiClient.post<ApiResponse<CADPlanFromPromptResponse>>('/cad/plans/from-prompt', {
    prompt,
    units: 'mm',
    attachments: attachments.map(file => ({
      file_id: file.file_id,
      filename: file.filename,
      content_type: file.content_type,
      size: file.size,
      upload_date: file.upload_date,
    })),
  });
  return response.data.data;
}

export async function applyCADOperation(documentId: string, operation: CADOperation): Promise<ApplyCADOperationResponse> {
  const response = await apiClient.post<ApiResponse<ApplyCADOperationResponse>>(`/cad/documents/${documentId}/operations`, {
    operation,
  });
  return response.data.data;
}

export async function applyCADOperations(documentId: string, operations: CADOperation[]): Promise<ApplyCADOperationResponse> {
  const response = await apiClient.post<ApiResponse<ApplyCADOperationResponse>>(`/cad/documents/${documentId}/operations/batch`, {
    operations,
  });
  return response.data.data;
}

export async function downloadCADDocumentDxf(documentId: string): Promise<Blob> {
  const response = await apiClient.get(`/cad/documents/${documentId}/export/dxf`, {
    responseType: 'blob',
  });
  return response.data;
}
