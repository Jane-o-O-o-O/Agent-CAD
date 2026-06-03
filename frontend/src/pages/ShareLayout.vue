<template>
  <div class="h-screen flex overflow-hidden cad-shell">
    <div className="flex-1 min-w-0 h-full py-0 pr-0 relative">
      <div class="flex h-full cad-surface">
        <div class="flex flex-1 min-w-0 min-h-0">
          <router-view />
          <FilePanel />
        </div>
      </div>
    </div>
  </div>
  <CustomDialog />
  <SessionFileList />
</template>

<script setup lang="ts">
import CustomDialog from '@/components/ui/CustomDialog.vue';
import SessionFileList from '@/components/SessionFileList.vue';
import FilePanel from '@/components/FilePanel.vue';
</script>
