document.addEventListener("DOMContentLoaded", () => {
  const themeButton = document.querySelector("[data-theme-toggle]");
  const themes = ["system", "light", "dark"];
  const themeLabels = { system: "跟随系统", light: "浅色", dark: "深色" };
  const statusLabels = {
    CREATED: "已创建",
    RUNNING: "执行中",
    WAITING_APPROVAL: "等待审批",
    COMPLETED: "已完成",
    FAILED: "失败",
    CANCELLED: "已取消",
  };

  const currentTheme = () =>
    themes.includes(document.documentElement.dataset.theme)
      ? document.documentElement.dataset.theme
      : "system";
  const updateThemeButton = () => {
    if (!themeButton) return;
    const theme = currentTheme();
    const label = `主题：${themeLabels[theme]}`;
    themeButton.setAttribute("aria-label", `切换主题；当前${themeLabels[theme]}`);
    themeButton.setAttribute("aria-pressed", String(theme !== "system"));
    const labelNode =
      typeof themeButton.querySelector === "function"
        ? themeButton.querySelector("[data-theme-toggle-label]")
        : null;
    if (labelNode) labelNode.textContent = label;
  };

  if (themeButton) {
    updateThemeButton();
    themeButton.addEventListener("click", () => {
      const theme = currentTheme();
      const nextTheme = themes[(themes.indexOf(theme) + 1) % themes.length];
      document.documentElement.dataset.theme = nextTheme;
      try {
        localStorage.setItem("guarded-agent-theme", nextTheme);
      } catch (_) {
        // Theme selection continues for this page when storage is unavailable.
      }
      updateThemeButton();
    });

    if (typeof window.matchMedia === "function") {
      const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
      mediaQuery.addEventListener("change", () => {
        if (document.documentElement.dataset.theme !== "system") return;
        updateThemeButton();
      });
    }
  }

  const node = document.querySelector("[data-status-url]");
  if (!node) return;

  let url;
  try {
    url = new URL(node.dataset.statusUrl, window.location.origin);
    if (url.origin !== window.location.origin) return;
  } catch (_) {
    return;
  }

  const renderTimeline = (timeline) => {
    const container = document.querySelector("[data-timeline]");
    if (!container || !Array.isArray(timeline)) return;
    const signature = JSON.stringify(timeline);
    if (container.dataset.timelineSignature === signature) return;
    container.replaceChildren();
    for (const event of timeline) {
      if (!event || typeof event !== "object") continue;
      const item = document.createElement("li");
      item.className = "timeline-event";
      const marker = document.createElement("span");
      marker.className = "timeline-event__node";
      marker.setAttribute("aria-hidden", "true");
      const body = document.createElement("div");
      const heading = document.createElement("div");
      heading.className = "timeline-event__heading";
      const label = document.createElement("strong");
      label.textContent = String(event.event_label || "受控事件");
      const time = document.createElement("time");
      time.dateTime = String(event.time || "");
      time.textContent = String(event.time_display || "");
      const raw = document.createElement("p");
      raw.className = "raw-value";
      raw.textContent = String(event.event || "");
      const details = document.createElement("details");
      details.className = "event-payload";
      const summary = document.createElement("summary");
      summary.textContent = "查看结构化载荷";
      const payload = document.createElement("pre");
      payload.textContent = String(event.payload || "");
      heading.append(label, time);
      details.append(summary, payload);
      body.append(heading, raw, details);
      item.append(marker, body);
      container.append(item);
    }
    container.dataset.timelineSignature = signature;
  };

  const refresh = async () => {
    try {
      const response = await fetch(url, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      const payload = await response.json();
      if (!payload || typeof payload.status !== "string") return;
      for (const value of document.querySelectorAll("[data-status-value]")) {
        value.textContent = payload.status;
        const badge = value.closest(".status-badge");
        if (badge) badge.setAttribute("aria-label", `状态：${statusLabels[payload.status] || "未知"}`);
      }
      for (const label of document.querySelectorAll("[data-status-label]")) {
        label.textContent = statusLabels[payload.status] || "未知";
      }
      renderTimeline(payload.timeline);
    } catch (_) {
      // Polling is best effort: a transient local failure must not break the page.
    }
  };

  window.setInterval(refresh, 2000);
});
