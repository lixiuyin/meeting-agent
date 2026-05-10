// Prevent theme flash by applying the saved theme before React bootstraps.
(function () {
  try {
    var s = localStorage.getItem("meeting-agent-theme");
    var theme =
      s === "dark" || s === "light"
        ? s
        : window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
          ? "dark"
          : "light";
    document.documentElement.setAttribute("data-theme", theme);
  } catch {
    document.documentElement.setAttribute("data-theme", "light");
  }
})();
