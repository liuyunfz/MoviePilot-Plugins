import { test } from "node:test";
import assert from "node:assert/strict";
import { createPoller } from "./poller.mjs";
function fixture(read) {
  let accepted = [],
    errors = [],
    timers = new Map(),
    next = 0,
    visible = true;
  const p = createPoller({
    read,
    accept: (v) => accepted.push(v),
    onError: (e) => errors.push(e),
    visible: () => visible,
    setTimer: (f, d) => {
      timers.set(++next, { f, d });
      return next;
    },
    clearTimer: (id) => timers.delete(id),
  });
  return {
    p,
    accepted,
    errors,
    timers,
    hide() {
      visible = false;
      p.pause();
    },
    show() {
      visible = true;
    },
  };
}
test("waiting polls locally and stops after approval", async () => {
  let pending = true,
    calls = 0;
  const f = fixture(async () => {
    calls++;
    return { pending };
  });
  await f.p.refresh();
  assert.equal(f.timers.size, 1);
  assert.equal([...f.timers.values()][0].d, 3000);
  pending = false;
  await [...f.timers.values()][0].f();
  assert.equal(calls, 2);
  assert.equal(f.timers.size, 0);
  assert.equal(f.accepted.at(-1).pending, false);
});
test("focus during an in-flight read queues one update without overlap", async () => {
  let resolve,
    calls = 0;
  const f = fixture(() => {
    calls++;
    return new Promise((r) => (resolve = r));
  });
  const request = f.p.refresh();
  await f.p.refresh();
  await f.p.refresh();
  assert.equal(calls, 1);
  resolve({ pending: true });
  await request;
  assert.equal(f.timers.size, 1);
  assert.equal([...f.timers.values()][0].d, 0);
});
test("closing drops in-flight results and all timers", async () => {
  let resolve;
  const f = fixture(() => new Promise((r) => (resolve = r)));
  const request = f.p.refresh();
  f.p.stop();
  resolve({ pending: true });
  await request;
  assert.equal(f.accepted.length, 0);
  assert.equal(f.timers.size, 0);
});
test("hidden tab pauses requests and focus immediately rechecks", async () => {
  let calls = 0;
  const f = fixture(async () => {
    calls++;
    return { pending: true };
  });
  await f.p.refresh();
  f.hide();
  await f.p.refresh();
  assert.equal(calls, 1);
  assert.equal(f.timers.size, 0);
  f.show();
  await f.p.refresh();
  assert.equal(calls, 2);
});
test("local read failures back off and recover", async () => {
  let failing = true;
  const f = fixture(async () => {
    if (failing) throw Error("offline");
    return { pending: false };
  });
  await f.p.refresh();
  assert.equal(f.errors.length, 1);
  assert.equal([...f.timers.values()][0].d, 6000);
  failing = false;
  await [...f.timers.values()][0].f();
  assert.equal(f.timers.size, 0);
  assert.equal(f.accepted.length, 1);
});
