export type AgentSSEEvent = {
  event: 'tool' | 'step' | 'message' | 'error' | 'done' | 'title';
  data: ToolEventData | StepEventData | MessageEventData | ErrorEventData | DoneEventData | TitleEventData;
}

export interface ToolEventData {
  timestamp: number;
  name: string;
  function: string;
  args: {[key: string]: any};
}

export interface StepEventData {
  timestamp: number;
  status: "pending" | "running" | "completed" | "failed"
  id: string
  description: string
}

export interface MessageEventData {
  timestamp: number;
  content: string;
}

export interface ErrorEventData {
  timestamp: number;
  error: string;
}

export interface DoneEventData {
  timestamp: number;
}

export interface TitleEventData {
  timestamp: number;
  title: string;
}

export interface PlanEventData {
  timestamp: number;
  steps: StepEventData[];
}