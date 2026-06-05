<template>
  <SimpleBar ref="simpleBarRef" @scroll="handleScroll">
    <div class="relative flex flex-col h-full flex-1 min-w-0 px-4">
      <div
        class="sm:min-w-[390px] flex flex-row items-center justify-between pt-2 pb-1 gap-1 sticky top-0 z-10 cad-surface flex-shrink-0">
        <div class="flex items-center flex-1">
          <div class="relative flex items-center">
            <div @click="toggleLeftPanel" v-if="!isLeftPanelShow"
              class="flex h-7 w-7 items-center justify-center cursor-pointer rounded-md hover:bg-[var(--fill-tsp-gray-main)]">
              <PanelLeft class="size-5 text-[var(--icon-secondary)]" />
            </div>
          </div>
        </div>
        <div class="max-w-full sm:max-w-[768px] sm:min-w-[390px] flex w-full flex-col gap-[4px] overflow-hidden">
          <div
            class="text-[var(--text-primary)] text-lg font-medium w-full flex flex-row items-center justify-between flex-1 min-w-0 gap-2">
            <div class="flex flex-row items-center gap-[6px] flex-1 min-w-0">
              <span class="whitespace-nowrap text-ellipsis overflow-hidden">
                {{ title }}
              </span>
            </div>
            <div class="flex items-center gap-2 flex-shrink-0">
              <span class="relative flex-shrink-0" aria-expanded="false" aria-haspopup="dialog">
                <Popover>
                  <PopoverTrigger>
                    <button
                      class="h-8 px-3 rounded-[100px] inline-flex items-center gap-1 clickable outline outline-1 outline-offset-[-1px] outline-[var(--border-btn-main)] hover:bg-[var(--fill-tsp-white-light)] me-1.5">
                      <ShareIcon color="var(--icon-secondary)" />
                      <span class="text-[var(--text-secondary)] text-sm font-medium">{{ t('Share') }}</span>
                    </button>
                  </PopoverTrigger>
                  <PopoverContent>
                    <div
                      class="w-[400px] flex flex-col rounded-2xl bg-[var(--background-menu-white)] shadow-[0px_8px_32px_0px_var(--shadow-S),0px_0px_0px_1px_var(--border-light)]"
                      style="max-width: calc(-16px + 100vw);">
                      <div class="flex flex-col pt-[12px] px-[16px] pb-[16px]">
                        <!-- Private mode option -->
                        <div @click="handleShareModeChange('private')"
                          :class="{'pointer-events-none opacity-50': sharingLoading}"
                          class="flex items-center gap-[10px] px-[8px] -mx-[8px] py-[8px] rounded-[8px] clickable hover:bg-[var(--fill-tsp-white-main)]">
                          <div
                            :class="shareMode === 'private' ? 'bg-[var(--Button-primary-black)]' : 'bg-[var(--fill-tsp-white-dark)]'"
                            class="w-[32px] h-[32px] rounded-[8px] flex items-center justify-center">
                            <Lock :size="16" :stroke="shareMode === 'private' ? 'var(--text-onblack)' : 'var(--icon-primary)'" :stroke-width="2" /></div>
                          <div class="flex flex-col flex-1 min-w-0">
                            <div class="text-sm font-medium text-[var(--text-primary)]">{{ t('Private Only') }}</div>
                            <div class="text-[13px] text-[var(--text-tertiary)]">{{ t('Only visible to you') }}</div>
                          </div><Check :size="20" :class="shareMode === 'private' ? 'ml-auto' : 'ml-auto invisible'" :color="shareMode === 'private' ? 'var(--icon-primary)' : 'var(--icon-tertiary)'" />
                        </div>
                        <!-- Public mode option -->
                        <div @click="handleShareModeChange('public')"
                          :class="{'pointer-events-none opacity-50': sharingLoading}"
                          class="flex items-center gap-[10px] px-[8px] -mx-[8px] py-[8px] rounded-[8px] clickable hover:bg-[var(--fill-tsp-white-main)]">
                          <div
                            :class="shareMode === 'public' ? 'bg-[var(--Button-primary-black)]' : 'bg-[var(--fill-tsp-white-dark)]'"
                            class="w-[32px] h-[32px] rounded-[8px] flex items-center justify-center">
                            <Globe :size="16" :stroke="shareMode === 'public' ? 'var(--text-onblack)' : 'var(--icon-primary)'" :stroke-width="2" /></div>
                          <div class="flex flex-col flex-1 min-w-0">
                            <div class="text-sm font-medium text-[var(--text-primary)]">{{ t('Public Access') }}</div>
                            <div class="text-[13px] text-[var(--text-tertiary)]">{{ t('Anyone with the link can view') }}</div>
                          </div><Check :size="20" :class="shareMode === 'public' ? 'ml-auto' : 'ml-auto invisible'" :color="shareMode === 'public' ? 'var(--icon-primary)' : 'var(--icon-tertiary)'" />
                        </div>
                        <div class="border-t border-[var(--border-main)] mt-[4px]"></div>
                        
                        <!-- Show instant share button when in private mode -->
                        <div v-if="shareMode === 'private'">
                          <button @click.stop="handleInstantShare"
                            :disabled="sharingLoading"
                            class="inline-flex items-center justify-center whitespace-nowrap font-medium transition-colors hover:opacity-90 active:opacity-80 bg-[var(--Button-primary-black)] text-[var(--text-onblack)] h-[36px] px-[12px] rounded-[10px] gap-[6px] text-sm min-w-16 mt-[16px] w-full disabled:opacity-50 disabled:cursor-not-allowed"
                            data-tabindex="" tabindex="-1">
                            <div v-if="sharingLoading" class="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
                            <Link v-else :size="16" stroke="currentColor" :stroke-width="2" />
                            {{ sharingLoading ? t('Sharing...') : t('Share Instantly') }}
                          </button>
                        </div>
                        
                        <!-- Show copy link button when in public mode -->
                        <div v-else>
                          <button @click.stop="handleCopyLink"
                            :class="linkCopied ? 'inline-flex items-center justify-center whitespace-nowrap font-medium transition-colors active:opacity-80 bg-[var(--Button-primary-white)] text-[var(--text-primary)] hover:opacity-70 active:hover-60 h-[36px] px-[12px] rounded-[10px] gap-[6px] text-sm min-w-16 mt-[16px] w-full border border-[var(--border-btn-main)] shadow-none' : 'inline-flex items-center justify-center whitespace-nowrap font-medium transition-colors hover:opacity-90 active:opacity-80 bg-[var(--Button-primary-black)] text-[var(--text-onblack)] h-[36px] px-[12px] rounded-[10px] gap-[6px] text-sm min-w-16 mt-[16px] w-full'"
                            data-tabindex="" tabindex="-1">
                            <Link v-if="!linkCopied" :size="16" stroke="currentColor" :stroke-width="2" />
                            <Check v-else :size="16" color="var(--text-primary)" />
                            {{ linkCopied ? t('Link Copied') : t('Copy Link') }}
                          </button>
                        </div>
                      </div>
                    </div>
                  </PopoverContent>
                </Popover>
              </span>
              <button @click="handleFileListShow"
                class="p-[5px] flex items-center justify-center hover:bg-[var(--fill-tsp-white-dark)] rounded-lg cursor-pointer">
                <FileSearch class="text-[var(--icon-secondary)]" :size="18" />
              </button>
            </div>
          </div>
          <div class="w-full flex justify-between items-center">
          </div>
        </div>
        <div class="flex-1"></div>
      </div>
      <div class="mx-auto w-full max-w-full sm:max-w-[768px] sm:min-w-[390px] flex flex-col flex-1">
        <div class="flex flex-col w-full gap-[12px] pb-[64px] pt-[10px] flex-1 overflow-y-auto">
          <ChatMessage v-for="(message, index) in messages" :key="index" :message="message"
            :hideHeader="isConsecutiveAssistant(messages, index)"
            @toolClick="handleToolClick" />

          <!-- Loading indicator -->
          <LoadingIndicator v-if="isLoading" :text="$t('Thinking')" />
        </div>

        <div class="flex flex-col cad-surface sticky bottom-0 pb-1">
          <button @click="handleFollow" v-if="!follow"
            class="flex items-center justify-center w-[36px] h-[36px] rounded-full bg-[var(--background-white-main)] hover:bg-[var(--background-gray-main)] clickable border border-[var(--border-main)] shadow-[0px_5px_16px_0px_var(--shadow-S),0px_0px_1.25px_0px_var(--shadow-S)] absolute -top-20 left-1/2 -translate-x-1/2">
            <ArrowDown class="text-[var(--icon-primary)]" :size="20" />
          </button>
          <PlanPanel v-if="plan && plan.steps.length > 0" :plan="plan" />
          <ChatBox v-model="inputMessage" :rows="1" @submit="handleSubmit" :isRunning="isLoading" @stop="handleStop"
            :attachments="attachments">
            <template #footer-actions>
              <div class="cad-home-shortcuts cad-session-shortcuts grid min-w-0 flex-1 items-center gap-1">
                <button
                  v-for="prompt in cadPrompts"
                  :key="prompt.title"
                  class="cad-home-shortcut-chip"
                  type="button"
                  :title="prompt.caption"
                  @click="usePrompt(prompt.message)">
                  <component :is="prompt.icon" class="size-3.5 shrink-0" />
                  <span>{{ prompt.shortTitle }}</span>
                </button>
              </div>
            </template>
          </ChatBox>
        </div>
      </div>
    </div>
    <CADRenderPanel
      v-if="isCadMode"
      :document="cadDocument"
      :dxfFile="cadDxfFile"
      :planSteps="cadPlanSteps"
      :isBusy="cadBusy"
      :briefSummary="cadBriefSummary"
      @hide="isCadMode = false"
      @reset="resetCadWorkspace"
      @addCenterSlot="addCenterSlot"
      @downloadDxf="downloadDxf" />
    <ToolPanel v-else ref="toolPanel" :size="toolPanelSize" :sessionId="sessionId" :realTime="realTime" 
      :isShare="false"
      @jumpToRealTime="jumpToRealTime" />
  </SimpleBar>
