<template>
  <div class="cad-backdrop" aria-hidden="true">
    <div class="cad-grid"></div>
    <div class="cad-origin"></div>
    <div class="cad-axis cad-axis-x"></div>
    <div class="cad-axis cad-axis-y"></div>
    <div class="cad-ruler cad-ruler-top"></div>
    <div class="cad-ruler cad-ruler-left"></div>
    <div class="cad-wire cad-wire-a"></div>
    <div class="cad-wire cad-wire-b"></div>
    <div class="cad-wire cad-wire-c"></div>
    <div class="cad-crosshair cad-crosshair-a"></div>
    <div class="cad-crosshair cad-crosshair-b"></div>
    <div class="cad-dimension cad-dimension-top" data-label="W 768.00"></div>
    <div class="cad-dimension cad-dimension-side" data-label="H 420.00"></div>
    <div class="cad-isometric">
      <span></span>
      <span></span>
      <span></span>
    </div>
    <div class="cad-title-block">
      <div>AI MANUS CAD</div>
      <div>MODEL SPACE</div>
      <div>SCALE 1:1</div>
    </div>
    <div class="cad-grid-label cad-grid-label-a">X: 000.00</div>
    <div class="cad-grid-label cad-grid-label-b">Y: 000.00</div>
  </div>
  <router-view />
  <Toast />
</template>

<script setup lang="ts">
import Toast from './components/ui/Toast.vue';
</script>
