(function () {
  const STORAGE_KEY = "babo_theme";

  function loadMode() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw === "light" || raw === "dark" || raw === "system") return raw;
    } catch {
      /* ignore */
    }
    return "dark";
  }

  function effectiveTheme(mode) {
    if (mode === "light" || mode === "dark") return mode;
    try {
      return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    } catch {
      return "dark";
    }
  }

  let mode = loadMode();

  function applyTheme() {
    document.documentElement.setAttribute("data-theme", mode);
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      meta.setAttribute("content", effectiveTheme(mode) === "dark" ? "#0c0d14" : "#eef0f7");
    }
    const tip = document.getElementById("theme-toggle");
    if (tip) {
      if (mode === "light") tip.title = "Theme: Light — click for Dark";
      else if (mode === "dark") tip.title = "Theme: Dark — click for System";
      else tip.title = "Theme: System — click for Light";
    }
  }

  function cycleTheme() {
    if (mode === "light") mode = "dark";
    else if (mode === "dark") mode = "system";
    else mode = "light";
    try {
      localStorage.setItem(STORAGE_KEY, mode);
    } catch {
      /* ignore */
    }
    applyTheme();
  }

  applyTheme();

  const themeBtn = document.getElementById("theme-toggle");
  if (themeBtn) themeBtn.addEventListener("click", cycleTheme);

  try {
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      if (mode === "system") applyTheme();
    };
    if (mql.addEventListener) mql.addEventListener("change", onChange);
    else if (mql.addListener) mql.addListener(onChange);
  } catch {
    /* ignore */
  }

  const nav = document.querySelector(".site-nav");
  const toggle = document.querySelector(".nav-toggle");
  const links = document.querySelector(".nav-links");

  if (toggle && links) {
    toggle.addEventListener("click", () => {
      const open = links.classList.toggle("open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
    links.querySelectorAll("a").forEach((a) => {
      a.addEventListener("click", () => {
        links.classList.remove("open");
        toggle.setAttribute("aria-expanded", "false");
      });
    });
  }

  const year = document.getElementById("year");
  if (year) year.textContent = String(new Date().getFullYear());

  const showcase = document.querySelector("[data-showcase]");
  if (!showcase) return;

  const mainTabs = showcase.querySelectorAll(".showcase-tab");
  const panels = showcase.querySelectorAll(".showcase-panel");

  function setMainTab(tabId) {
    mainTabs.forEach((btn) => {
      const active = btn.dataset.tab === tabId;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    panels.forEach((panel) => {
      const active = panel.dataset.panel === tabId;
      panel.classList.toggle("is-active", active);
      panel.hidden = !active;
    });
  }

  mainTabs.forEach((btn) => {
    btn.addEventListener("click", () => setMainTab(btn.dataset.tab));
  });

  showcase.querySelectorAll(".showcase-panel").forEach((panel) => {
    const subtabs = panel.querySelectorAll(".showcase-subtab");
    if (!subtabs.length) return;
    const images = panel.querySelectorAll(".screen-viewport img[data-subpanel], img[data-subpanel]");
    subtabs.forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.dataset.subtab;
        subtabs.forEach((b) => {
          const on = b.dataset.subtab === id;
          b.classList.toggle("is-active", on);
          b.setAttribute("aria-selected", on ? "true" : "false");
        });
        images.forEach((img) => {
          img.hidden = img.dataset.subpanel !== id;
        });
      });
    });
  });
})();
