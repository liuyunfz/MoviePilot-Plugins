<script setup>
import Dashboard from "./Dashboard.vue";
defineProps(["api", "initialConfig"]);
const emit = defineEmits(["close", "switch"]);
</script>
<template>
  <Dashboard
    :api="api"
    mode="config"
    :initial-config="initialConfig"
    @close="emit('close')"
    @switch="emit('switch')"
  />
</template>