</template>

<script setup lang="ts">
import SimpleBar from '../components/SimpleBar.vue';
import { computed, ref, onMounted, watch, nextTick, onUnmounted, reactive, toRefs } from 'vue';
import { useRouter, onBeforeRouteUpdate } from 'vue-router';
import { useI18n } from 'vue-i18n';
import ChatBox from '../components/ChatBox.vue';
import ChatMessage from '../components/ChatMessage.vue';
import * as agentApi from '../api/agent';
import { Message, MessageContent, ToolContent, StepContent, AttachmentsContent, isConsecutiveAssistant } from '../types/message';
import {
  StepEventData,
  ToolEventData,
  MessageEventData,
  MessageDeltaEventData,
  ErrorEventData,
  TitleEventData,
  PlanEventData,
  AgentSSEEvent,
} from '../types/event';
import ToolPanel from '../components/ToolPanel.vue'
import CADRenderPanel from '../components/CADRenderPanel.vue';
import PlanPanel from '../components/PlanPanel.vue';
import { ArrowDown, FileSearch, PanelLeft, Lock, Globe, Link, Check } from 'lucide-vue-next';
import ShareIcon from '@/components/icons/ShareIcon.vue';
import { showErrorToast, showSuccessToast } from '../utils/toast';
import type { FileInfo } from '../api/file';
import { useLeftPanel } from '../composables/useLeftPanel'
import { useSessionFileList } from '../composables/useSessionFileList'
import { useFilePanel } from '../composables/useFilePanel'
import { copyToClipboard } from '../utils/dom'
import { SessionStatus } from '../types/response';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import LoadingIndicator from '@/components/ui/LoadingIndicator.vue';
import { cadPrompts } from '@/constants/cadPrompts';
import {
  applyCADOperation,
  downloadCADDocumentDxf,
  type CADPlanStep,
  type MechanicalCADDocument,
} from '@/api/cad';
import { downloadFile } from '@/api/file';

