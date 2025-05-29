<template>
  <div
    :class="isPanelShow ?
      'h-full flex flex-col' :
      'h-full flex flex-col fixed top-0 start-0 bottom-0 z-[1]'"
    :style="isPanelShow ?
      'width: 300px; transition: width 0.28s cubic-bezier(0.4, 0, 0.2, 1);' :
      'width: 24px; transition: width 0.36s cubic-bezier(0.4, 0, 0.2, 1);'">
    <div :class="isHomepage ?
      'absolute start-0 top-0 w-[100vw] p-4 bg-[var(--background-gray-main)] pointer-events-none flex' :
      'absolute start-4 top-3 md:top-4 pointer-events-none flex'">
      <div class="flex items-center gap-2 pointer-events-auto">
        <div class="relative flex">
          <div class="flex h-7 w-7 items-center justify-center cursor-pointer hover:bg-[var(--fill-tsp-gray-main)] rounded-md" 
            @click="togglePanel">
            <PanelRight class="h-5 w-5 text-[var(--icon-secondary)]"/>
          </div>
        </div>
        <div v-if="isHomepage" class="flex">
          <Bot :size="30"/>
          <ManusLogoTextIcon />
        </div>
      </div>
      <div>
      </div>
    </div>
    <div :class="isPanelShow ?
      'flex flex-col overflow-hidden bg-[var(--background-nav)] h-full opacity-100 translate-x-0' :
      'flex flex-col overflow-hidden bg-[var(--background-nav)] fixed top-1 start-1 bottom-1 z-[1] border-1 dark:border-[1px] border-[var(--border-main)] dark:border-[var(--border-light)] rounded-xl shadow-[0px_8px_32px_0px_rgba(0,0,0,0.16),0px_0px_0px_1px_rgba(0,0,0,0.06)] opacity-0 pointer-events-none -translate-x-10'"
      :style="(isPanelShow ? 'width: 300px;' : 'width: 0px;') + ' transition: opacity 0.2s, transform 0.2s, width 0.2s;'">
      <div class="flex">
        <div class="flex items-center px-3 py-3 flex-row h-[52px] gap-1 justify-end w-full">
          <div class="flex justify-between w-full px-1 pt-2">
            <div class="relative flex">
              <div class="flex h-7 w-7 items-center justify-center cursor-pointer hover:bg-[var(--fill-tsp-gray-main)] rounded-md" @click="togglePanel">
                <PanelRight class="h-5 w-5 text-[var(--icon-secondary)]"/>
              </div>
            </div>
          </div>
        </div>
      </div>
      <div class="px-3 mb-1 flex justify-center flex-shrink-0">
        <button class="flex min-w-[36px] w-full items-center justify-center gap-1.5 rounded-lg h-[32px] bg-[var(--Button-primary-white)] hover:bg-white/20 dark:hover:bg-black/60 cursor-pointer shadow-[0px_0.5px_3px_0px_var(--shadow-S)]">
          <Plus class="h-4 w-4 text-[var(--icon-primary)]"/>
          <span class="text-sm font-medium text-[var(--text-primary)] whitespace-nowrap truncate">
            新建任务
          </span>
          <div class="flex items-center gap-0.5">
            <span class="flex text-[var(--text-tertiary)] justify-center items-center min-w-5 h-5 px-1 rounded-[4px] bg-[var(--fill-tsp-white-light)] border border-[var(--border-light)]">
              <Command :size="14"/>
            </span>
            <span class="flex justify-center items-center w-5 h-5 px-1 rounded-[4px] bg-[var(--fill-tsp-white-light)] border border-[var(--border-light)] text-sm font-normal text-[var(--text-tertiary)] ">
              K
            </span>
          </div>
        </button>
      </div>
      <div class="flex flex-col flex-1 min-h-0 overflow-auto pt-2 pb-5 overflow-x-hidden">
        <div class="px-2">
          <div class="group flex h-14 cursor-pointer items-center gap-2 rounded-[10px] px-2 transition-colors hover:bg-[var(--fill-tsp-gray-main)]">
            <div class="relative">
              <div class="h-8 w-8 rounded-full flex items-center justify-center relative bg-[var(--fill-tsp-white-dark)]">
                <div class="relative h-4 w-4 object-cover brightness-0 opacity-75 dark:opacity-100 dark:brightness-100">
                  <img alt="Hello" class="w-full h-full object-cover" src="/chatting.svg">
                </div>
              </div>
            </div>
            <div class="min-w-20 flex-1 transition-opacity opacity-100">
              <div class="flex items-center gap-1 overflow-x-hidden">
                <span class="truncate text-sm font-medium text-[var(--text-primary)] flex-1 min-w-0"
                title="Hello">
                  <span class="">
                    Hello
                  </span>
                </span>
                <span class="text-[var(--text-tertiary)] text-xs whitespace-nowrap">
                  周五
                </span>
              </div>
              <div class="flex items-center gap-2 h-[18px] relative">
                <span class="min-w-0 flex-1 truncate text-xs text-[var(--text-tertiary)]"
                title="您好！我是Manus，一个AI助手。我能帮您完成各种任务，包括信息收集、数据分析、文档撰写、网站创建、图像生成等。请告诉我您需要什么帮助，我会尽力为您服务。">
                  您好！我是Manus，一个AI助手。我能帮您完成各种任务，包括信息收集、数据分析、文档撰写、网站创建、图像生成等。请告诉我您需要什么帮助，我会尽力为您服务。
                </span>
                <div class="w-[22px] h-[22px] flex rounded-[6px] items-center justify-center pointer invisible cursor-pointer bg-[var(--background-menu-white)] border border-[var(--border-main)] shadow-sm group-hover:visible touch-device:visible"
                aria-expanded="false" aria-haspopup="dialog">
                  <Ellipsis :size="16"/>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { PanelRight, Plus, Command, Ellipsis, Bot } from 'lucide-vue-next';
import ManusLogoTextIcon from './icons/ManusLogoTextIcon.vue';
import { usePanelState } from '../composables/usePanelState';
import { computed } from 'vue';
import { useRoute } from 'vue-router';

const { isPanelShow, togglePanel } = usePanelState()
const route = useRoute()

// Check if current page is homepage
const isHomepage = computed(() => {
  console.log(route.path)
  return route.path === '/'
})
</script>