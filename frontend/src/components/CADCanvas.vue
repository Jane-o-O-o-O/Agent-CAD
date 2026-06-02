<template>
  <div ref="containerRef" class="h-full w-full overflow-hidden bg-[#f7f8f8]">
    <svg
      class="h-full w-full"
      :viewBox="viewBox"
      role="img"
      aria-label="Mechanical CAD preview"
      @wheel.prevent="handleWheel"
      @mousedown="handleMouseDown"
      @mousemove="handleMouseMove"
      @mouseup="stopPan"
      @mouseleave="stopPan">
      <defs>
        <pattern id="cad-grid" :width="gridSize" :height="gridSize" patternUnits="userSpaceOnUse">
          <path :d="`M ${gridSize} 0 L 0 0 0 ${gridSize}`" fill="none" stroke="#d9dedc" stroke-width="0.25" />
        </pattern>
      </defs>

      <rect :x="view.x" :y="view.y" :width="view.width" :height="view.height" fill="url(#cad-grid)" />

      <g transform="scale(1,-1)">
        <template v-for="entity in document?.entities ?? []" :key="entity.id">
          <line
            v-if="entity.type === 'line'"
            :x1="entity.start.x"
            :y1="-entity.start.y"
            :x2="entity.end.x"
            :y2="-entity.end.y"
            :stroke="layerColor(entity.layer)"
            :stroke-width="strokeWidth(entity.layer)"
            :stroke-dasharray="dashArray(entity.layer)"
            vector-effect="non-scaling-stroke" />

          <circle
            v-else-if="entity.type === 'circle'"
            :cx="entity.center.x"
            :cy="-entity.center.y"
            :r="entity.radius"
            :stroke="layerColor(entity.layer)"
            fill="none"
            :stroke-width="strokeWidth(entity.layer)"
            vector-effect="non-scaling-stroke" />

          <polyline
            v-else-if="entity.type === 'polyline'"
            :points="polylinePoints(entity)"
            :stroke="layerColor(entity.layer)"
            fill="none"
            :stroke-width="strokeWidth(entity.layer)"
            vector-effect="non-scaling-stroke" />

          <path
            v-else-if="entity.type === 'arc'"
            :d="arcPath(entity)"
            :stroke="layerColor(entity.layer)"
            fill="none"
            :stroke-width="strokeWidth(entity.layer)"
            vector-effect="non-scaling-stroke" />

          <path
            v-else-if="entity.type === 'slot'"
            :d="slotPath(entity)"
            :stroke="layerColor(entity.layer)"
            fill="none"
            :stroke-width="strokeWidth(entity.layer)"
            vector-effect="non-scaling-stroke" />
        </template>
      </g>

      <g>
        <template v-for="dimension in document?.dimensions ?? []" :key="dimension.id">
          <line
            v-if="dimension.start && dimension.end"
            :x1="dimension.start.x"
            :y1="-dimension.start.y"
            :x2="dimension.end.x"
            :y2="-dimension.end.y"
            stroke="#64748b"
            stroke-width="0.8"
            vector-effect="non-scaling-stroke" />
          <text
            :x="dimension.position.x"
            :y="-dimension.position.y"
            text-anchor="middle"
            dominant-baseline="central"
            class="fill-slate-600"
            :font-size="textSize">
            {{ dimension.text }}
          </text>
        </template>

        <template v-for="entity in notes" :key="entity.id">
          <text
            :x="entity.position.x"
            :y="-entity.position.y"
            text-anchor="start"
            dominant-baseline="central"
            class="fill-slate-700"
            :font-size="entity.height">
            {{ entity.text }}
          </text>
        </template>
      </g>
    </svg>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { CADArc, CADPolyline, CADSlot, MechanicalCADDocument } from '@/api/cad';

const props = defineProps<{
  document?: MechanicalCADDocument | null;
}>();

const containerRef = ref<HTMLElement | null>(null);
const view = ref({ x: -20, y: -120, width: 180, height: 140 });
const isPanning = ref(false);
const lastPointer = ref({ x: 0, y: 0 });
const gridSize = 10;
const textSize = 4;

const viewBox = computed(() => `${view.value.x} ${view.value.y} ${view.value.width} ${view.value.height}`);
const notes = computed(() => (props.document?.entities ?? []).filter((entity: any) => entity.type === 'note') as any[]);

watch(
  () => props.document?.id,
  () => fitToDocument(),
);

watch(
  () => props.document?.version,
  () => fitToDocument(),
);

