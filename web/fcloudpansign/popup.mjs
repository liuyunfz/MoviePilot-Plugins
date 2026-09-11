export function openAuthorizationWindow(host, value, origin) {
  const url = new URL(value);
  if (
    url.origin !== origin ||
    url.pathname !== "/oauth/device" ||
    url.username ||
    url.password ||
    url.hash ||
    !["https:", "http:"].includes(url.protocol)
  )
    throw Error("Invalid authorization URL");
  // Same popup pattern as MoviePilot's storage OAuth dialog; no MP callback needed.
  const width = 600,
    height = 700;
  const left = Math.max(0, Math.round((host.screen.width - width) / 2));
  const top = Math.max(0, Math.round((host.screen.height - height) / 2));
  const popup = host.open(
    "about:blank",
    "FCloudpanAuthorization",
    `width=${width},height=${height},left=${left},top=${top},toolbar=no,location=no,status=no,menubar=no,scrollbars=yes,resizable=yes`,
  );
  if (!popup) return null;
  try {
    popup.opener = null;
    popup.location.replace(url.href);
    popup.focus();
    return popup;
  } catch (error) {
    try {
      popup.close();
    } catch {}
    throw error;
  }
}
