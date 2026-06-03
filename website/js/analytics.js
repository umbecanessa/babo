(function () {
  var VISITOR_KEY = "babo_analytics_visitor_id";
  var ATTRIBUTION_KEY = "babo_analytics_attribution";
  var API_BASE =
    window.BABO_ANALYTICS_API ||
    (location.hostname === "localhost" || location.hostname === "127.0.0.1"
      ? "http://localhost:3000/api"
      : "https://api.babo.agency/api");

  var enabled = null;
  var queue = [];
  var flushing = false;

  function visitorId() {
    try {
      var id = localStorage.getItem(VISITOR_KEY);
      if (!id) {
        id =
          typeof crypto !== "undefined" && crypto.randomUUID
            ? crypto.randomUUID()
            : "v-" + Math.random().toString(36).slice(2);
        localStorage.setItem(VISITOR_KEY, id);
      }
      return id;
    } catch (_) {
      return "unknown";
    }
  }

  function readAttribution() {
    try {
      var raw = localStorage.getItem(ATTRIBUTION_KEY);
      return raw ? JSON.parse(raw) : {};
    } catch (_) {
      return {};
    }
  }

  function captureAttribution() {
    try {
      var params = new URLSearchParams(window.location.search);
      var utmSource = params.get("utm_source");
      var utmMedium = params.get("utm_medium");
      var utmCampaign = params.get("utm_campaign");
      var utmContent = params.get("utm_content");
      if (!utmSource && !utmMedium && !utmCampaign && !utmContent) return;

      var current = readAttribution();
      var next = Object.assign({}, current);
      if (utmSource) next.utm_source = utmSource;
      if (utmMedium) next.utm_medium = utmMedium;
      if (utmCampaign) next.utm_campaign = utmCampaign;
      if (utmContent) next.utm_content = utmContent;
      localStorage.setItem(ATTRIBUTION_KEY, JSON.stringify(next));
    } catch (_) {
      /* ignore */
    }
  }

  function audience() {
    return document.body.dataset.audience || "innovator";
  }

  function track(name, properties) {
    if (enabled !== true) return;
    queue.push({
      name: name,
      properties: Object.assign({}, readAttribution(), properties || {}, {
        audience: audience(),
      }),
    });
    flush();
  }

  function flush() {
    if (flushing || enabled !== true || !queue.length) return;
    flushing = true;
    var batch = queue.splice(0, 20);
    fetch(API_BASE + "/analytics/web-events", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        events: batch.map(function (event) {
          return {
            name: event.name,
            installId: visitorId(),
            platform: "web",
            occurredAt: new Date().toISOString(),
            properties: event.properties,
          };
        }),
      }),
      keepalive: true,
    })
      .catch(function () {
        queue.unshift.apply(queue, batch);
      })
      .finally(function () {
        flushing = false;
        if (queue.length) flush();
      });
  }

  function linkLocation(anchor) {
    var hrefAttr = anchor.getAttribute("href") || "";

    if (anchor.id === "hero-cta-primary") return "hero_primary";
    if (anchor.id === "hero-cta-secondary") return "hero_secondary";
    if (anchor.id === "manifesto-cta") return "manifesto_cta";
    if (anchor.id === "contribute-primary") return "contribute_primary";
    if (anchor.id === "contribute-secondary") return "contribute_secondary";
    if (anchor.classList.contains("nav-discord") || anchor.id === "contribute-discord") {
      return "discord";
    }
    if (anchor.closest(".brand")) return "brand";

    var card = anchor.closest(".pricing-card");
    if (card) {
      var title = card.querySelector("h3");
      return "pricing_" + (title ? title.textContent.trim().toLowerCase().replace(/\s+/g, "_") : "plan");
    }

    var downloadSection = anchor.closest("#download");
    if (downloadSection) {
      var label = (anchor.textContent || "").trim().toLowerCase();
      if (label.indexOf("download") >= 0) return "download_release";
      if (label.indexOf("install") >= 0) return "download_install_guide";
      if (label.indexOf("self-host") >= 0) return "download_self_host_guide";
      if (label.indexOf("github") >= 0) return "download_github";
      return "download_other";
    }

    if (hrefAttr.indexOf("download") >= 0) return "download_redirect";

    if (anchor.closest(".site-nav")) {
      if (hrefAttr.indexOf("getting-started") >= 0) return "nav_docs";
      var navSlug = hrefAttr.replace(/^#/, "") || "link";
      return "nav_" + navSlug;
    }

    if (anchor.closest(".site-footer")) {
      if (hrefAttr.indexOf("privacy") >= 0) return "footer_privacy";
      if (hrefAttr.indexOf("getting-started") >= 0) return "footer_docs";
      if (hrefAttr.indexOf("neural-ledger") >= 0) return "footer_nls";
      if (hrefAttr.indexOf("umbecanessa/babo") >= 0) return "footer_github";
      return "footer_link";
    }

    if (anchor.closest("#manifesto")) return "manifesto_section";
    if (anchor.closest("#contribute")) return "contribute_section";

    if (hrefAttr.charAt(0) === "#") {
      return "anchor_" + (hrefAttr.slice(1) || "top");
    }

    if (/^https?:\/\//i.test(hrefAttr)) return "external_link";
    return "internal_" + hrefAttr.replace(/[^\w]+/g, "_").slice(0, 48);
  }

  function outboundDestination(href) {
    if (!href) return "unknown";
    if (href.indexOf("github.com/umbecanessa/babo/releases") >= 0) return "github_releases";
    if (href.indexOf("github.com/umbecanessa/babo") >= 0) return "github_repo";
    if (href.indexOf("discord.gg") >= 0) return "discord";
    if (/^https?:\/\//i.test(href)) return "external";
    return "internal";
  }

  function bindClicks() {
    document.addEventListener(
      "click",
      function (event) {
        if (enabled !== true) return;
        var anchor = event.target && event.target.closest ? event.target.closest("a") : null;
        if (!anchor || !anchor.href) return;

        var locationName = linkLocation(anchor);
        var href = anchor.href;
        var dest = outboundDestination(href);
        if (dest === "external" || dest === "github_releases" || dest === "github_repo" || dest === "discord") {
          track("outbound_click", { location: locationName, destination: dest });
        } else {
          track("cta_click", { location: locationName, destination: dest });
        }
      },
      true
    );

    document.addEventListener("click", function (event) {
      if (enabled !== true) return;
      var tab = event.target && event.target.closest ? event.target.closest(".showcase-tab") : null;
      if (!tab) return;
      track("showcase_tab", { tab: tab.dataset.tab || "unknown" });
    });

    document.addEventListener("click", function (event) {
      if (enabled !== true) return;
      var subtab = event.target && event.target.closest ? event.target.closest(".showcase-subtab") : null;
      if (!subtab) return;
      track("showcase_subtab", { tab: subtab.dataset.subtab || "unknown" });
    });

    document.addEventListener("babo:lightbox-open", function (event) {
      if (enabled !== true) return;
      track("showcase_lightbox", { alt: (event.detail && event.detail.alt) || "" });
    });
  }

  function bindSectionViews() {
    if (!window.IntersectionObserver) return;
    var seen = {};
    var obs = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting || entry.intersectionRatio < 0.35) return;
          var id = entry.target.id;
          if (!id || seen[id]) return;
          seen[id] = true;
          track("section_view", { section: id });
        });
      },
      { threshold: [0.35] }
    );

    ["manifesto", "punches", "drift", "product", "pricing", "download", "contribute"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) obs.observe(el);
    });
  }

  function loadConfig() {
    return fetch(API_BASE + "/analytics/config")
      .then(function (res) {
        return res.json();
      })
      .then(function (cfg) {
        enabled = !!(cfg && cfg.enabled);
        if (enabled) {
          var path = location.pathname || "/";
          var eventName = path.indexOf("/download") >= 0 ? "download_page_view" : "landing_page_view";
          track(eventName, { path: path });
          bindSectionViews();
        }
      })
      .catch(function () {
        enabled = false;
      });
  }

  captureAttribution();
  bindClicks();
  loadConfig();

  window.BABO_ANALYTICS = { track: track };
})();
