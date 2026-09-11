<script setup>
import { ref, reactive, onMounted, onBeforeUnmount } from "vue";
import NativeNode from "./NativeNode.vue";
import { createPoller } from "./poller.mjs";
import { openAuthorizationWindow } from "./popup.mjs";
const props = defineProps({
  api: { type: Object, required: true },
  initialConfig: Object,
  mode: { type: String, default: "data" },
});
const emit = defineEmits(["close", "switch"]);
const popupError = ref("");
let authPopup = null;
function closePopup() {
  try {
    if (authPopup && !authPopup.closed) authPopup.close();
  } catch {}
  authPopup = null;
}
function openAuth(url) {
  try {
    closePopup();
    authPopup = openAuthorizationWindow(window, url, view.value.origin);
    popupError.value = authPopup
      ? ""
      : "授权窗口被浏览器拦截，请允许本站弹窗，或使用下方新标签页链接。";
  } catch {
    popupError.value = "授权地址无效，请重新连接。";
  }
}
const icon = __FCLOUDPAN_ICON__;
const view = ref(null),
  draft = reactive({}),
  dirty = ref(false),
  busy = ref(false),
  message = ref(""),
  error = ref("");
const readController = new AbortController();
let alive = true;
const poller = createPoller({
  read: () =>
    props.api.get("plugin/FCloudpanSign/view", {
      signal: readController.signal,
    }),
  accept: (value) => {
    if (view.value?.pending && !value.pending) {
      closePopup();
      popupError.value = "";
    }
    view.value = value;
    error.value = "";
    if (!dirty.value) Object.assign(draft, value.config);
  },
  onError: () => {
    error.value =
      "暂时无法读取 MoviePilot 状态，请检查 MP 连接；恢复后会自动更新。";
  },
  visible: () => document.visibilityState !== "hidden",
});
async function save() {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    const result = await props.api.put("plugin/FCloudpanSign", {
      ...draft,
      prepare_auth: false,
      cancel_auth: false,
      revoke_auth: false,
    });
    if (!result?.success) throw Error("save");
    dirty.value = false;
    message.value = "设置已保存";
    await poller.refresh();
  } catch {
    if (alive)
      error.value = "保存未确认，请检查 MoviePilot 连接后重新读取状态。";
  } finally {
    if (alive) busy.value = false;
  }
}
async function act(params) {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  message.value = "";
  try {
    const result = await props.api.post("plugin/FCloudpanSign/action", params);
    if (alive) {
      await poller.refresh();
    }
  } catch {
    if (alive) {
      error.value = "操作结果暂未确认，正在重新读取状态，请勿重复点击。";
      await poller.refresh();
    }
  } finally {
    if (alive) busy.value = false;
  }
}
function onVisibility() {
  if (document.visibilityState === "hidden") poller.pause();
  else poller.refresh();
}
onMounted(() => {
  poller.refresh();
  window.addEventListener("focus", poller.refresh);
  document.addEventListener("visibilitychange", onVisibility);
});
onBeforeUnmount(() => {
  alive = false;
  closePopup();
  poller.stop();
  readController.abort();
  window.removeEventListener("focus", poller.refresh);
  document.removeEventListener("visibilitychange", onVisibility);
});
</script>
<template>
  <VCard>
    <VCardTitle class="d-flex align-center pa-4"
      >F-Cloudpan {{ mode === "config" ? "设置" : "签到数据" }}<VSpacer /><VBtn
        variant="text"
        @click="emit('switch')"
        >{{ mode === "config" ? "查看数据" : "设置" }}</VBtn
      ><VBtn variant="text" aria-label="关闭" @click="emit('close')"
        >关闭</VBtn
      ></VCardTitle
    >
    <VDivider />
    <VCardText class="pa-4 fcloud-content">
      <VCard
        color="primary"
        variant="tonal"
        rounded="xl"
        class="mb-4 text-center pa-5"
      >
        <img
          :src="icon"
          alt="F-Cloudpan"
          width="64"
          height="64"
          class="fcloud-logo"
        />
        <div class="text-h5 font-weight-bold">F-Cloudpan</div>
        <div class="text-body-2 mt-2">每日签到 · 授权连接 · 积分随时掌握</div>
      </VCard>
      <VAlert v-if="error" type="warning" variant="tonal" class="mb-4">{{
        error
      }}</VAlert>
      <VAlert
        v-if="mode === 'config' && popupError"
        type="warning"
        variant="tonal"
        class="mb-4"
        >{{ popupError }}</VAlert
      >
      <VAlert v-if="message" type="info" variant="tonal" class="mb-4">{{
        message
      }}</VAlert>
      <VProgressLinear v-if="!view || busy" indeterminate class="mb-4" />
      <template v-if="view">
        <VAlert
          v-if="mode === 'config' && view.pending"
          type="info"
          variant="tonal"
          class="mb-4"
          >正在等待云盘确认，完成后本页会自动更新。切回 MoviePilot
          时也会立即检查，无需刷新。</VAlert
        >
        <VAlert
          v-if="mode === 'config' && dirty"
          type="warning"
          variant="tonal"
          class="mb-4"
          >有未保存的设置，请先点击下方“保存设置”再连接账号。</VAlert
        >
        <NativeNode
          v-for="(item, index) in mode === 'config'
            ? view.connection
            : view.page"
          :key="'page' + index"
          :config="item"
          :model="draft"
          :busy="busy"
          :dirty="dirty"
          @action="act"
          @authorize="openAuth"
        />
        <VAlert
          v-if="mode === 'config' && !view.authorized"
          type="info"
          variant="tonal"
          class="mb-4"
          >先连接账号；授权成功后将自动显示签到设置。代理和超时可以在连接前调整。</VAlert
        >
        <fieldset
          v-if="mode === 'config'"
          :disabled="busy"
          class="fcloud-fields"
        >
          <NativeNode
            v-for="(item, index) in view.form"
            :key="'form' + index"
            :config="item"
            :model="draft"
            :busy="busy"
            :dirty="dirty"
            @changed="dirty = true"
          />
        </fieldset>
        <VBtn
          v-if="mode === 'config'"
          color="primary"
          :loading="busy"
          :disabled="busy || !dirty"
          @click="save"
          >保存设置</VBtn
        >
      </template>
    </VCardText>
  </VCard>
</template>
<style scoped>
.fcloud-content {
  max-height: 80vh;
  overflow-y: auto;
}
.fcloud-logo {
  display: block;
  object-fit: contain;
  margin: 0 auto 12px;
  border-radius: 16px;
}
.fcloud-fields {
  border: 0;
  padding: 0;
  margin: 0;
  min-width: 0;
}
</style>
