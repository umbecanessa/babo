(function () {
  const THEME_KEY = "babo_theme";
  const AUDIENCE_KEY = "babo_audience";

  function applyLink(el, href) {
    if (!el) return;
    el.href = href;
    if (/^https?:\/\//i.test(href)) {
      el.target = "_blank";
      el.rel = "noopener noreferrer";
    } else {
      el.removeAttribute("target");
      el.removeAttribute("rel");
    }
  }

  function externalLinkAttrs(href) {
    return /^https?:\/\//i.test(href) ? ' target="_blank" rel="noopener noreferrer"' : "";
  }

  const DISCORD_ICON =
    '<svg class="btn-icon" viewBox="0 0 24 24" fill="currentColor" width="18" height="18" aria-hidden="true"><path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028 14.09 14.09 0 0 0 1.226-1.994.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z"/></svg>';

  function applyHeroCta(el, cta, primary) {
    if (!el || !cta) return;
    applyLink(el, cta.href);
    el.classList.remove("btn-primary", "btn-glow", "btn-ghost", "btn-discord");
    if (primary) {
      el.classList.add("btn-primary", "btn-glow");
      el.textContent = cta.label;
    } else if (cta.variant === "discord") {
      el.classList.add("btn-discord");
      el.innerHTML = DISCORD_ICON + `<span>${cta.label}</span>`;
    } else {
      el.classList.add("btn-ghost");
      el.textContent = cta.label;
    }
  }

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
    applyHeroCta(ctaP, copy.hero.ctaPrimary, true);
    applyHeroCta(ctaS, copy.hero.ctaSecondary, false);

    const metaEl = document.getElementById("hero-meta");
    if (metaEl) {
      if (copy.hero.metaHtml) metaEl.innerHTML = copy.hero.metaHtml;
      else if (copy.hero.meta) metaEl.textContent = copy.hero.meta;
    }

    const heroImg = document.getElementById("hero-product-img");
    if (heroImg && copy.hero.visual) {
      heroImg.src = copy.hero.visual.src;
      heroImg.alt = copy.hero.visual.alt || "Babo product screenshot";
    }

    const stickyDownload = document.getElementById("sticky-cta-download");
    const stickyDiscord = document.getElementById("sticky-cta-discord");
    const stickyLabel = document.getElementById("sticky-cta-label");
    if (stickyDownload && copy.hero.ctaPrimary) {
      applyLink(stickyDownload, copy.hero.ctaPrimary.href);
      stickyDownload.textContent = copy.hero.ctaPrimary.label;
    }
    if (stickyDiscord && copy.hero.ctaSecondary?.variant === "discord") {
      applyLink(stickyDiscord, copy.hero.ctaSecondary.href);
      stickyDiscord.textContent = "Discord";
    }
    if (stickyLabel) {
      stickyLabel.textContent = id === "everyday" ? "Babo — beyond ChatGPT" : "Babo — local agent runtime";
    }

    const integratesLabel = document.getElementById("integrates-label");
    const integratesLogos = document.getElementById("integrates-logos");
    if (integratesLabel) integratesLabel.textContent = copy.integratesLabel || "Works with";
    if (integratesLogos && copy.integrations) {
      integratesLogos.innerHTML = copy.integrations
        .map(
          (item) => `
        <li>
          <span class="integrates-item" title="${item.name}">
            <img src="assets/integrations/${item.id}.svg" alt="${item.name}" width="28" height="28" loading="lazy" />
            <span class="integrates-name">${item.name}</span>
          </span>
        </li>`
        )
        .join("");
    }

    const punchesLabel = document.getElementById("punches-label");
    const punchesTitle = document.getElementById("punches-title");
    if (punchesLabel) punchesLabel.textContent = copy.punchesLabel;
    if (punchesTitle) punchesTitle.textContent = copy.punchesTitle;

    const punchGrid = document.getElementById("punch-grid");
    if (punchGrid) {
      punchGrid.innerHTML = copy.punches
        .map(
          (p) => `
        <article class="punch-card">
          <div class="punch-body">
            ${p.step ? `<p class="punch-step">${p.step}</p>` : ""}
            <h3>${p.title}</h3>
            <p>${p.text}</p>
          </div>
        </article>`
        )
        .join("");
    }

    const marqueeTrack = document.getElementById("marquee-track");
    if (marqueeTrack && copy.marquee && copy.marquee.length) {
      const items = copy.marquee
        .map((t) => `<span class="marquee-item">${t}</span>`)
        .join("");
      marqueeTrack.innerHTML = `<div class="marquee-group">${items}</div><div class="marquee-group" aria-hidden="true">${items}</div>`;
    }

    const productLead = document.getElementById("product-lead");
    if (productLead) productLead.textContent = copy.productLead;

    document.querySelectorAll("[data-caption]").forEach((el) => {
      const key = el.dataset.caption;
      if (copy.captions[key]) el.textContent = copy.captions[key];
    });

    const manifestoLabel = document.getElementById("manifesto-teaser-label");
    const quoteEl = document.getElementById("manifesto-quote");
    const snippetEl = document.getElementById("manifesto-snippet");
    const manifestoCta = document.getElementById("manifesto-cta");
    if (manifestoLabel && copy.manifesto.label) manifestoLabel.textContent = copy.manifesto.label;
    if (quoteEl) quoteEl.textContent = copy.manifesto.quote;
    if (snippetEl) snippetEl.innerHTML = copy.manifesto.snippet || "";
    if (manifestoCta && copy.manifesto.cta) {
      manifestoCta.href = copy.manifesto.cta.href;
      manifestoCta.textContent = copy.manifesto.cta.label;
    }

    const capLabel = document.getElementById("capabilities-label");
    const capTitle = document.getElementById("capabilities-title");
    const capLead = document.getElementById("capabilities-lead");
    const capGrid = document.getElementById("capabilities-grid");
    if (copy.capabilities) {
      if (capLabel) capLabel.textContent = copy.capabilities.label;
      if (capTitle) capTitle.textContent = copy.capabilities.title;
      if (capLead) capLead.textContent = copy.capabilities.lead;
      if (capGrid && copy.capabilities.items) {
        capGrid.innerHTML = copy.capabilities.items
          .map(
            (item) => `
        <article class="cap-card glass">
          <h3>${item.title}</h3>
          <p class="cap-tags">${item.tags}</p>
          <p class="cap-text">${item.text}</p>
        </article>`
          )
          .join("");
      }
    }

    const qsBar = document.getElementById("quickstart-bar");
    const qsLabel = document.getElementById("quickstart-label");
    const qsHint = document.getElementById("quickstart-hint");
    const qsCommands = document.getElementById("quickstart-commands");
    if (copy.quickstart && qsBar) {
      qsBar.hidden = false;
      if (qsLabel) qsLabel.textContent = copy.quickstart.label;
      if (qsHint) qsHint.textContent = copy.quickstart.hint || "";
      if (qsCommands && copy.quickstart.commands) {
        qsCommands.innerHTML = copy.quickstart.commands
          .map((cmd) => {
            const inner = cmd.href
              ? `<a class="quickstart-cmd-value" href="${cmd.href}" target="_blank" rel="noopener noreferrer">${cmd.value}</a>`
              : `<code class="quickstart-cmd-value">${cmd.value}</code>`;
            const copyBtn = cmd.copy
              ? `<button type="button" class="quickstart-copy" data-copy="${cmd.value.replace(/"/g, "&quot;")}" aria-label="Copy command">Copy</button>`
              : "";
            return `<div class="quickstart-cmd"><span class="quickstart-cmd-label">${cmd.label}</span>${inner}${copyBtn}</div>`;
          })
          .join("");
        qsCommands.querySelectorAll(".quickstart-copy").forEach((btn) => {
          btn.addEventListener("click", () => {
            const text = btn.getAttribute("data-copy") || "";
            navigator.clipboard?.writeText(text).then(() => {
              btn.textContent = "Copied";
              setTimeout(() => {
                btn.textContent = "Copy";
              }, 1600);
            });
          });
        });
      }
    } else if (qsBar) {
      qsBar.hidden = true;
    }

    const platformSection = document.getElementById("platform");
    if (platformSection) {
      if (copy.platform) {
        platformSection.classList.remove("hidden");
        const pl = document.getElementById("platform-label");
        const pt = document.getElementById("platform-title");
        const px = document.getElementById("platform-text");
        const pc = document.getElementById("platform-cta");
        const ps = document.getElementById("platform-stack");
        if (pl && copy.platform.label) pl.textContent = copy.platform.label;
        if (pt) pt.textContent = copy.platform.title;
        if (px) px.textContent = copy.platform.text;
        if (pc && copy.platform.cta) {
          pc.href = copy.platform.cta.href;
          pc.textContent = copy.platform.cta.label;
        }
        if (ps && copy.platform.stack) {
          ps.innerHTML = copy.platform.stack.map((line) => `<li>${line}</li>`).join("");
        }
      } else {
        platformSection.classList.add("hidden");
      }
    }

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

    if (copy.pricing) {
      const pricingLabel = document.getElementById("pricing-label");
      const pricingTitle = document.getElementById("pricing-title");
      const pricingLead = document.getElementById("pricing-lead");
      const pricingFootnote = document.getElementById("pricing-footnote");
      const pricingGrid = document.getElementById("pricing-grid");
      if (pricingLabel) pricingLabel.textContent = copy.pricing.label;
      if (pricingTitle) pricingTitle.textContent = copy.pricing.title;
      if (pricingLead) pricingLead.textContent = copy.pricing.lead;
      if (pricingFootnote) pricingFootnote.textContent = copy.pricing.footnote || "";
      if (pricingGrid && copy.pricing.plans) {
        pricingGrid.innerHTML = copy.pricing.plans
          .map(
            (plan) => `
        <article class="pricing-card${plan.featured ? " pricing-card-featured" : ""}">
          ${plan.badge ? `<span class="pricing-badge">${plan.badge}</span>` : ""}
          <h3>${plan.name}</h3>
          <p class="pricing-desc">${plan.description}</p>
          <div class="pricing-amount">
            <span class="pricing-price">${plan.price}</span>
            <span class="pricing-period">${plan.period}</span>
          </div>
          <ul class="pricing-features">
            ${plan.features.map((f) => `<li>${f}</li>`).join("")}
          </ul>
          ${plan.note ? `<p class="pricing-note">${plan.note}</p>` : ""}
          <a class="btn ${plan.featured ? "btn-primary" : "btn-ghost"}" href="${plan.cta.href}"${externalLinkAttrs(plan.cta.href)}>${plan.cta.label}</a>
        </article>`
          )
          .join("");
      }
    }

    const downloadTitle = document.getElementById("download-title");
    const downloadLead = document.getElementById("download-lead");
    if (downloadTitle) downloadTitle.textContent = copy.bottom.downloadTitle;
    if (downloadLead) downloadLead.textContent = copy.bottom.downloadLead;

    const contribTitle = document.getElementById("contribute-title");
    const contribLead = document.getElementById("contribute-lead");
    const contribP = document.getElementById("contribute-primary");
    const contribD = document.getElementById("contribute-discord");
    const contribDLabel = document.getElementById("contribute-discord-label");
    const contribS = document.getElementById("contribute-secondary");
    if (contribTitle) contribTitle.textContent = copy.bottom.contributeTitle;
    if (contribLead) contribLead.textContent = copy.bottom.contributeLead;
    if (contribP) {
      contribP.href = copy.bottom.contributePrimary.href;
      contribP.textContent = copy.bottom.contributePrimary.label;
    }
    if (contribD && copy.bottom.contributeDiscord) {
      contribD.href = copy.bottom.contributeDiscord.href;
      if (contribDLabel) contribDLabel.textContent = copy.bottom.contributeDiscord.label;
      contribD.hidden = false;
    } else if (contribD) {
      contribD.hidden = true;
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

  function observeReveals() {
    const els = document.querySelectorAll(".reveal:not(.is-visible)");
    if (!("IntersectionObserver" in window) || !els.length) {
      els.forEach((el) => el.classList.add("is-visible"));
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            e.target.classList.add("is-visible");
            io.unobserve(e.target);
          }
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -40px 0px" }
    );
    els.forEach((el) => io.observe(el));
  }

  document.querySelectorAll("#punch-grid .punch-card").forEach((card, i) => {
    card.classList.add("reveal");
    card.style.transitionDelay = `${0.08 + i * 0.1}s`;
  });
  const platformPanel = document.querySelector("#platform .platform-panel");
  const driftPanel = document.querySelector("#drift .drift-panel");
  if (platformPanel) platformPanel.classList.add("reveal");
  if (driftPanel) driftPanel.classList.add("reveal");
  document.querySelector(".contribute-panel")?.classList.add("reveal");

  observeReveals();

  requestAnimationFrame(() => {
    document.querySelectorAll(".hero .reveal").forEach((el) => el.classList.add("is-visible"));
  });

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

  /* Nav scroll state */
  const nav = document.querySelector(".site-nav");
  if (nav) {
    const onScroll = () => nav.classList.toggle("is-scrolled", window.scrollY > 24);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  /* Hero product frame tilt */
  const tiltEl = document.querySelector("[data-tilt]");
  if (tiltEl && window.matchMedia("(hover: hover) and (pointer: fine)").matches) {
    const frame = tiltEl.querySelector(".hero-product-frame");
    if (frame) {
      tiltEl.addEventListener("mousemove", (e) => {
        const r = tiltEl.getBoundingClientRect();
        const x = (e.clientX - r.left) / r.width - 0.5;
        const y = (e.clientY - r.top) / r.height - 0.5;
        frame.style.transform = `rotateY(${x * 8}deg) rotateX(${-y * 6}deg)`;
      });
      tiltEl.addEventListener("mouseleave", () => {
        frame.style.transform = "";
      });
    }
  }

  /* Sticky mobile CTA */
  const stickyCta = document.getElementById("sticky-cta");
  const heroSection = document.querySelector(".hero");
  if (stickyCta && heroSection) {
    const stickyObserver = new IntersectionObserver(
      ([entry]) => {
        stickyCta.hidden = entry.isIntersecting;
        stickyCta.classList.toggle("is-visible", !entry.isIntersecting);
      },
      { threshold: 0, rootMargin: "0px 0px -20% 0px" }
    );
    stickyObserver.observe(heroSection);
  }

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
    const viewport = panel.querySelector(".screen-viewport-showcase");
    const images = panel.querySelectorAll(".screen-viewport img[data-subpanel], img[data-subpanel]");

    function syncPortraitMode(activeId) {
      if (!viewport) return;
      const activeImg = panel.querySelector(`img[data-subpanel="${activeId}"]`);
      viewport.classList.toggle("is-portrait", !!activeImg?.hasAttribute("data-portrait"));
    }

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
        syncPortraitMode(id);
      });
    });

    const initial = panel.querySelector(".showcase-subtab.is-active")?.dataset.subtab;
    if (initial) syncPortraitMode(initial);
  });

  /* Fullscreen lightbox for showcase screenshots */
  const lightbox = document.getElementById("screenshot-lightbox");
  if (lightbox) {
    const lbImg = lightbox.querySelector(".lightbox-img");
    const lbStage = lightbox.querySelector(".lightbox-stage");
    const lbBackdrop = lightbox.querySelector(".lightbox-backdrop");
    const lbClose = lightbox.querySelector(".lightbox-close");
    let lastFocus = null;

    function openLightbox(img) {
      if (!img || img.hidden) return;
      lastFocus = document.activeElement;
      lbImg.src = img.currentSrc || img.src;
      lbImg.alt = img.alt || "Enlarged product screenshot";
      lbStage.classList.toggle("is-portrait", img.hasAttribute("data-portrait"));
      lightbox.hidden = false;
      lightbox.setAttribute("aria-hidden", "false");
      document.body.classList.add("lightbox-open");
      lbClose.focus();
      document.dispatchEvent(
        new CustomEvent("babo:lightbox-open", { detail: { alt: img.alt || "" } }),
      );
    }

    function closeLightbox() {
      lightbox.hidden = true;
      lightbox.setAttribute("aria-hidden", "true");
      document.body.classList.remove("lightbox-open");
      lbImg.removeAttribute("src");
      if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
    }

    showcase.querySelectorAll(".screen-viewport-showcase img").forEach((img) => {
      img.classList.add("showcase-zoomable");
      img.addEventListener("click", (e) => {
        e.stopPropagation();
        openLightbox(img);
      });
    });

    showcase.querySelectorAll(".screen-viewport-showcase").forEach((viewport) => {
      viewport.addEventListener("click", () => {
        openLightbox(viewport.querySelector("img:not([hidden])"));
      });
    });

    lbClose.addEventListener("click", closeLightbox);
    lbBackdrop.addEventListener("click", closeLightbox);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !lightbox.hidden) closeLightbox();
    });
  }
})();