function fitToDocument() {
  const bounds = getBounds();
  if (!bounds) return;
  const padding = Math.max((bounds.maxX - bounds.minX) * 0.16, 20);
  view.value = {
    x: bounds.minX - padding,
    y: -bounds.maxY - padding,
    width: Math.max(bounds.maxX - bounds.minX + padding * 2, 80),
    height: Math.max(bounds.maxY - bounds.minY + padding * 2, 80),
  };
}

function getBounds() {
  const points: { x: number; y: number }[] = [];
  for (const entity of props.document?.entities ?? []) {
    if (entity.type === 'line') points.push(entity.start, entity.end);
    if (entity.type === 'circle' || entity.type === 'arc') {
      points.push(
        { x: entity.center.x - entity.radius, y: entity.center.y - entity.radius },
        { x: entity.center.x + entity.radius, y: entity.center.y + entity.radius },
      );
    }
    if (entity.type === 'polyline') points.push(...entity.points);
    if (entity.type === 'slot') {
      points.push(
        { x: entity.center.x - entity.length / 2, y: entity.center.y - entity.width / 2 },
        { x: entity.center.x + entity.length / 2, y: entity.center.y + entity.width / 2 },
      );
    }
    if (entity.type === 'note') points.push(entity.position);
  }
  if (points.length === 0) return null;
  return {
    minX: Math.min(...points.map(point => point.x)),
    maxX: Math.max(...points.map(point => point.x)),
    minY: Math.min(...points.map(point => point.y)),
    maxY: Math.max(...points.map(point => point.y)),
  };
}

function handleWheel(event: WheelEvent) {
  const scale = event.deltaY > 0 ? 1.12 : 0.88;
  const nextWidth = Math.min(Math.max(view.value.width * scale, 20), 2000);
  const nextHeight = Math.min(Math.max(view.value.height * scale, 20), 2000);
  view.value = {
    x: view.value.x + (view.value.width - nextWidth) / 2,
    y: view.value.y + (view.value.height - nextHeight) / 2,
    width: nextWidth,
    height: nextHeight,
  };
}

function handleMouseDown(event: MouseEvent) {
  isPanning.value = true;
  lastPointer.value = { x: event.clientX, y: event.clientY };
}

function handleMouseMove(event: MouseEvent) {
  if (!isPanning.value || !containerRef.value) return;
  const rect = containerRef.value.getBoundingClientRect();
  const dx = ((event.clientX - lastPointer.value.x) / rect.width) * view.value.width;
  const dy = ((event.clientY - lastPointer.value.y) / rect.height) * view.value.height;
  view.value = { ...view.value, x: view.value.x - dx, y: view.value.y - dy };
  lastPointer.value = { x: event.clientX, y: event.clientY };
}

function stopPan() {
  isPanning.value = false;
}

function polylinePoints(entity: CADPolyline): string {
  const points = entity.closed ? [...entity.points, entity.points[0]] : entity.points;
  return points.map(point => `${point.x},${-point.y}`).join(' ');
}

function arcPath(entity: CADArc): string {
  const start = polar(entity.center.x, -entity.center.y, entity.radius, -entity.start_angle);
  const end = polar(entity.center.x, -entity.center.y, entity.radius, -entity.end_angle);
  const sweep = Math.abs(entity.end_angle - entity.start_angle) > 180 ? 1 : 0;
  return `M ${start.x} ${start.y} A ${entity.radius} ${entity.radius} 0 ${sweep} 0 ${end.x} ${end.y}`;
}

function slotPath(entity: CADSlot): string {
  const halfLength = entity.length / 2;
  const halfWidth = entity.width / 2;
  const x = entity.center.x;
  const y = -entity.center.y;
  return `M ${x - halfLength} ${y - halfWidth} L ${x + halfLength} ${y - halfWidth} L ${x + halfLength} ${y + halfWidth} L ${x - halfLength} ${y + halfWidth} Z`;
}

function polar(cx: number, cy: number, radius: number, angle: number) {
  const radians = (angle * Math.PI) / 180;
  return { x: cx + Math.cos(radians) * radius, y: cy + Math.sin(radians) * radius };
}

function layerColor(layer: string) {
  if (layer.includes('HOLE')) return '#2563eb';
  if (layer.includes('CENTER')) return '#dc2626';
  if (layer.includes('DIM')) return '#64748b';
  return '#0f172a';
}

function strokeWidth(layer: string) {
  return layer.includes('CENTER') ? 0.7 : 1.4;
}

function dashArray(layer: string) {
  return layer.includes('CENTER') ? '8 4 2 4' : undefined;
}
</script>

