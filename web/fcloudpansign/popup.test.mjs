import { test } from "node:test";
import assert from "node:assert/strict";
import { openAuthorizationWindow } from "./popup.mjs";
test("opens the MP-sized popup, detaches opener and navigates to official origin", () => {
  let args, destination;
  const popup = {
    opener: {},
    location: { replace: (url) => (destination = url) },
    focus() {},
  };
  const host = {
    screen: { width: 1400, height: 900 },
    open: (...a) => {
      args = a;
      return popup;
    },
  };
  assert.equal(
    openAuthorizationWindow(
      host,
      "https://fcloudpan.com/oauth/device?user_code=ABCD-EFGH",
      "https://fcloudpan.com",
    ),
    popup,
  );
  assert.equal(popup.opener, null);
  assert.equal(
    destination,
    "https://fcloudpan.com/oauth/device?user_code=ABCD-EFGH",
  );
  assert.equal(args[0], "about:blank");
  assert.match(args[2], /width=600,height=700,left=400,top=100/);
});
test("blocked popups are reported for fallback links", () => {
  assert.equal(
    openAuthorizationWindow(
      { screen: { width: 600, height: 700 }, open: () => null },
      "https://fcloudpan.com/oauth/device",
      "https://fcloudpan.com",
    ),
    null,
  );
});
test("untrusted destinations never open a window", () => {
  const host = {
    open() {
      throw Error("must not open");
    },
  };
  for (const url of [
    "https://evil.example/oauth/device",
    "https://fcloudpan.com/other",
    "javascript:alert(1)",
  ])
    assert.throws(
      () => openAuthorizationWindow(host, url, "https://fcloudpan.com"),
      /Invalid authorization URL/,
    );
});
