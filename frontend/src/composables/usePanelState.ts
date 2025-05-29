import { ref } from 'vue'
import type { PanelState } from '../types/panel'

// Global panel state management
const isPanelShow = ref(false)

export function usePanelState(): PanelState {
  // Toggle panel visibility
  const togglePanel = () => {
    isPanelShow.value = !isPanelShow.value
  }

  // Set panel visibility
  const setPanel = (visible: boolean) => {
    isPanelShow.value = visible
  }

  // Show panel
  const showPanel = () => {
    isPanelShow.value = true
  }

  // Hide panel
  const hidePanel = () => {
    isPanelShow.value = false
  }

  return {
    isPanelShow,
    togglePanel,
    setPanel,
    showPanel,
    hidePanel
  }
} 