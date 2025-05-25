<template>
  <SimpleBar ref="simpleBarRef" @scroll="handleScroll">
    <div
      class="relative flex flex-col h-full flex-1 min-w-0 mx-auto w-full max-w-full sm:max-w-[768px] sm:min-w-[390px] px-5">
      <div
        class="sticky top-0 z-10 bg-[var(--background-gray-main)] flex-shrink-0 flex flex-row items-center justify-between pt-3 pb-1">
        <div class="flex w-full flex-col gap-[4px]">
          <div
            class="text-[var(--text-primary)] text-lg font-medium w-full flex flex-row items-center justify-between flex-1 min-w-0">
            <div @click="handleGoHome"
              class="flex h-8 w-8 items-center justify-center cursor-pointer hover:bg-[var(--fill-tsp-gray-main)] rounded-md">
              <Bot class="h-6 w-6 text-[var(--icon-secondary)]" :size="24" />
            </div>
            <div class="flex flex-row items-center gap-2 flex-1 min-w-0">
              <span class="whitespace-nowrap text-ellipsis overflow-hidden">
                {{ title }}
              </span>
            </div>
          </div>
          <div class="w-full flex justify-between items-center">
          </div>
        </div>
      </div>

      <div class="flex flex-col w-full gap-[12px] pb-[80px] pt-[12px] flex-1 overflow-y-auto">
        <ChatMessage v-for="(message, index) in messages" :key="index" :message="message"
          @toolClick="handleToolClick" />

        <!-- Loading indicator -->
        <div v-if="isLoading" class="flex items-center gap-1 text-[var(--text-tertiary)] text-sm"><span>{{ $t('Thinking') }}</span><span
            class="flex gap-1 relative top-[4px]"><span
              class="w-[3px] h-[3px] rounded animate-bounce-dot bg-[var(--icon-tertiary)]"
              style="animation-delay: 0ms;"></span><span
              class="w-[3px] h-[3px] rounded animate-bounce-dot bg-[var(--icon-tertiary)]"
              style="animation-delay: 200ms;"></span><span
              class="w-[3px] h-[3px] rounded animate-bounce-dot bg-[var(--icon-tertiary)]"
              style="animation-delay: 400ms;"></span></span></div>
      </div>

      <div class="flex flex-col bg-[var(--background-gray-main)] sticky bottom-0">
        <template v-if="plan && plan.steps.length > 0">
          <button @click="handleFollow" v-if="!follow"
            class="flex items-center justify-center w-[36px] h-[36px] rounded-full bg-[var(--background-white-main)] hover:bg-[var(--background-gray-main)] clickable border border-[var(--border-main)] shadow-[0px_5px_16px_0px_var(--shadow-S),0px_0px_1.25px_0px_var(--shadow-S)] absolute -top-20 left-1/2 -translate-x-1/2">
            <ArrowDown class="text-[var(--icon-primary)]" :size="20" />
          </button>
          <div class="[&amp;:not(:empty)]:pb-2 bg-[var(--background-gray-main)] rounded-[22px_22px_0px_0px]">
            <div v-if="isShowPlanPanel"
              class="border border-black/8 dark:border-[var(--border-main)] bg-[var(--background-menu-white)] rounded-[16px] sm:rounded-[12px] shadow-[0px_0px_1px_0px_rgba(0,_0,_0,_0.05),_0px_8px_32px_0px_rgba(0,_0,_0,_0.04)] z-99 flex flex-col py-4">
              <div class="flex px-4 mb-4 w-full">
                <div class="flex items-start ml-auto">
                  <div class="flex items-center justify-center gap-2">
                    <div @click="isShowPlanPanel = false"
                      class="flex h-7 w-7 items-center justify-center cursor-pointer hover:bg-[var(--fill-tsp-gray-main)] rounded-md">
                      <ChevronDown class="text-[var(--icon-tertiary)]" :size="16" />
                    </div>
                  </div>
                </div>
              </div>
              <div class="px-4">
                <div class="bg-[var(--fill-tsp-gray-main)] rounded-lg pt-4 pb-2">
                  <div class="flex justify-between w-full px-4"><span
                      class="text-[var(--text-primary)] font-bold">{{ $t('Task Progress') }}</span>
                    <div class="flex items-center gap-3"><span class="text-xs text-[var(--text-tertiary)]">{{ planProgress() }}</span>
                    </div>
                  </div>
                  <div class="max-h-[min(calc(100vh-360px),400px)] overflow-y-auto">
                    <div v-for="step in plan.steps" :key="step.id"
                      class="flex items-start gap-2.5 w-full px-4 py-2 truncate">
                      <StepSuccessIcon v-if="step.status === 'completed'" />
                      <Clock v-else class="relative top-[2px] flex-shrink-0" :size="16" />
                      <div class="flex flex-col w-full gap-[2px] truncate">
                        <div class="text-sm truncate" title="{{ step.description }}"
                          style="color: var(--text-primary);">
                          {{ step.description }}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
            <div v-if="!isShowPlanPanel" @click="isShowPlanPanel = true"
              class="flex flex-row items-start justify-between pe-3 relative clickable border border-black/8 dark:border-[var(--border-main)] bg-[var(--background-menu-white)] rounded-[16px] sm:rounded-[12px] shadow-[0px_0px_1px_0px_rgba(0,_0,_0,_0.05),_0px_8px_32px_0px_rgba(0,_0,_0,_0.04)] z-99">
              <div class="flex-1 min-w-0 relative overflow-hidden">
                <div class="w-full" style="height: 36px; --offset: -36px;">
                  <div class="w-full">
                    <div class="flex items-start gap-2.5 w-full px-4 py-2 truncate">
                      <StepSuccessIcon v-if="planCompleted()" />
                      <Clock v-else class="relative top-[2px] flex-shrink-0" :size="16" />
                      <div class="flex flex-col w-full gap-[2px] truncate">
                        <div class="text-sm truncate" :title="runningStep()" style="color: var(--text-tertiary);">
                          {{ runningStep() }}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <button
                class="flex h-full cursor-pointer justify-center gap-2 hover:opacity-80 flex-shrink-0 items-start py-2.5">
                <span class="text-xs text-[var(--text-tertiary)] hidden sm:flex">{{ planProgress() }}</span>
                <ChevronUp class="text-[var(--icon-tertiary)]" :size="16" />
              </button>
            </div>
          </div>
        </template>
        <ChatBox v-model="inputMessage" :rows="1" @submit="chat(inputMessage)" />
      </div>
    </div>
    <ToolPanel ref="toolPanel" :sessionId="sessionId" :realTime="realTime" @jumpToRealTime="jumpToRealTime" />
  </SimpleBar>
