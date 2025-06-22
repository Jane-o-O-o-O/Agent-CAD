<template>
      <div class="bg-[var(--background-gray-main)] overflow-hidden shadow-[0px_0px_8px_0px_rgba(0,0,0,0.02)] ltr:border-l rtl:border-r border-black/8 dark:border-[var(--border-light)] flex flex-col h-full w-full">
        <div
          class="px-4 pt-2 pb-4 gap-4 flex items-center justify-between flex-shrink-0 border-b border-[var(--border-main)] flex-col-reverse md:flex-row md:py-4">
          <div class="flex justify-between self-stretch flex-1 truncate">
            <div
              class="flex flex-row gap-1 items-center text-[var(--text-secondary)] font-medium truncate [&amp;_svg]:flex-shrink-0">
              <a href="" class="p-1 flex-shrink-0 cursor-default" target="_blank">
                <div class="relative flex items-center justify-center">
                  <FileIcon />
                </div>
              </a>
              <div class="truncate flex flex-col"><span class="truncate" :title="file.filename">{{ file.filename }}</span></div>
            </div>
          </div>
          <div class="flex items-center justify-between gap-2 w-full py-3 md:w-auto md:py-0 select-none">
            <div class="flex items-center gap-2">
              <div @click="download"
                class="flex h-7 w-7 items-center justify-center cursor-pointer hover:bg-[var(--fill-tsp-gray-main)] rounded-md"
                aria-expanded="false" aria-haspopup="dialog">
                <Download class="text-[var(--icon-secondary)] size-[18px]" />
              </div>
            </div>
            <div class="flex items-center gap-2">
              <div @click="hide"
                class="flex h-7 w-7 items-center justify-center cursor-pointer hover:bg-[var(--fill-tsp-gray-main)] rounded-md">
                <X class="size-5 text-[var(--icon-secondary)]" />
              </div>
            </div>
          </div>
        </div>
        <div class="flex flex-col items-center justify-center gap-6 flex-1 w-full min-h-0">
          <div class="flex items-center gap-1.5 rounded-[10px] bg-[var(--fill-tsp-gray-main)] px-2 py-2 w-[280px]">
            <div class="relative flex items-center justify-center">
              <FileIcon />
            </div>
            <div class="flex flex-col gap-0.5 flex-1 min-w-0">
              <div class="text-sm text-[var(--text-primary)] truncate">{{ file.filename }}</div>
              <div class="text-xs text-[var(--text-tertiary)] truncate">{{ t('File') }}</div>
            </div>
          </div>
          <div class="text-sm text-center text-[var(--text-tertiary)]">{{ t('This format cannot be previewed') }}。<br>{{ t('Please download the file to view its content') }}。
          </div>
          <button @click="download"
            class="inline-flex items-center justify-center whitespace-nowrap font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring hover:opacity-90 active:opacity-80 bg-[var(--Button-primary-black)] text-[var(--text-onblack)] h-[36px] px-[12px] rounded-[10px] gap-[6px] text-sm">
            <Download :size="16" />
            <span class="text-sm ">{{ t('Download') }}</span>
          </button>
        </div>
      </div>
</template>

<script setup lang="ts">
import { Download, X } from 'lucide-vue-next';
import type { FileInfo } from '../api/file';
import FileIcon from './icons/FileIcon.vue';
import { getFileDownloadUrl } from '../api/file';
import { useI18n } from 'vue-i18n';

const { t } = useI18n();

const props = defineProps<{
  file: FileInfo;
}>();

const emit = defineEmits<{
  (e: 'hide'): void
}>();

const hide = () => {
  emit('hide');
};

const download = () => {
  const url = getFileDownloadUrl(props.file.file_id);
  window.open(url, '_blank');
};
</script>
