<template>
    <div class="relative overflow-auto flex-1 min-h-0 p-5">
        <div class="relative w-full max-w-[768px] mx-auto" style="min-height: calc(-200px + 100vh);">
            <div spellcheck="false" data-slate-editor="true" data-slate-node="value" contenteditable="false" zindex="-1"
                style="position: relative; white-space: pre-wrap; overflow-wrap: break-word;">
                <pre class="text-sm">{{ content }}</pre>
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue';
import type { FileInfo } from '../../api/file';
import { downloadFile } from '../../api/file';

const content = ref('');

const props = defineProps<{
    file: FileInfo;
}>();

watch(props.file, async (_) => {
    try {
        const blob = await downloadFile(props.file.file_id);
        const text = await blob.text();
        content.value = text;
    } catch (error) {
        console.error('Failed to load file content:', error);
        content.value = 'Failed to load file content';
    }
}, { immediate: true });
</script>