</template>

<script setup lang="ts">
import SimpleBar from '../components/SimpleBar.vue';
import { ref, onMounted, watch, nextTick } from 'vue';
import { useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import ChatBox from '../components/ChatBox.vue';
import ChatMessage from '../components/ChatMessage.vue';
import * as agentApi from '../api/agent';
import { Message, MessageContent, ToolContent, StepContent } from '../types/message';
import { 
  StepEventData, 
  ToolEventData, 
  MessageEventData, 
  ErrorEventData, 
  TitleEventData, 
  PlanEventData, 
  AgentSSEEvent 
} from '../types/event';
import ToolPanel from '../components/ToolPanel.vue';
import { ArrowDown, Bot, Clock, ChevronUp, ChevronDown } from 'lucide-vue-next';
import StepSuccessIcon from '../components/icons/StepSuccessIcon.vue';
import { showErrorToast } from '../utils/toast';

const router = useRouter();
const { t } = useI18n();
const inputMessage = ref('');
const isLoading = ref(false);
const sessionId = ref<string>();
const messages = ref<Message[]>([]);
const toolPanel = ref();
const realTime = ref(true);
const follow = ref(true);
const simpleBarRef = ref<InstanceType<typeof SimpleBar>>();
const title = ref(t('New Chat'));
const isShowPlanPanel = ref(false)
const plan = ref<PlanEventData>();
const lastNoMessageTool = ref<ToolContent>();

// Watch message changes and automatically scroll to bottom
watch(messages, async () => {
  await nextTick();
  if (follow.value) {
    simpleBarRef.value?.scrollToBottom();
  }
}, { deep: true });

const runningStep = (): string => {
  for (const step of plan.value?.steps ?? []) {
    if (step.status === 'running') {
      return step.description;
    }
  }
  return t('Confirm Task Completion');
}

const planCompleted = (): boolean => {
  return plan.value?.steps.every(step => step.status === 'completed') ?? false;
}

const planProgress = (): string => {
  const completedSteps = plan.value?.steps.filter(step => step.status === 'completed').length ?? 0;
  return `${completedSteps} / ${plan.value?.steps.length ?? 1}`;
}

const getLastStep = (): StepContent | undefined => {
  return messages.value.filter(message => message.type === 'step').pop()?.content as StepContent;
}

// Handle message event
const handleMessageEvent = (messageData: MessageEventData) => {
  messages.value.push({
    type: 'assistant',
    content: {
      ...messageData
    } as MessageContent,
  });
}

// Handle tool event
const handleToolEvent = (toolData: ToolEventData) => {
  const lastStep = getLastStep();
  let toolContent : ToolContent = {
    ...toolData
  }
  if (lastStep?.status === 'running') {
    lastStep.tools.push(toolContent);
  } else {
    messages.value.push({
      type: 'tool',
      content: toolContent,
    });
  }
  if (toolContent.name !== 'message') {
    lastNoMessageTool.value = toolContent;
    if (realTime.value) {
      toolPanel.value.show(toolContent);
    }
  }
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
  } else if (event.event === 'tool') {
    handleToolEvent(event.data as ToolEventData);
  } else if (event.event === 'step') {
    handleStepEvent(event.data as StepEventData);
  } else if (event.event === 'done') {
    isLoading.value = false;
  } else if (event.event === 'error') {
    handleErrorEvent(event.data as ErrorEventData);
  } else if (event.event === 'title') {
    handleTitleEvent(event.data as TitleEventData);
  } else if (event.event === 'plan') {
    handlePlanEvent(event.data as PlanEventData);
  }
}

