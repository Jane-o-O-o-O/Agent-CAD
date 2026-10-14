<template>
  <div
    class="cad-logo-text"
    :style="logoStyle"
    aria-label="CAD大王"
  >
    CAD大王
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps({
  width: {
    type: [String, Number],
    default: 96,
  },
  height: {
    type: [String, Number],
    default: 38,
  },
})

const cssSize = (value: string | number) =>
  typeof value === 'number' ? `${value}px` : value

const logoStyle = computed(() => {
  const width = props.width
  const height = props.height
  const style: Record<string, string> = {
    width: cssSize(width),
    height: cssSize(height),
  }
  if (typeof width === 'number' && typeof height === 'number') {
    style.fontSize = `${Math.max(12, Math.min(height * 0.58, width / 4.7))}px`
  }
  return style
})
</script>

<style scoped>
.cad-logo-text {
  display: inline-flex;
  align-items: center;
  justify-content: flex-start;
  color: var(--logo-color);
  font-weight: 800;
  line-height: 1;
  white-space: nowrap;
}
</style>
