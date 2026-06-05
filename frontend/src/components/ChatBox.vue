<template>
    <div class="pb-2 relative bg-transparent">
        <div
            class="cad-chatbox flex flex-col gap-2 rounded-[18px] transition-all relative bg-[var(--fill-input-chat)] py-2.5 max-h-[260px] shadow-[0px_8px_24px_0px_rgba(0,0,0,0.025)] border border-black/8 dark:border-[var(--border-main)]">
            <ChatBoxFiles
                ref="chatBoxFileListRef"
                :attachments="attachments"
                @update:attachments="value => emit('update:attachments', value)" />
            <div class="overflow-y-auto pl-4 pr-2">
                <textarea
                    class="flex rounded-md border-input focus-visible:outline-none focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 overflow-hidden flex-1 bg-transparent p-0 pt-[1px] border-0 focus-visible:ring-0 focus-visible:ring-offset-0 w-full placeholder:text-[var(--text-disable)] text-[15px] shadow-none resize-none min-h-[40px]"
                    :rows="rows"
                    :value="modelValue"
                    @input="$emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
                    @compositionstart="isComposing = true"
                    @compositionend="isComposing = false"
                    @keydown.enter.exact="handleEnterKeydown"
                    :placeholder="placeholder || '给 CAD大王 一个 CAD 任务...'"
                    :style="{ height: '46px' }"></textarea>
            </div>
            <footer class="flex flex-row items-center justify-between gap-2 w-full px-3">
                <div class="flex min-w-0 flex-1 gap-2 pr-2 items-center">
                    <button @click="uploadFile"
                        class="rounded-full border border-[var(--border-main)] inline-flex items-center justify-center gap-1 clickable cursor-pointer text-xs text-[var(--text-secondary)] hover:bg-[var(--fill-tsp-gray-main)] w-8 h-8 p-0 data-[popover-trigger]:bg-[var(--fill-tsp-gray-main)] shrink-0"
                        aria-expanded="false" aria-haspopup="dialog">
                        <Paperclip :size="16" />
                    </button>
                    <slot name="footer-actions"></slot>
                </div>
                <div class="flex gap-2">
                    <button v-if="!isRunning || sendEnabled || hideStopButton"
                        class="whitespace-nowrap text-sm font-medium focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 text-primary-foreground hover:bg-primary/90 p-0 w-8 h-8 rounded-full flex items-center justify-center transition-colors hover:opacity-90"
                        :class="!sendEnabled ? 'cursor-not-allowed bg-[var(--fill-tsp-white-dark)]' : 'cursor-pointer bg-[var(--Button-primary-black)]'"
                        @click="handleSubmit">
                        <SendIcon :disabled="!sendEnabled" />
                    </button>
                    <button v-else-if="!hideStopButton" @click="handleStop"
                        class="inline-flex items-center justify-center whitespace-nowrap text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring bg-[var(--Button-primary-black)] text-[var(--text-onblack)] gap-[4px] hover:opacity-90 rounded-full p-0 w-8 h-8">
                        <div class="w-[10px] h-[10px] bg-[var(--icon-onblack)] rounded-[2px]">
                        </div>
                    </button>
                </div>
            </footer>
        </div>
    </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import SendIcon from './icons/SendIcon.vue';
import ChatBoxFiles from './ChatBoxFiles.vue';
import { Paperclip } from 'lucide-vue-next';
import type { FileInfo } from '../api/file';

const isComposing = ref(false);
const chatBoxFileListRef = ref();

const props = defineProps<{
    modelValue: string;
    rows: number;
    isRunning: boolean;
    attachments: FileInfo[];
    hideStopButton?: boolean;
    allowSendFilesOnly?: boolean;
    placeholder?: string;
}>();

const sendEnabled = computed(() => {
    const hasTextInput = props.modelValue.trim() !== '';
    const displayedFiles = chatBoxFileListRef.value?.getFiles?.() ?? props.attachments ?? [];
    const hasFiles = displayedFiles.length > 0;
    const allUploaded = chatBoxFileListRef.value?.isAllUploaded ?? true;
    if (props.allowSendFilesOnly) {
        return hasTextInput || (hasFiles && allUploaded);
    }
    return hasTextInput && (!hasFiles || allUploaded);
});

const emit = defineEmits<{
    (e: 'update:modelValue', value: string): void;
    (e: 'update:attachments', value: FileInfo[]): void;
    (e: 'submit'): void;
    (e: 'stop'): void;
}>();

const handleEnterKeydown = (event: KeyboardEvent) => {
    if (isComposing.value) {
        // If in input method composition state, do nothing and allow default behavior.
        return;
    }

    // Not in input method composition state and has text input, prevent default behavior and submit.
    if (sendEnabled.value) {
        event.preventDefault();
        handleSubmit();
    }
};

const handleSubmit = () => {
    if (!sendEnabled.value) return;
    emit('submit');
};

const handleStop = () => {
    emit('stop');
};

const uploadFile = () => {
    chatBoxFileListRef.value?.uploadFile();
};

</script>

<style scoped>
.cad-chatbox::before,
.cad-chatbox::after {
    content: "";
    position: absolute;
    pointer-events: none;
    width: 30px;
    height: 30px;
    border-color: var(--cad-line);
    opacity: 0.72;
}

.cad-chatbox::before {
    left: 10px;
    top: 10px;
    border-left: 1px solid;
    border-top: 1px solid;
}

.cad-chatbox::after {
    right: 10px;
    bottom: 10px;
    border-right: 1px solid;
    border-bottom: 1px solid;
}
</style>
