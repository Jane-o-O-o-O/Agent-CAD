<template>
  <SimpleBar>
    <div class="flex h-full w-full min-w-0 flex-1 flex-col px-4 sm:px-7">
      <header class="sticky top-0 z-10 w-full py-4">
        <div class="flex w-full items-center justify-between">
          <div class="relative z-20 flex h-9 flex-shrink-0 items-center gap-2 overflow-hidden">
            <button
              v-if="!isLeftPanelShow"
              type="button"
              class="cad-icon-button flex h-8 w-8 items-center justify-center"
              @click="toggleLeftPanel">
              <PanelLeft class="size-5 text-[var(--icon-secondary)]" />
            </button>
            <div class="flex items-center gap-1 rounded-xl border border-[var(--border-light)] bg-[var(--cad-panel-strong)] px-2 py-1">
              <Bot :size="30" />
              <ManusLogoTextIcon />
            </div>
          </div>

          <div class="flex items-center gap-2">
            <div
              class="relative flex items-center"
              aria-expanded="false"
              aria-haspopup="dialog"
              @mouseenter="handleUserMenuEnter"
              @mouseleave="handleUserMenuLeave">
              <div class="relative flex flex-shrink-0 cursor-pointer items-center justify-center font-bold">
                <div
                  class="relative flex flex-shrink-0 items-center justify-center overflow-hidden rounded-full font-bold"
                  style="width: 32px; height: 32px; font-size: 16px; color: rgba(255, 255, 255, 0.9); background-color: rgb(59, 130, 246);">
                  {{ avatarLetter }}
                </div>
              </div>
              <div
                v-if="showUserMenu"
                class="absolute right-0 top-full z-50 mr-[-15px] mt-1"
                @mouseenter="handleUserMenuEnter"
                @mouseleave="handleUserMenuLeave">
                <UserMenu />
              </div>
            </div>
          </div>
        </div>
      </header>

      <main class="cad-shortcut-stage mx-auto flex w-full max-w-[980px] flex-1 items-center pb-16 pt-8">
        <section class="w-full">
          <div class="cad-command-deck cad-quick-command relative overflow-hidden rounded-[26px] border border-[var(--border-light)] px-5 py-5 sm:px-7 sm:py-6">
            <div class="cad-scanline" aria-hidden="true"></div>
            <div class="relative flex flex-col gap-4">
              <div class="grid grid-cols-1 gap-3 md:grid-cols-3">
                <button
                  v-for="prompt in prompts"
                  :key="prompt.title"
                  class="cad-prompt-tile cad-shortcut-tile text-left"
                  type="button"
                  @click="usePrompt(prompt.message)">
                  <component :is="prompt.icon" class="size-6 text-[var(--cad-blue)]" />
                  <span class="text-[16px] font-semibold text-[var(--text-primary)]">{{ prompt.title }}</span>
                  <span class="text-[13px] leading-5 text-[var(--text-tertiary)]">{{ prompt.caption }}</span>
                </button>
              </div>

              <div class="cad-input-dock flex w-full flex-col rounded-[24px]">
                <ChatBox
                  :rows="3"
                  v-model="message"
                  @submit="handleSubmit"
                  :isRunning="isSubmitting"
                  :attachments="attachments" />
              </div>
            </div>
          </div>
        </section>
      </main>
    </div>
  </SimpleBar>
</template>

<script setup lang="ts">
import SimpleBar from '../components/SimpleBar.vue';
import { ref, onMounted, computed } from 'vue';
import { useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import ChatBox from '../components/ChatBox.vue';
import { createSession } from '../api/agent';
import { showErrorToast } from '../utils/toast';
import {
  Bot,
  DraftingCompass,
  PanelLeft,
  TerminalSquare,
  Workflow,
} from 'lucide-vue-next';
import ManusLogoTextIcon from '../components/icons/ManusLogoTextIcon.vue';
import type { FileInfo } from '../api/file';
import { useLeftPanel } from '../composables/useLeftPanel';
import { useFilePanel } from '../composables/useFilePanel';
import { useAuth } from '../composables/useAuth';
import UserMenu from '../components/UserMenu.vue';

const { t } = useI18n();
const router = useRouter();
const message = ref('');
const isSubmitting = ref(false);
const attachments = ref<FileInfo[]>([]);
const { toggleLeftPanel, isLeftPanelShow } = useLeftPanel();
const { hideFilePanel } = useFilePanel();
const { currentUser } = useAuth();

const prompts = [
  {
    title: '生成 CAD 方案',
    caption: '需求拆解、结构规划、建模步骤',
    message: '帮我把这个 CAD 任务拆成可执行方案，并给出建模步骤。',
    icon: Workflow,
  },
  {
    title: '检查工程文件',
    caption: '读取项目、定位问题、给出修复点',
    message: '检查这个项目结构，找出最影响 CAD 大王运行体验的问题。',
    icon: TerminalSquare,
  },
  {
    title: '优化界面体验',
    caption: '布局、文案、交互流程',
    message: '把这个 CAD 大王界面继续优化成更完整、更专业的工作台。',
    icon: DraftingCompass,
  },
];

const avatarLetter = computed(() => {
  return currentUser.value?.fullname?.charAt(0)?.toUpperCase() || 'M';
});

const showUserMenu = ref(false);
const userMenuTimeout = ref<number | null>(null);

const handleUserMenuEnter = () => {
  if (userMenuTimeout.value) {
    clearTimeout(userMenuTimeout.value);
    userMenuTimeout.value = null;
  }
  showUserMenu.value = true;
};

const handleUserMenuLeave = () => {
  userMenuTimeout.value = setTimeout(() => {
    showUserMenu.value = false;
  }, 200);
};

const usePrompt = (value: string) => {
  message.value = value;
};

onMounted(() => {
  hideFilePanel();
});

const handleSubmit = async () => {
  if (!message.value.trim() || isSubmitting.value) return;

  isSubmitting.value = true;

  try {
    const session = await createSession();
    const sessionId = session.session_id;

    router.push({
      path: `/chat/${sessionId}`,
      state: {
        message: message.value,
        files: attachments.value.map((file: FileInfo) => ({
          file_id: file.file_id,
          filename: file.filename,
          content_type: file.content_type,
          size: file.size,
          upload_date: file.upload_date,
        })),
      },
    });
  } catch (error) {
    console.error('Failed to create session:', error);
    showErrorToast(t('Failed to create session, please try again later'));
    isSubmitting.value = false;
  }
};
</script>
