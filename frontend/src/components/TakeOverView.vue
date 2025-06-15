<template>
    <div v-if="shouldShow" class="fixed bg-[var(--background-gray-main)] z-50 transition-all w-full h-full inset-0">
        <div class="w-full h-full">
            <div ref="vncContainer"
                style="display: flex; width: 100%; height: 100%; overflow: auto; background: rgb(40, 40, 40);"></div>
        </div>
        <div class="absolute bottom-4 left-1/2 -translate-x-1/2">
            <button @click="exitTakeOver"
                class="inline-flex items-center justify-center whitespace-nowrap font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring hover:opacity-90 active:opacity-80 bg-[var(--Button-primary-black)] text-[var(--text-onblack)] h-[36px] px-[12px] gap-[6px] text-sm rounded-full border-2 border-[var(--border-dark)] shadow-[0px_8px_32px_0px_rgba(0,0,0,0.32)]">
                <span class="text-sm font-medium text-[var(--text-onblack)]">{{ t('Exit Takeover') }}</span>
            </button>
        </div>
    </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue';
import { useRoute } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { getVNCUrl } from '../api/agent';
// @ts-ignore
import RFB from '@novnc/novnc/lib/rfb';

const route = useRoute();
const { t } = useI18n();

// Takeover state
const takeOverActive = ref(false);
const currentSessionId = ref('');

// VNC related refs
const vncContainer = ref<HTMLDivElement | null>(null);
let rfb: RFB | null = null;

// Listen to takeover events
const handleTakeOverEvent = (event: Event) => {
    const customEvent = event as CustomEvent;
    takeOverActive.value = customEvent.detail.active;
    currentSessionId.value = customEvent.detail.sessionId;
};

// Initialize VNC connection
const initVNCConnection = async () => {
    await nextTick(); // Wait for DOM to be updated
    
    if (!vncContainer.value) {
        console.warn('VNC container is not available');
        return;
    }

    // Get sessionId from component state or route parameters
    const sessionIdToUse = currentSessionId.value || route.params.sessionId as string;
    if (!sessionIdToUse) {
        console.warn('No session ID available for VNC connection');
        return;
    }
    
    const wsUrl = getVNCUrl(sessionIdToUse);
    console.log('sessionIdToUse', sessionIdToUse);

    // Disconnect existing connection if any
    if (rfb) {
        rfb.disconnect();
        rfb = null;
    }

    // Create NoVNC connection
    rfb = new RFB(vncContainer.value, wsUrl, {
        credentials: { password: '' },
        shared: true,
        repeaterID: '',
        wsProtocols: ['binary'],
        // Scaling options
        scaleViewport: true,  // Automatically scale to fit container
        //resizeSession: true   // Request server to adjust resolution
    });

    rfb.scaleViewport = true;
    //rfb.resizeSession = true;

    rfb.addEventListener('connect', () => {
        console.log('VNC connection successful');
    });

    rfb.addEventListener('disconnect', (e: any) => {
        console.log('VNC connection disconnected', e);
    });

    rfb.addEventListener('credentialsrequired', () => {
        console.log('VNC credentials required');
    });
};

// Calculate whether to show takeover view
const shouldShow = computed(() => {
    // Check component state first (from takeover event)
    if (takeOverActive.value && currentSessionId.value) {
        return true;
    }
    
    // Also check route parameters (for direct URL access or page refresh)
    const { params: { sessionId }, query: { vnc } } = route;
    // Only show if both sessionId exists in route AND vnc=1 in query
    return !!sessionId && vnc === '1';
});

// Add event listener when component is mounted
onMounted(() => {
    window.addEventListener('takeover', handleTakeOverEvent as EventListener);
});

// Watch for shouldShow changes to initialize VNC connection
watch(shouldShow, async (newValue) => {
    if (newValue) {
        await initVNCConnection();
    } else if (rfb) {
        rfb.disconnect();
        rfb = null;
    }
}, { immediate: true });

// Remove event listener when component is unmounted
onBeforeUnmount(() => {
    if (rfb) {
        rfb.disconnect();
        rfb = null;
    }
    window.removeEventListener('takeover', handleTakeOverEvent as EventListener);
});

// Get session ID
const sessionId = computed(() => {
    return currentSessionId.value || route.params.sessionId as string || '';
});

// Exit takeover functionality
const exitTakeOver = () => {
    // Update local state
    takeOverActive.value = false;
    currentSessionId.value = '';
};

// Expose sessionId for parent component to use
defineExpose({
    sessionId
});
</script>