const chat = async (message: string = '') => {
  if (!sessionId.value) return;

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

  // Automatically enable follow mode when sending message
  follow.value = true;

  // Clear input field
  inputMessage.value = '';
  isLoading.value = true;

  try {
    // Use the split event handler function
    await agentApi.chatWithSession(
      sessionId.value,
      message,
      {
        onOpen: () => {
          console.log('Chat opened');
          isLoading.value = true;
        },
        onMessage: (event) => {
          handleEvent(event);
        },
        onClose: () => {
          console.log('Chat closed');
          isLoading.value = false;
        },
        onError: (error) => {
          console.error('Chat error:', error);
          isLoading.value = false;
        }
      }
    );
  } catch (error) {
    console.error('Chat error:', error);
    isLoading.value = false;
  }
}

const restoreSession = async () => {
  if (!sessionId.value) {
    showErrorToast(t('Session not found'));
    return;
  }
  const session = await agentApi.getSession(sessionId.value);
  realTime.value = false;
  for (const event of session.events) {
    handleEvent(event);
  }
  realTime.value = true;
  return session;
}

// Initialize active conversation
onMounted(() => {
  const routeParams = router.currentRoute.value.params;
  if (routeParams.sessionId) {
    // If sessionId is included in URL, use it directly
    sessionId.value = String(routeParams.sessionId) as string;
    // Get initial message from history.state
    const message = history.state?.message;
    history.replaceState({}, document.title);
    if (message) {
      chat(message);
    } else {
      restoreSession().then(() => {
        chat();
      });
    }
  }
});


const handleToolClick = (tool: ToolContent) => {
  realTime.value = false;
  if (toolPanel.value && sessionId.value) {
    toolPanel.value.show(tool);
  }
}

const jumpToRealTime = () => {
  realTime.value = true;
  if (lastNoMessageTool.value) {
    toolPanel.value.show(lastNoMessageTool.value);
  }
}

const handleFollow = () => {
  follow.value = true;
  simpleBarRef.value?.scrollToBottom();
}

const handleScroll = (_: Event) => {
  follow.value = simpleBarRef.value?.isScrolledToBottom() ?? false;
}

const handleGoHome = () => {
  router.push('/');
}
</script>

<style scoped>
.animate-bounce-dot {
  display: inline-block;
  animation: dot-animation 1.5s infinite;
}

@keyframes dot-animation {
  0% {
    transform: translateY(0);
  }

  20% {
    transform: translateY(-4px);
  }

  40% {
    transform: translateY(0);
  }

  100% {
    transform: translateY(0);
  }
}

.\[\&\:not\(\:empty\)\]\:pb-2:not(:empty) {
  padding-bottom: .5rem;
}
</style>../types/event