const router = useRouter()
const { t } = useI18n()
const { toggleLeftPanel, isLeftPanelShow } = useLeftPanel()
const { showSessionFileList } = useSessionFileList()
const { hideFilePanel } = useFilePanel()

// Create initial state factory
type CADProgressStatus = 'pending' | 'running' | 'done' | 'failed';
type CADProgressStep = CADPlanStep & { status: CADProgressStatus };

const createInitialState = () => ({
  inputMessage: '',
  isLoading: false,
  sessionId: undefined as string | undefined,
  messages: [] as Message[],
  toolPanelSize: 0,
  realTime: true,
  follow: true,
  title: t('New Chat'),
  plan: undefined as PlanEventData | undefined,
  lastNoMessageTool: undefined as ToolContent | undefined,
  lastMessageTool: undefined as ToolContent | undefined,
  lastTool: undefined as ToolContent | undefined,
  lastEventId: undefined as string | undefined,
  streamingAssistantIndex: -1,
  cancelCurrentChat: null as (() => void) | null,
  attachments: [] as FileInfo[],
  shareMode: 'private' as 'private' | 'public', // Default to private mode
  linkCopied: false,
  sharingLoading: false, // Loading state for share operations
  isCadMode: false,
  cadBusy: false,
  cadDocument: null as MechanicalCADDocument | null,
  cadDxfFile: null as FileInfo | null,
  cadPlanSteps: [] as CADProgressStep[],
  lastCadRequestKey: ''
});

