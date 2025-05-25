// Backend API service
import { apiClient, BASE_URL, ApiResponse } from './client';
import { fetchEventSource, EventSourceMessage } from '@microsoft/fetch-event-source';
import { SSEEvent } from '../types/sseEvent';


// Agent related interfaces
export interface Session {
  session_id: string;
  status: string;
  message: string;
}

/**
 * Create Session
 * @returns Session
 */
export async function createSession(): Promise<Session> {
  const response = await apiClient.put<ApiResponse<Session>>('/sessions');
  // Error handling
  if (response.data.code !== 0) {
    throw new Error(response.data.msg);
  }
  return response.data.data;
}

export const getVNCUrl = (sessionId: string): string => {
  // Convert http to ws, https to wss
  const wsBaseUrl = BASE_URL.replace(/^http/, 'ws');
  return `${wsBaseUrl}/sessions/${sessionId}/vnc`;
}

/**
 * Chat with Session (using SSE to receive streaming responses)
 */
export const chatWithSession = async (
  sessionId: string, 
  message: string = '', 
  onMessage: (event: SSEEvent) => void,
  onError?: (error: Error) => void
) => {
  try {
    const apiUrl = `${BASE_URL}/sessions/${sessionId}/chat`;
    
    await fetchEventSource(apiUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      openWhenHidden: true,
      body: JSON.stringify({ message, timestamp: Math.floor(Date.now() / 1000) }),
      onmessage(event: EventSourceMessage) {
        if (event.event && event.event.trim() !== '') {
          onMessage({
            event: event.event as SSEEvent['event'],
            data: JSON.parse(event.data) as SSEEvent['data']
          });
        }
      },
      onerror(err: Error | string | unknown) {
        console.error('EventSource error:', err);
        if (onError) {
          onError(err instanceof Error ? err : new Error(String(err)));
        }
        throw err;
      },
    });
  } catch (error) {
    console.error('Chat error:', error);
    if (onError) {
      onError(error instanceof Error ? error : new Error(String(error)));
    }
    throw error;
  }
};

export interface ConsoleRecord {
  ps1: string;
  command: string;
  output: string;
}

export interface ShellViewResponse {
  output: string;
  session_id: string;
  console: ConsoleRecord[];
}

/**
 * View Shell session output
 * @param sessionId Session ID
 * @param shellSessionId Shell session ID
 * @returns Shell session output content
 */
export async function viewShellSession(sessionId: string, shellSessionId: string): Promise<ShellViewResponse> {
  const response = await apiClient.post<ApiResponse<ShellViewResponse>>(`/sessions/${sessionId}/shell`, { session_id: shellSessionId });
  // Error handling
  if (response.data.code !== 0) {
    throw new Error(response.data.msg);
  }
  return response.data.data;
}

export interface FileViewResponse {
  content: string;
  file: string;
}

/**
 * View file content
 * @param sessionId Session ID
 * @param file File path
 * @returns File content
 */
export async function viewFile(sessionId: string, file: string): Promise<FileViewResponse> {
  const response = await apiClient.post<ApiResponse<FileViewResponse>>(`/sessions/${sessionId}/file`, { file });
  // Error handling
  if (response.data.code !== 0) {
    throw new Error(response.data.msg);
  }
  return response.data.data;
}