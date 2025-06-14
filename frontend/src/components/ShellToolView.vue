<template>
  <div
    class="h-[36px] flex items-center px-3 w-full bg-[var(--background-gray-main)] border-b border-[var(--border-main)] rounded-t-[12px] shadow-[inset_0px_1px_0px_0px_#FFFFFF] dark:shadow-[inset_0px_1px_0px_0px_#FFFFFF30]">
    <div class="flex-1 flex items-center justify-center">
      <div class="max-w-[250px] truncate text-[var(--text-tertiary)] text-sm font-medium text-center">{{
        shellSessionId }}
      </div>
    </div>
  </div>
  <div class="flex-1 min-h-0 w-full overflow-y-auto">
    <div dir="ltr" data-orientation="horizontal" class="flex flex-col flex-1 min-h-0">
      <div data-state="active" data-orientation="horizontal" role="tabpanel"
        id="radix-:r5m:-content-setup" tabindex="0"
        class="py-2 focus-visible:outline-none data-[state=inactive]:hidden flex-1 font-mono text-sm leading-relaxed px-3 outline-none overflow-auto whitespace-pre-wrap break-all"
        style="animation-duration: 0s;">
        <code v-html="shell"></code>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, computed, watch, onUnmounted } from 'vue';
import { viewShellSession } from '../api/agent';
import { ToolContent } from '../types/message';
//import { showErrorToast } from '../utils/toast';

const props = defineProps<{
  sessionId: string;
  toolContent: ToolContent;
  live: boolean;
}>();

defineExpose({
  loadContent: () => {
    loadShellContent();
  }
});

const shell = ref('');
const refreshInterval = ref<number | null>(null);

// Get shellSessionId from toolContent
const shellSessionId = computed(() => {
  if (props.toolContent && props.toolContent.args.id) {
    return props.toolContent.args.id;
  }
  return '';
});

const updateShellContent = (console: any) => {
  if (!console) return;
  let newShell = '';
  for (const e of console) {
    newShell += `<span style="color: rgb(0, 187, 0);">${e.ps1}</span><span> ${e.command}</span>\n`;
    newShell += `<span>${e.output}</span>\n`;
  }
  if (newShell !== shell.value) {
    shell.value = newShell;
  }
}

// Function to load Shell session content
const loadShellContent = () => {
  if (!props.live) {
    updateShellContent(props.toolContent.content?.console);
    return;
  }
  if (!shellSessionId.value) return;

  viewShellSession(props.sessionId, shellSessionId.value).then((response) => {
    updateShellContent(response.console);
  }).catch((error) => {
    console.error('Failed to load Shell session content:', error);
  });
};

// Watch for sessionId changes to reload content
watch(shellSessionId, (newVal) => {
  if (newVal) {
    loadShellContent();
  }
});

watch(() => props.toolContent.status, () => {
  loadShellContent();
});

// Load content and set up refresh timer when component is mounted
onMounted(() => {
  loadShellContent();
  refreshInterval.value = window.setInterval(() => {
    loadShellContent();
  }, 5000);
});

// Clear timer when component is unmounted
onUnmounted(() => {
  if (refreshInterval.value !== null) {
    clearInterval(refreshInterval.value);
    refreshInterval.value = null;
  }
});
</script>
