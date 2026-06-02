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
          <div class="cad-command-deck cad-quick-command relative overflow-hidden rounded-[20px] border border-[var(--border-light)] px-4 py-4 sm:px-5 sm:py-5">
            <div class="cad-scanline" aria-hidden="true"></div>
            <div class="relative flex flex-col gap-3">
              <div class="grid grid-cols-1 gap-3 md:grid-cols-3">
                <button
                  v-for="prompt in prompts"
                  :key="prompt.title"
                  class="cad-prompt-tile cad-shortcut-tile text-left"
                  type="button"
                  @click="usePrompt(prompt.message)">
                  <component :is="prompt.icon" class="size-5 text-[var(--cad-blue)]" />
                  <span class="text-[15px] font-semibold text-[var(--text-primary)]">{{ prompt.title }}</span>
                  <span class="text-[12px] leading-[18px] text-[var(--text-tertiary)]">{{ prompt.caption }}</span>
                </button>
              </div>

              <div class="cad-input-dock flex w-full flex-col rounded-[18px]">
                <ChatBox
                  :rows="3"
                  v-model="message"
                  v-model:attachments="attachments"
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
import { showErrorToast } from '../utils/toast';
import * as agentApi from '../api/agent';
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
    title: '瀹屾垚 CAD 鐢诲浘',
    caption: '鐞嗚В瑕佹眰锛岀敓鎴愬浘绾搞€佽剼鏈垨妯″瀷鏂囦欢',
    message: '甯垜瀹屾垚杩欎釜 CAD 鐢诲浘浠诲姟銆傝鍏堢悊瑙ｅ浘绾歌姹傦紝鍐嶇粰鍑哄彲鎵ц鐨勭粯鍥炬楠わ紝骞跺敖閲忕敓鎴愬彲鐩存帴鎵撳紑鎴栬繍琛岀殑 CAD 鏂囦欢/鑴氭湰銆?,
    icon: DraftingCompass,
  },
  {
    title: '鎷嗚В寤烘ā鏂规',
    caption: '浠诲姟鎷嗗垎銆佺粨鏋勮鍒掋€佸缓妯℃楠?,
    message: '甯垜鎶婅繖涓?CAD 浠诲姟鎷嗘垚鍙墽琛屾柟妗堬紝骞剁粰鍑哄缓妯℃楠ゃ€?,
    icon: Workflow,
  },
  {
    title: '妫€鏌ュ浘绾搁棶棰?,
    caption: '灏哄銆佺粨鏋勩€佹爣娉ㄣ€佸浘灞備笌鍙埗閫犳€?,
    message: '甯垜妫€鏌ヨ繖涓?CAD 鍥剧焊鎴栧缓妯＄粨鏋滐紝鎸囧嚭灏哄銆佺粨鏋勩€佹爣娉ㄣ€佸浘灞傚拰鍙埗閫犳€ф柟闈㈢殑闂锛屽苟缁欏嚭鍏蜂綋淇敼寤鸿銆?,
    icon: TerminalSquare,
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
