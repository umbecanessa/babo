(function () {
  const THEME_KEY = "babo_theme";
  const AUDIENCE_KEY = "babo_audience";

  function resolveAudience() {
    const params = new URLSearchParams(window.location.search);
    let fromUrl = params.get("audience") || params.get("utm_content") || "";
    fromUrl = fromUrl.toLowerCase().trim();
    if (fromUrl === "home") fromUrl = "everyday";
    if (fromUrl === "innovator" || fromUrl === "everyday") {
      try {
        sessionStorage.setItem(AUDIENCE_KEY, fromUrl);
      } catch {
        /* ignore */
      }
      return fromUrl;
    }
    try {
      const stored = sessionStorage.getItem(AUDIENCE_KEY);
      if (stored === "innovator" || stored === "everyday") return stored;
    } catch {
      /* ignore */
    }
    return "innovator";
  }

  function applyAudience(id) {
    const copy = window.BABO_AUDIENCE && window.BABO_AUDIENCE[id];
    if (!copy) return;

    document.body.dataset.audience = id;

    document.title = copy.meta.title;
    const metaDesc = document.querySelector('meta[name="description"]');
    if (metaDesc) metaDesc.setAttribute("content", copy.meta.description);

    const badgesEl = document.getElementById("hero-badges");
    if (badgesEl) {
      badgesEl.innerHTML = copy.badges
        .map((b) => {
          const text = typeof b === "string" ? b : b.text;
          const type = typeof b === "string" ? "soft" : b.type || "soft";
          const cls = type === "alive" ? "badge badge-alive" : "badge badge-soft";
          const dot = type === "alive" ? '<span class="alive-dot" aria-hidden="true"></span> ' : "";
          return `<span class="${cls}">${dot}${text}</span>`;
        })
        .join("");
    }

    const titleEl = document.getElementById("hero-title");
    const gradEl = document.getElementById("hero-title-gradient");
    if (titleEl) titleEl.textContent = copy.hero.title;
    if (gradEl) gradEl.textContent = copy.hero.titleGradient;

    const leadEl = document.getElementById("hero-lead");
    if (leadEl) leadEl.innerHTML = copy.hero.lead;

    const ctaP = document.getElementById("hero-cta-primary");
    const ctaS = document.getElementById("hero-cta-secondary");
    if (ctaP) {
      ctaP.href = copy.hero.ctaPrimary.href;
      ctaP.textContent = copy.hero.ctaPrimary.label;
    }
    if (ctaS) {
      ctaS.href = copy.hero.ctaSecondary.href;
      ctaS.textContent = copy.hero.ctaSecondary.label;
    }

    const metaEl = document.getElementById("hero-meta");
    if (metaEl) metaEl.textContent = copy.hero.meta;

    const punchesLabel = document.getElementById("punches-label");
    const punchesTitle = document.getElementById("punches-title");
    if (punchesLabel) punchesLabel.textContent = copy.punchesLabel;
    if (punchesTitle) punchesTitle.textContent = copy.punchesTitle;

    const punchGrid = document.getElementById("punch-grid");
    if (punchGrid) {
      punchGrid.innerHTML = copy.punches
        .map(
          (p, i) => `
        <article class="punch-card glass${i === 0 ? " punch-card-featured" : ""}">
          <span class="punch-num">${i + 1}</span>
          <h3>${p.title}</h3>
          <p>${p.text}</p>
        </article>`
        )
        .join("");
    }

    const productLead = document.getElementById("product-lead");
    if (productLead) productLead.textContent = copy.productLead;

    document.querySelectorAll("[data-caption]").forEach((el) => {
      const key = el.dataset.caption;
      if (copy.captions[key]) el.textContent = copy.captions[key];
    });

    const quoteEl = document.getElementById("manifesto-quote");
    const bodyEl = document.getElementById("manifesto-body");
    if (quoteEl) quoteEl.textContent = copy.manifesto.quote;
    if (bodyEl) bodyEl.innerHTML = copy.manifesto.body;

    const driftSection = document.getElementById("drift");
    if (driftSection) {
      if (copy.drift) {
        driftSection.classList.remove("hidden");
        const dt = document.getElementById("drift-title");
        const dx = document.getElementById("drift-text");
        if (dt) dt.textContent = copy.drift.title;
        if (dx) dx.textContent = copy.drift.text;
      } else {
        driftSection.classList.add("hidden");
      }
    }

    const downloadTitle = document.getElementById("download-title");
    const downloadLead = document.getElementById("download-lead");
    if (downloadTitle) downloadTitle.textContent = copy.bottom.downloadTitle;
    if (downloadLead) downloadLead.textContent = copy.bottom.downloadLead;

    const contribTitle = document.getElementById("contribute-title");
    const contribLead = document.getElementById("contribute-lead");
    const contribP = document.getElementById("contribute-primary");
    const contribS = document.getElementById("contribute-secondary");
    if (contribTitle) contribTitle.textContent = copy.bottom.contributeTitle;
    if (contribLead) contribLead.textContent = copy.bottom.contributeLead;
    if (contribP) {
      contribP.href = copy.bottom.contributePrimary.href;
      contribP.textContent = copy.bottom.contributePrimary.label;
    }
    if (contribS) {
      contribS.href = copy.bottom.contributeSecondary.href;
      contribS.textContent = copy.bottom.contributeSecondary.label;
    }

    document.querySelectorAll(".nav-contribute").forEach((li) => {
      li.style.display = copy.nav.showContribute ? "" : "none";
    });
  }

  const audienceId = resolveAudience();
  applyAudience(audienceId);

  /* Theme */
  function loadMode() {
    try {
      const raw = localStorage.getItem(THEME_KEY);
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
      localStorage.setItem(THEME_KEY, mode);
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

  /* Nav mobile */
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

  /* Showcase tabs */
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
