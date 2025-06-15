<template>
  <div className="h-screen flex overflow-hidden bg-white">
    <Panel />
    <div className="flex-1 min-w-0 h-full py-0 pr-0 relative">
      <div className="flex h-full bg-[var(--background-gray-main)]">
        <router-view />
      </div>
    </div>
  </div>
  <TakeOverView />
  <ContextMenu />
  <CustomDialog />
  <Toast />
</template>

<script setup lang="ts">
import Toast from './components/Toast.vue';
import Panel from './components/Panel.vue';
import CustomDialog from './components/CustomDialog.vue';
import ContextMenu from './components/ContextMenu.vue';
import TakeOverView from './components/TakeOverView.vue';
</script>
