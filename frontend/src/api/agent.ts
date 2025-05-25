// Backend API service
import { apiClient, BASE_URL, ApiResponse } from './client';
import { fetchEventSource, EventSourceMessage } from '@microsoft/fetch-event-source';
import { AgentSSEEvent } from '../types/event';
import { CreateSessionResponse, GetSessionResponse, ShellViewResponse, FileViewResponse } from '../types/response';

/**
 * Create Session
 * @returns Session
 */
export async function createSession(): Promise<CreateSessionResponse> {
  const response = await apiClient.put<ApiResponse<CreateSessionResponse>>('/sessions');
  return response.data.data;
}

export async function getSession(sessionId: string): Promise<GetSessionResponse> {
  const response = await apiClient.get<ApiResponse<GetSessionResponse>>(`/sessions/${sessionId}`);
  return response.data.data;
}

export const getVNCUrl = (sessionId: string): string => {
  // Convert http to ws, https to wss
  const wsBaseUrl = BASE_URL.replace(/^http/, 'ws');
  return `${wsBaseUrl}/sessions/${sessionId}/vnc`;
}

interface ChatCallbacks {
  onOpen: () => void;
  onMessage: (event: AgentSSEEvent) => void;
  onClose: () => void;
  onError?: (error: Error) => void;
}

/**
 * Chat with Session (using SSE to receive streaming responses)
 */
export const chatWithSession = async (
  sessionId: string, 
  message: string = '',
  callbacks: ChatCallbacks
) => {
  const { onOpen, onMessage, onClose, onError } = callbacks;
  
  try {
    const apiUrl = `${BASE_URL}/sessions/${sessionId}/chat`;
    
    await fetchEventSource(apiUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      openWhenHidden: true,
      body: JSON.stringify({ message, timestamp: Math.floor(Date.now() / 1000) }),
      async onopen() {
        onOpen();
      },
      onmessage(event: EventSourceMessage) {
        if (event.event && event.event.trim() !== '') {
          onMessage({
            event: event.event as AgentSSEEvent['event'],
            data: JSON.parse(event.data) as AgentSSEEvent['data']
          });
        }
      },
      onclose() {
        onClose();
      },
      onerror(err: any) {
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

/**
 * View Shell session output
 * @param sessionId Session ID
 * @param shellSessionId Shell session ID
 * @returns Shell session output content
 */
export async function viewShellSession(sessionId: string, shellSessionId: string): Promise<ShellViewResponse> {
  const response = await apiClient.post<ApiResponse<ShellViewResponse>>(`/sessions/${sessionId}/shell`, { session_id: shellSessionId });
  return response.data.data;
}

/**
 * View file content
 * @param sessionId Session ID
 * @param file File path
 * @returns File content
 */
export async function viewFile(sessionId: string, file: string): Promise<FileViewResponse> {
  const response = await apiClient.post<ApiResponse<FileViewResponse>>(`/sessions/${sessionId}/file`, { file });
  return response.data.data;
}