// Create reactive state
const state = reactive(createInitialState());

// Destructure refs from reactive state
const {
  inputMessage,
  isLoading,
  sessionId,
  messages,
  toolPanelSize,
  realTime,
  follow,
  title,
  plan,
  lastNoMessageTool,
  lastTool,
  lastEventId,
  streamingAssistantIndex,
  cancelCurrentChat,
  attachments,
  shareMode,
  linkCopied,
  sharingLoading,
  isCadMode,
  cadBusy,
  cadDocument,
  cadDxfFile,
  cadPlanSteps,
  lastCadRequestKey
} = toRefs(state);

// Non-state refs that don't need reset
const toolPanel = ref<InstanceType<typeof ToolPanel>>()
const simpleBarRef = ref<InstanceType<typeof SimpleBar>>();
const cadBriefSummary = computed(() => {
  const brief = cadDocument.value?.brief;
  if (!brief) return cadBusy.value ? 'Generating DXF drawing' : 'DXF generation process';
  return `${brief.part_type || 'part'} | ${brief.features.length} parsed features`;
});

// Reset all refs to their initial values
const resetState = () => {
  // Cancel any existing chat connection
  if (cancelCurrentChat.value) {
    cancelCurrentChat.value();
  }

  // Reset reactive state to initial values
  Object.assign(state, createInitialState());
};

// Watch message changes and automatically scroll to bottom
watch(messages, async () => {
  await nextTick();
  if (follow.value) {
    simpleBarRef.value?.scrollToBottom();
  }
}, { deep: true });



const getLastStep = (): StepContent | undefined => {
  return messages.value.filter(message => message.type === 'step').pop()?.content as StepContent;
}

// Handle message event
const handleMessageEvent = (messageData: MessageEventData) => {
  if (messageData.role === 'assistant' && streamingAssistantIndex.value >= 0) {
    const message = messages.value[streamingAssistantIndex.value];
    if (message?.type === 'assistant') {
      Object.assign(message.content, {
        ...messageData,
      } as MessageContent);
      streamingAssistantIndex.value = -1;
      if (messageData.attachments?.length > 0) {
        messages.value.push({
          type: 'attachments',
          content: {
            ...messageData
          } as AttachmentsContent,
        });
      }
      return;
    }
  }

  messages.value.push({
    type: messageData.role,
    content: {
      ...messageData
    } as MessageContent,
  });

  if (messageData.attachments?.length > 0) {
    messages.value.push({
      type: 'attachments',
      content: {
        ...messageData
      } as AttachmentsContent,
    });
  }
}

// Handle tool event
const handleToolEvent = (toolData: ToolEventData) => {
  const lastStep = getLastStep();
  let toolContent: ToolContent = {
    ...toolData
  }
  if (lastTool.value && lastTool.value.tool_call_id === toolContent.tool_call_id) {
    Object.assign(lastTool.value, toolContent);
  } else {
    if (lastStep?.status === 'running') {
      lastStep.tools.push(toolContent);
    } else {
      messages.value.push({
        type: 'tool',
        content: toolContent,
      });
    }
    lastTool.value = toolContent;
  }
  syncCadToolResult(toolContent);
  if (toolContent.name !== 'message') {
    lastNoMessageTool.value = toolContent;
    if (realTime.value && !isCadMode.value) {
      toolPanel.value?.showToolPanel(toolContent, true);
    }
  }
}

