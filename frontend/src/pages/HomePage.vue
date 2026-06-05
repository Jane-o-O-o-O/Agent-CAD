<template>
  <SimpleBar>
    <div class="flex h-full w-full min-w-0 flex-1 flex-col px-3 sm:px-5">
      <header class="sticky top-0 z-10 w-full py-3">
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

      <main class="cad-shortcut-stage mx-auto flex w-full max-w-[900px] flex-1 items-center pb-8 pt-3">
        <section class="w-full">
          <div class="cad-home-greeting" aria-label="CAD大王初始问候">
            <h1>吾乃CAD大王，有什么我能帮你的吗？</h1>
            <p>Ciallo～(∠・ω&lt; )⌒★</p>
          </div>
          <div class="cad-command-deck cad-quick-command cad-doubao-composer relative overflow-hidden rounded-[24px] border border-[var(--border-light)] px-2.5 py-2.5 sm:px-3 sm:py-3">
            <div class="cad-scanline" aria-hidden="true"></div>
            <div class="relative">
              <div class="cad-input-dock flex w-full flex-col rounded-[18px]">
                <ChatBox
                  :rows="2"
                  v-model="message"
                  v-model:attachments="attachments"
                  @submit="handleSubmit"
                  :isRunning="isSubmitting"
                  placeholder="给 CAD大王 一个 CAD 任务...">
                  <template #footer-actions>
                    <div class="cad-home-shortcuts grid min-w-0 flex-1 grid-cols-7 items-center gap-1">
                      <button
                        v-for="prompt in prompts"
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
import { showErrorToast } from '../utils/toast';
import * as agentApi from '../api/agent';
import {
  Bot,
  PanelLeft,
} from 'lucide-vue-next';
import ManusLogoTextIcon from '../components/icons/ManusLogoTextIcon.vue';
import type { FileInfo } from '../api/file';
import { useLeftPanel } from '../composables/useLeftPanel';
import { useFilePanel } from '../composables/useFilePanel';
import { useAuth } from '../composables/useAuth';
import UserMenu from '../components/UserMenu.vue';
import { cadPrompts } from '@/constants/cadPrompts';

const { t } = useI18n();
const router = useRouter();
const message = ref('');
const isSubmitting = ref(false);
const attachments = ref<FileInfo[]>([]);
const { toggleLeftPanel, isLeftPanelShow } = useLeftPanel();
const { hideFilePanel } = useFilePanel();
const { currentUser } = useAuth();

const prompts = cadPrompts;

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
  userMenuTimeout.value = window.setTimeout(() => {
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
    const session = await agentApi.createSession();
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
