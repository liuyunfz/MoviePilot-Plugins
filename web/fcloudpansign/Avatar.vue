<script setup>
import { computed, ref, watch } from "vue";
const props = defineProps({ src: String, name: String });
const failed = ref(false),
  loaded = ref(false);
watch(
  () => props.src,
  () => {
    failed.value = false;
    loaded.value = false;
  },
);
const initial = computed(
  () => Array.from((props.name || "云").trim())[0] || "云",
);
</script>
<template>
  <span
    class="cloud-avatar"
    role="img"
    :aria-label="`${name || '用户'}的云盘头像`"
  >
    <span v-if="!src || failed || !loaded" class="cloud-avatar-fallback">{{
      initial
    }}</span>
    <img
      v-if="src && !failed"
      v-show="loaded"
      :src="src"
      alt=""
      width="64"
      height="64"
      referrerpolicy="no-referrer"
      @load="loaded = true"
      @error="failed = true"
    />
  </span>
</template>
<style scoped>
.cloud-avatar {
  position: relative;
  display: grid;
  place-items: center;
  width: 100%;
  height: 100%;
  overflow: hidden;
  border-radius: 50%;
  background: rgba(var(--v-theme-primary), 0.12);
}
.cloud-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.cloud-avatar-fallback {
  font-size: 26px;
  font-weight: 700;
  color: rgb(var(--v-theme-primary));
}
</style>