const handleMessageDeltaEvent = (messageData: MessageDeltaEventData) => {
  if (!messageData.delta) return;

  if (streamingAssistantIndex.value < 0) {
    streamingAssistantIndex.value = messages.value.length;
    messages.value.push({
      type: 'assistant',
      content: {
        content: '',
        role: 'assistant',
        timestamp: messageData.timestamp,
      } as MessageContent,
    });
  }

  const message = messages.value[streamingAssistantIndex.value];
  if (message?.type !== 'assistant') {
    streamingAssistantIndex.value = -1;
    return;
  }
  (message.content as MessageContent).content += messageData.delta;
}

function syncCadToolResult(toolContent: ToolContent) {
  if (toolContent.name !== 'cad') return;

  const functionName = toolContent.function;
  const isDxfGeneration = isDxfGenerationFunction(functionName);
  const isDxfProcess = isDxfGeneration || functionName === 'cad_analyze_request' || functionName === 'cad_validate_dxf';
  if (!isDxfProcess) return;

  const result = toolContent.content?.result;
  const data = result?.data ?? result;
  const success = result?.success !== false;

  if (functionName === 'cad_analyze_request') {
    if (toolContent.status === 'calling') {
      updateCadProgressStep('analyze', '分析绘图要求', '解析尺寸、结构和出图约束', 'running');
      return;
    }

    if (toolContent.status === 'called') {
      if (Array.isArray(data?.operations)) {
        cadPlanSteps.value = data.operations.map((operation: any, index: number) => ({
          id: `plan-${index + 1}`,
          title: operation.operation?.replace(/_/g, ' ') || `CAD step ${index + 1}`,
          description: JSON.stringify(operation.params ?? {}),
          operation,
          status: 'done',
        }));
      } else {
        updateCadProgressStep('analyze', '分析绘图要求', result?.message || 'CAD request analyzed', success ? 'done' : 'failed');
      }
    }
    return;
  }

  if (isDxfGeneration && toolContent.status === 'calling') {
    isCadMode.value = true;
    cadBusy.value = true;
    toolPanel.value?.hideToolPanel();
    updateCadProgressStep(
      'generate-dxf',
      functionName === 'cad_generate_dxf_from_spec' ? '生成最终 DXF' : '创建 DXF 文件',
      getCadToolDescription(toolContent),
      'running',
    );
    updateCadProgressStep('validate-dxf', '校验 DXF 文件', '等待生成完成后解析校验', 'pending');
    return;
  }

  if (isDxfGeneration && toolContent.status === 'called') {
    isCadMode.value = true;
    cadBusy.value = false;
    const outputPath = data?.output_path || toolContent.args?.output_path || '/home/ubuntu/output.dxf';
    updateCadProgressStep(
      'generate-dxf',
      functionName === 'cad_generate_dxf_from_spec' ? '生成最终 DXF' : '创建 DXF 文件',
      `${success ? '已写入' : '生成失败'}: ${outputPath}`,
      success ? 'done' : 'failed',
    );
    if (data?.validation) {
      updateCadProgressStep(
        'validate-dxf',
        '校验 DXF 文件',
        getCadValidationDescription(data.validation),
        data.validation.has_geometry === false ? 'failed' : 'done',
      );
    }
    if (data?.document) {
      cadDocument.value = data.document as MechanicalCADDocument;
    }
    const dxfFile = getCadDxfFile(data);
    if (dxfFile) {
      cadDxfFile.value = dxfFile;
    }
    return;
  }

  if (functionName === 'cad_validate_dxf') {
    isCadMode.value = true;
    cadBusy.value = toolContent.status === 'calling';
    toolPanel.value?.hideToolPanel();
    if (toolContent.status === 'calling') {
      updateCadProgressStep('validate-dxf', '校验 DXF 文件', getCadToolDescription(toolContent), 'running');
      return;
    }
    updateCadProgressStep(
      'validate-dxf',
      '校验 DXF 文件',
      data ? getCadValidationDescription(data) : result?.message || 'DXF validation finished',
      success ? 'done' : 'failed',
    );
    const dxfFile = getCadDxfFile(data);
    if (dxfFile) {
      cadDxfFile.value = dxfFile;
    }
    cadBusy.value = false;
  }
}

