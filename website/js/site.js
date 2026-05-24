(function () {
  const nav = document.querySelector(".site-nav");
  const toggle = document.querySelector(".nav-toggle");
  const links = document.querySelector(".nav-links");

  if (nav) {
    const onScroll = () => {
      nav.classList.toggle("scrolled", window.scrollY > 24);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

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
          const show = img.dataset.subpanel === id;
          img.hidden = !show;
        });
      });
    });
  });
})();
