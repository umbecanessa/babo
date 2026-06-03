(function () {
  var RELEASES_URL =
    window.BABO_DOWNLOAD_REDIRECT ||
    "https://github.com/umbecanessa/babo/releases/latest";
  var API_BASE =
    window.BABO_ANALYTICS_API ||
    (location.hostname === "localhost" || location.hostname === "127.0.0.1"
      ? "http://localhost:3000/api"
      : "https://api.babo.agency/api");
  var statusEl = document.getElementById("download-status");

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text;
  }

  function visitorId() {
    try {
      var key = "babo_analytics_visitor_id";
      var id = localStorage.getItem(key);
      if (!id) {
        id =
          typeof crypto !== "undefined" && crypto.randomUUID
            ? crypto.randomUUID()
            : "v-" + Math.random().toString(36).slice(2);
        localStorage.setItem(key, id);
      }
      return id;
    } catch (_) {
      return "unknown";
    }
  }

  function readAttribution() {
    try {
      var raw = localStorage.getItem("babo_analytics_attribution");
      return raw ? JSON.parse(raw) : {};
    } catch (_) {
      return {};
    }
  }

  function copyClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).catch(function () {});
    }
    return Promise.resolve();
  }

  function analyticsEnabled() {
    return fetch(API_BASE + "/analytics/config")
      .then(function (res) { return res.json(); })
      .then(function (cfg) { return !!(cfg && cfg.enabled); })
      .catch(function () { return false; });
  }

  function createHandoff(enabled) {
    if (!enabled) return Promise.resolve(null);
    var props = Object.assign({}, readAttribution(), {
      audience: document.body.dataset.audience || "innovator",
      path: "/download/",
    });
    return fetch(API_BASE + "/analytics/handoff", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ visitorId: visitorId(), properties: props }),
    })
      .then(function (res) { return res.json(); })
      .then(function (data) { return data && data.ref ? data : null; })
      .catch(function () { return null; });
  }

  function sendDownloadIntent(ref) {
    if (!window.BABO_ANALYTICS || !window.BABO_ANALYTICS.track) return;
    window.BABO_ANALYTICS.track("download_intent", {
      destination: "github_releases",
      attribution_ref: ref || null,
    });
  }

  function redirectToReleases(ref) {
    var url = RELEASES_URL;
    if (ref) {
      url += (url.indexOf("?") >= 0 ? "&" : "?") + "babo_ref=" + encodeURIComponent(ref);
    }
    window.location.replace(url);
  }

  analyticsEnabled().then(function (enabled) {
    if (!enabled) {
      setStatus("Redirecting to GitHub Releases…");
      redirectToReleases(null);
      return;
    }

    setStatus("Linking your visit to the installer…");
    createHandoff(true).then(function (handoff) {
      var ref = handoff ? handoff.ref : null;
      var clip = handoff && handoff.clipPayload ? handoff.clipPayload : null;
      if (ref) {
        try {
          localStorage.setItem("babo_attribution_ref", ref);
        } catch (_) {}
      }
      sendDownloadIntent(ref);
      if (clip) {
        copyClipboard(clip).finally(function () {
          setStatus("Redirecting to GitHub Releases…");
          redirectToReleases(ref);
        });
      } else {
        setStatus("Redirecting to GitHub Releases…");
        redirectToReleases(ref);
      }
    });
  });
})();
