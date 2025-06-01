import { Ref } from 'vue'

export interface PanelState {
  isPanelShow: Ref<boolean>
  togglePanel: () => void
  setPanel: (visible: boolean) => void
  showPanel: () => void
  hidePanel: () => void
} 