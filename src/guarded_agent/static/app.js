document.addEventListener("DOMContentLoaded", () => {
  const themeButton = document.querySelector("[data-theme-toggle]");
  const themes = ["system", "light", "dark"];
  if (themeButton) {
    const initialTheme = themes.includes(document.documentElement.dataset.theme)
      ? document.documentElement.dataset.theme
      : "system";
    themeButton.setAttribute("aria-pressed", String(initialTheme !== "system"));
    themeButton.addEventListener("click", () => {
      const currentTheme = themes.includes(document.documentElement.dataset.theme)
        ? document.documentElement.dataset.theme
        : "system";
      const nextTheme = themes[(themes.indexOf(currentTheme) + 1) % themes.length];
      document.documentElement.dataset.theme = nextTheme;
      try {
        localStorage.setItem("guarded-agent-theme", nextTheme);
      } catch (_) {
        // Theme selection continues for this page when storage is unavailable.
      }
      themeButton.setAttribute("aria-pressed", String(nextTheme !== "system"));
    });
  }

  const node = document.querySelector("[data-status-url]");
  if (!node) return;
  const refresh = async () => {
    const response = await fetch(node.dataset.statusUrl, { credentials: "same-origin" });
    if (response.ok) node.querySelector("strong").textContent = (await response.json()).status;
  };
  window.setInterval(refresh, 2000);
});