function isDxfGenerationFunction(functionName: string) {
  return functionName === 'cad_generate_dxf' || functionName === 'cad_generate_dxf_from_spec';
}

function updateCadProgressStep(
  id: string,
  title: string,
  description: string,
  status: CADProgressStatus,
) {
  const operation: CADPlanStep['operation'] = {
    operation: id,
    params: { description },
  };
  const existing = cadPlanSteps.value.find(step => step.id === id);
  if (existing) {
    existing.title = title;
    existing.description = description;
    existing.status = status;
    existing.operation = operation;
    return;
  }
  cadPlanSteps.value.push({ id, title, description, operation, status });
}

function getCadToolDescription(toolContent: ToolContent) {
  const target = toolContent.args?.output_path || toolContent.args?.file;
  if (target) return String(target);
  if (toolContent.args?.title) return String(toolContent.args.title);
  return '准备几何、图层、标注并写入 DXF';
}

function getCadValidationDescription(validation: any) {
  const entityCount = validation?.entity_count ?? validation?.entities;
  const version = validation?.dxf_version;
  const entityTypes = Array.isArray(validation?.entity_types) ? validation.entity_types.join(', ') : '';
  const parts = [
    version ? `版本 ${version}` : '',
    entityCount !== undefined ? `${entityCount} 个实体` : '',
    entityTypes,
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(' | ') : 'DXF validation passed';
}

function getCadDxfFile(data: any): FileInfo | null {
  const files = Array.isArray(data?.files) ? data.files : [];
  return files.find((file: FileInfo) => file.filename?.toLowerCase().endsWith('.dxf')) ?? null;
}

// Handle step event
const handleStepEvent = (stepData: StepEventData) => {
  const lastStep = getLastStep();
  if (stepData.status === 'running') {
    messages.value.push({
      type: 'step',
      content: {
        ...stepData,
        tools: []
      } as StepContent,
    });
  } else if (stepData.status === 'completed') {
    if (lastStep) {
      lastStep.status = stepData.status;
    }
  } else if (stepData.status === 'failed') {
    isLoading.value = false;
  }
}

// Handle error event
const handleErrorEvent = (errorData: ErrorEventData) => {
  isLoading.value = false;
  messages.value.push({
    type: 'assistant',
    content: {
      content: errorData.error,
      timestamp: errorData.timestamp
    } as MessageContent,
  });
}

// Handle title event
const handleTitleEvent = (titleData: TitleEventData) => {
  title.value = titleData.title;
}

// Handle plan event
const handlePlanEvent = (planData: PlanEventData) => {
  plan.value = planData;
}

// Main event handler function
const handleEvent = (event: AgentSSEEvent) => {
  if (event.event === 'message') {
    handleMessageEvent(event.data as MessageEventData);
  } else if (event.event === 'message_delta') {
    handleMessageDeltaEvent(event.data as MessageDeltaEventData);
  } else if (event.event === 'tool') {
    handleToolEvent(event.data as ToolEventData);
  } else if (event.event === 'step') {
    handleStepEvent(event.data as StepEventData);
  } else if (event.event === 'done') {
    isLoading.value = false;
    streamingAssistantIndex.value = -1;
    cancelCurrentChat.value = null;
  } else if (event.event === 'wait') {
    isLoading.value = false;
    streamingAssistantIndex.value = -1;
    cancelCurrentChat.value = null;
  } else if (event.event === 'error') {
    handleErrorEvent(event.data as ErrorEventData);
    streamingAssistantIndex.value = -1;
    cancelCurrentChat.value = null;
  } else if (event.event === 'title') {
    handleTitleEvent(event.data as TitleEventData);
  } else if (event.event === 'plan') {
    handlePlanEvent(event.data as PlanEventData);
  }
  lastEventId.value = event.data.event_id;
}

const handleSubmit = () => {
  chat(inputMessage.value, attachments.value);
}

const usePrompt = (value: string) => {
  inputMessage.value = value;
}

const chat = async (message: string = '', files: FileInfo[] = []) => {
  if (!sessionId.value) return;

  // Cancel any existing chat connection before starting a new one
  if (cancelCurrentChat.value) {
    cancelCurrentChat.value();
    cancelCurrentChat.value = null;
  }

  if (message.trim()) {
    // Add user message to conversation list
    messages.value.push({
      type: 'user',
      content: {
        content: message,
        timestamp: Math.floor(Date.now() / 1000)
      } as MessageContent,
    });
  }

  if (files.length > 0) {
    messages.value.push({
      type: 'attachments',
      content: {
        role: 'user',
        attachments: files
      } as AttachmentsContent,
    });
  }

  // Automatically enable follow mode when sending message
  follow.value = true;

  // Clear input field and attachments
  inputMessage.value = '';
  attachments.value = [];
  isLoading.value = true;

  try {
    // Use the split event handler function and store the cancel function
    cancelCurrentChat.value = await agentApi.chatWithSession(
      sessionId.value,
      message,
      lastEventId.value,
      files.map((file: FileInfo) => ({file_id : file.file_id, 
                                        filename : file.filename})),
      {
        onOpen: () => {
          console.log('Chat opened');
          isLoading.value = true;
        },
        onMessage: ({ event, data }) => {
          handleEvent({
            event: event as AgentSSEEvent['event'],
            data: data as AgentSSEEvent['data']
          });
        },
        onClose: () => {
          console.log('Chat closed');
          isLoading.value = false;
          // Clear the cancel function when connection is closed normally
          if (cancelCurrentChat.value) {
            cancelCurrentChat.value = null;
          }
        },
        onError: (error) => {
          console.error('Chat error:', error);
          isLoading.value = false;
          // Clear the cancel function when there's an error
          if (cancelCurrentChat.value) {
            cancelCurrentChat.value = null;
          }
        }
      }
    );
  } catch (error) {
    console.error('Chat error:', error);
    isLoading.value = false;
    cancelCurrentChat.value = null;
  }
}

const restoreSession = async () => {
  if (!sessionId.value) {
    showErrorToast(t('Session not found'));
    return;
  }
  const session = await agentApi.getSession(sessionId.value);
  // Initialize share mode based on session state
  shareMode.value = session.is_shared ? 'public' : 'private';
  realTime.value = false;
  for (const event of session.events) {
    handleEvent(event);
  }
  realTime.value = true;
  if (session.status === SessionStatus.RUNNING) {
    await chat();
  }
  const sessionFiles = await agentApi.getSessionFiles(sessionId.value);
  const latestDxf = [...sessionFiles].reverse().find(file => file.filename?.toLowerCase().endsWith('.dxf'));
  if (latestDxf && isCadMode.value && !cadDocument.value) {
    cadDxfFile.value = latestDxf;
  }
  agentApi.clearUnreadMessageCount(sessionId.value);
}



onBeforeRouteUpdate((to, _, next) => {
  toolPanel.value?.hideToolPanel();
  hideFilePanel();
  resetState();
  if (to.params.sessionId) {
    messages.value = [];
    sessionId.value = String(to.params.sessionId) as string;
    restoreSession();
  }
  next();
})

// Initialize active conversation
onMounted(() => {
  hideFilePanel();
  const routeParams = router.currentRoute.value.params;
  if (routeParams.sessionId) {
    // If sessionId is included in URL, use it directly
    sessionId.value = String(routeParams.sessionId) as string;
    // Get initial message from history.state
    const message = history.state?.message;
    const files: FileInfo[] = history.state?.files;
    history.replaceState({}, document.title);
    if (message) {
      chat(message, files);
    } else {
      restoreSession();
    }
  }


});

onUnmounted(() => {
  if (cancelCurrentChat.value) {
    cancelCurrentChat.value();
    cancelCurrentChat.value = null;
  }
})

const isLastNoMessageTool = (tool: ToolContent) => {
  return tool.tool_call_id === lastNoMessageTool.value?.tool_call_id;
}

const isLiveTool = (tool: ToolContent) => {
  if (tool.status === 'calling') {
    return true;
  }
  if (!isLastNoMessageTool(tool)) {
    return false;
  }
  if (tool.timestamp > Date.now() - 5 * 60 * 1000) {
    return true;
  }
  return false;
}

const handleToolClick = async (tool: ToolContent) => {
  realTime.value = false;
  isCadMode.value = false;
  await nextTick();
  if (sessionId.value) {
    toolPanel.value?.showToolPanel(tool, isLiveTool(tool));
  }
}

const jumpToRealTime = async () => {
  realTime.value = true;
  isCadMode.value = false;
  await nextTick();
  if (lastNoMessageTool.value) {
    toolPanel.value?.showToolPanel(lastNoMessageTool.value, isLiveTool(lastNoMessageTool.value));
  }
}

const handleFollow = () => {
  follow.value = true;
  simpleBarRef.value?.scrollToBottom();
}

const handleScroll = (_: Event) => {
  follow.value = simpleBarRef.value?.isScrolledToBottom() ?? false;
}

const handleStop = () => {
  if (sessionId.value) {
    agentApi.stopSession(sessionId.value);
  }
}

const handleFileListShow = () => {
  showSessionFileList()
}

// Share functionality handlers
const handleShareModeChange = async (mode: 'private' | 'public') => {
  if (!sessionId.value || sharingLoading.value) return;
  
  // If mode is same as current, no need to call API
  if (shareMode.value === mode) {
    linkCopied.value = false;
    return;
  }
  
  try {
    sharingLoading.value = true;
    
    if (mode === 'public') {
      await agentApi.shareSession(sessionId.value);
    } else {
      await agentApi.unshareSession(sessionId.value);
    }
    
    shareMode.value = mode;
    linkCopied.value = false;
  } catch (error) {
    console.error('Error changing share mode:', error);
    showErrorToast(t('Failed to change sharing settings'));
  } finally {
    sharingLoading.value = false;
  }
}

const handleInstantShare = async () => {
  if (!sessionId.value) return;
  
  try {
    sharingLoading.value = true;
    await agentApi.shareSession(sessionId.value);
    shareMode.value = 'public';
    linkCopied.value = false;
  } catch (error) {
    console.error('Error sharing session:', error);
    showErrorToast(t('Failed to share session'));
  } finally {
    sharingLoading.value = false;
  }
}

const handleCopyLink = async () => {
  if (!sessionId.value) return;
  
  const shareUrl = `${window.location.origin}/share/${sessionId.value}`;
  
  try {
    const success = await copyToClipboard(shareUrl);
    
    if (success) {
      linkCopied.value = true;
      setTimeout(() => {
        linkCopied.value = false;
      }, 3000);
      showSuccessToast(t('Link copied to clipboard'));
    } else {
      showErrorToast(t('Failed to copy link'));
    }
  } catch (error) {
    console.error('Error copying share link:', error);
    showErrorToast(t('Failed to copy link'));
  }
}

async function addCenterSlot() {
  if (!cadDocument.value) return;
  const plate = cadDocument.value.brief?.features.find(feature => feature.type === 'base_plate');
  const width = Number(plate?.width || 120);
  const height = Number(plate?.height || 80);
  try {
    const result = await applyCADOperation(cadDocument.value.id, {
      operation: 'add_slot',
      params: {
        center: [width / 2, height / 2],
        length: Math.min(width, height) * 0.38,
        width: Math.min(width, height) * 0.12,
        rotation: 0,
      },
    });
    cadDocument.value = result.document;
  } catch (error: any) {
    showErrorToast(error?.message || 'Failed to add slot');
  }
}

async function downloadDxf() {
  if (!cadDocument.value && !cadDxfFile.value) return;
  try {
    const blob = cadDxfFile.value
      ? await downloadFile(cadDxfFile.value.file_id)
      : await downloadCADDocumentDxf(cadDocument.value!.id);
    const url = URL.createObjectURL(blob);
    const link = window.document.createElement('a');
    link.href = url;
    link.download = cadDxfFile.value?.filename
      || `${cadDocument.value!.title.replace(/\s+/g, '_').toLowerCase() || cadDocument.value!.id}.dxf`;
    link.click();
    URL.revokeObjectURL(url);
  } catch (error: any) {
    showErrorToast(error?.message || 'Failed to download DXF');
  }
}

function resetCadWorkspace() {
  cadDocument.value = null;
  cadDxfFile.value = null;
  cadPlanSteps.value = [];
  lastCadRequestKey.value = '';
}
</script>

<style scoped>
</style>
