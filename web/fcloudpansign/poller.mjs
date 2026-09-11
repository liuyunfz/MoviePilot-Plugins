// No overlap; only one timer. Cloud token polling belongs to the Python scheduler.
export function createPoller({
  read,
  accept,
  onError,
  visible = () => true,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
}) {
  let timer,
    stopped = false,
    active = false,
    again = false,
    pending = false,
    failures = 0;
  function clear() {
    if (timer !== undefined) clearTimer(timer);
    timer = undefined;
  }
  async function refresh() {
    clear();
    if (stopped || !visible()) return;
    if (active) {
      again = true;
      return;
    }
    active = true;
    try {
      const state = await read();
      if (!stopped) {
        pending = state.watching ?? state.pending;
        failures = 0;
        accept(state);
      }
    } catch (error) {
      if (!stopped) {
        failures++;
        onError(error);
      }
    } finally {
      active = false;
      if (!stopped && visible()) {
        if (again) {
          again = false;
          timer = setTimer(refresh, 0);
        } else if (pending || failures)
          timer = setTimer(
            refresh,
            failures
              ? Math.min(30000, 3000 * 2 ** Math.min(failures, 4))
              : 3000,
          );
      }
    }
  }
  return {
    refresh,
    stop() {
      stopped = true;
      again = false;
      clear();
    },
    pause() {
      clear();
    },
  };
}
