document.addEventListener("DOMContentLoaded", () => {
  const node = document.querySelector("[data-status-url]");
  if (!node) return;
  const refresh = async () => {
    const response = await fetch(node.dataset.statusUrl, { credentials: "same-origin" });
    if (response.ok) node.querySelector("strong").textContent = (await response.json()).status;
  };
  window.setInterval(refresh, 2000);
});
