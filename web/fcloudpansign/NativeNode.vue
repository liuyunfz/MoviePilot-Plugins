<script>
import { h, resolveComponent } from "vue";
import FCloudpanAvatar from "./Avatar.vue";
export default {
  name: "NativeNode",
  props: ["config", "model", "busy", "dirty"],
  emits: ["action", "changed", "authorize"],
  setup(props, { emit }) {
    function render(config) {
      const raw = config.props || {},
        attrs = { ...raw };
      if (raw.model) {
        delete attrs.model;
        attrs.modelValue = props.model[raw.model];
        attrs["onUpdate:modelValue"] = (value) => {
          props.model[raw.model] = value;
          emit("changed");
        };
      }
      const action = config.events?.click?.params;
      if (action) {
        attrs.onClick = () => emit("action", action);
        attrs.loading = props.busy;
        attrs.disabled =
          attrs.disabled ||
          props.busy ||
          (props.dirty && action.action === "connect");
      }
      if (attrs.href && config.text === "前往 F-Cloudpan 授权") {
        const url = attrs.href;
        delete attrs.href;
        delete attrs.target;
        attrs.onClick = () => emit("authorize", url);
        return h("div", {}, [
          h(resolveComponent("VBtn"), attrs, { default: () => "打开授权窗口" }),
          h(
            "a",
            {
              href: url,
              target: "_blank",
              rel: "noopener noreferrer",
              style: "display:block;margin-top:12px",
            },
            "弹窗无法打开？在新标签页授权",
          ),
        ]);
      }
      const component =
        config.component === "FCloudpanAvatar"
          ? FCloudpanAvatar
          : /^[a-z]/.test(config.component)
            ? config.component
            : resolveComponent(config.component);
      const children = () => config.text ?? (config.content || []).map(render);
      return h(
        component,
        attrs,
        typeof component === "string" ? children() : { default: children },
      );
    }
    return () => render(props.config);
  },
};
</script>
