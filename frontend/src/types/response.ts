import { AgentSSEEvent } from "./event";

export interface CreateSessionResponse {
    session_id: string;
}

export interface GetSessionResponse {
    session_id: string;
    title: string | null;
    events: AgentSSEEvent[];
}

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

export interface FileViewResponse {
    content: string;
    file: string;
  }
  