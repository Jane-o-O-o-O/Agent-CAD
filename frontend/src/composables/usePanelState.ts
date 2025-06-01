import { ref, watch } from 'vue'
import type { PanelState } from '../types/panel'

// Local storage key for panel state
const PANEL_STATE_KEY = 'manus-panel-state'

// Read initial state from localStorage
const getInitialPanelState = (): boolean => {
  try {
    const saved = localStorage.getItem(PANEL_STATE_KEY)
    return saved ? JSON.parse(saved) : false
  } catch (error) {
    console.error('Failed to read panel state from localStorage:', error)
    return false
  }
}

// Global panel state management
const isPanelShow = ref(getInitialPanelState())

// Save panel state to localStorage
const savePanelState = (state: boolean) => {
  try {
    localStorage.setItem(PANEL_STATE_KEY, JSON.stringify(state))
  } catch (error) {
    console.error('Failed to save panel state to localStorage:', error)
  }
}

// Watch for panel state changes and save to localStorage
watch(isPanelShow, (newValue) => {
  savePanelState(newValue)
}, { immediate: false })

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