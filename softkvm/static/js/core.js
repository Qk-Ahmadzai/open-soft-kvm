/* SoftKVM - shared front-end runtime.
   Exposes a small global `SoftKVM` namespace used by every page. */
(function () {
  "use strict";

  const SoftKVM = {
    pollTimer: null,
    pollInterval: 2000,
    metrics: null,
  };

  /* ------------------------------------------------------------------ *
   * API helper
   * ------------------------------------------------------------------ */
  async function request(path, options) {
    const opts = Object.assign({ headers: {} }, options || {});
    opts.headers = Object.assign(
      { "Content-Type": "application/json" },
      opts.headers || {}
    );
    if (opts.body && typeof opts.body !== "string") {
      opts.body = JSON.stringify(opts.body);
    }

    let response;
    try {
      response = await fetch(path, opts);
    } catch (err) {
      throw new Error("Network request failed - is the server still running?");
    }

    let payload = null;
    const type = response.headers.get("content-type") || "";
    if (type.includes("application/json")) {
      payload = await response.json().catch(function () {
        return null;
      });
    }

    if (!response.ok) {
      const message =
        (payload && (payload.error || payload.message)) ||
        "Request failed (" + response.status + ")";
      const error = new Error(message);
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  SoftKVM.get = (path) => request(path, { method: "GET" });
  SoftKVM.post = (path, body) => request(path, { method: "POST", body: body || {} });
  SoftKVM.del = (path) => request(path, { method: "DELETE" });

  /* ------------------------------------------------------------------ *
   * Toasts
   * ------------------------------------------------------------------ */
  const TOAST_ICONS = {
    success: "i-check",
    error: "i-alert",
    warning: "i-alert",
    info: "i-info",
  };

  SoftKVM.toast = function (message, type, title) {
    const stack = document.getElementById("toastStack");
    if (!stack) return;

    const kind = type || "info";
    const el = document.createElement("div");
    el.className = "toast is-" + kind;

    const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    icon.setAttribute("class", "icon");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#" + (TOAST_ICONS[kind] || TOAST_ICONS.info));
    icon.appendChild(use);

    const body = document.createElement("div");
    body.className = "toast-body";
    if (title) {
      const strong = document.createElement("div");
      strong.className = "toast-title";
      strong.textContent = title;
      body.appendChild(strong);
    }
    const text = document.createElement("div");
    text.className = "toast-text";
    text.textContent = message;
    body.appendChild(text);

    el.appendChild(icon);
    el.appendChild(body);
    stack.appendChild(el);

    const remove = function () {
      el.classList.add("is-leaving");
      setTimeout(function () {
        el.remove();
      }, 220);
    };
    const timer = setTimeout(remove, kind === "error" ? 7000 : 4200);
    el.addEventListener("click", function () {
      clearTimeout(timer);
      remove();
    });
  };

  /* ------------------------------------------------------------------ *
   * Confirmation modal (replaces window.confirm)
   * ------------------------------------------------------------------ */
  SoftKVM.confirm = function (options) {
    const opts = options || {};
    return new Promise(function (resolve) {
      const backdrop = document.createElement("div");
      backdrop.className = "modal-backdrop";

      const modal = document.createElement("div");
      modal.className = "modal";
      modal.setAttribute("role", "dialog");
      modal.setAttribute("aria-modal", "true");

      const heading = document.createElement("h2");
      heading.textContent = opts.title || "Are you sure?";
      modal.appendChild(heading);

      const message = document.createElement("p");
      message.textContent = opts.message || "";
      modal.appendChild(message);

      if (opts.confirmLabel) {
        const input = document.createElement("input");
        input.className = "input";
        input.type = opts.confirmLabel;
        input.placeholder = opts.placeholder || "Type to confirm";
        input.style.marginBottom = "1rem";
        modal.appendChild(input);

        const actions = document.createElement("div");
        actions.className = "modal-actions";

        const cancel = button("Cancel", "btn btn-ghost", function () {
          close(false);
        });
        const confirm = button(opts.confirmLabel, "btn btn-danger", function () {
          if (input.value.trim().toUpperCase() !== String(opts.confirmLabel).toUpperCase()) {
            SoftKVM.toast("Type " + opts.confirmLabel + " to confirm", "warning");
            return;
          }
          close(true);
        });
        actions.appendChild(cancel);
        actions.appendChild(confirm);
        modal.appendChild(actions);
        setTimeout(function () {
          input.focus();
        }, 40);
      } else {
        const actions = document.createElement("div");
        actions.className = "modal-actions";
        actions.appendChild(
          button("Cancel", "btn btn-ghost", function () {
            close(false);
          })
        );
        actions.appendChild(
          button(opts.confirmText || "Confirm", "btn " + (opts.danger ? "btn-danger" : "btn-primary"), function () {
            close(true);
          })
        );
        modal.appendChild(actions);
      }

      backdrop.appendChild(modal);
      document.body.appendChild(backdrop);

      function button(label, className, handler) {
        const el = document.createElement("button");
        el.type = "button";
        el.className = className;
        el.textContent = label;
        el.addEventListener("click", handler);
        return el;
      }

      function close(result) {
        backdrop.remove();
        document.removeEventListener("keydown", onKey);
        resolve(result);
      }

      function onKey(event) {
        if (event.key === "Escape") close(false);
      }

      document.addEventListener("keydown", onKey);
      backdrop.addEventListener("click", function (event) {
        if (event.target === backdrop) close(false);
      });
    });
  };

  /* ------------------------------------------------------------------ *
   * Theme + wallpaper
   * ------------------------------------------------------------------ */
  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("softkvm.theme", theme);
    } catch (err) {
      /* storage disabled - not fatal */
    }
  }

  function applyWallpaper(id) {
    if (!id) return;
    document.documentElement.setAttribute("data-wallpaper", id);
    const url = "/static/img/wallpapers/" + id + ".jpg";
    document.documentElement.style.setProperty("--wallpaper-image", 'url("' + url + '")');
  }

  SoftKVM.applyTheme = applyTheme;
  SoftKVM.applyWallpaper = applyWallpaper;

  const themeToggle = document.getElementById("themeToggle");
  if (themeToggle) {
    themeToggle.addEventListener("click", function () {
      const next =
        document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      applyTheme(next);
      SoftKVM.post("/api/settings", { settings: { theme: next } }).catch(function () {});
    });
  }

  /* ------------------------------------------------------------------ *
   * Sidebar (mobile slide-over)
   * ------------------------------------------------------------------ */
  const sidebar = document.getElementById("sidebar");
  const sidebarScrim = document.getElementById("sidebarScrim");
  const sidebarToggle = document.getElementById("sidebarToggle");

  function setSidebar(open) {
    if (!sidebar) return;
    sidebar.classList.toggle("is-open", open);
    if (sidebarScrim) sidebarScrim.hidden = !open;
    if (sidebarToggle) sidebarToggle.setAttribute("aria-expanded", open ? "true" : "false");
  }
  SoftKVM.setSidebar = setSidebar;

  if (sidebarToggle) {
    sidebarToggle.addEventListener("click", function () {
      setSidebar(!sidebar.classList.contains("is-open"));
    });
  }
  if (sidebarScrim) {
    sidebarScrim.addEventListener("click", function () {
      setSidebar(false);
    });
  }

  /* ------------------------------------------------------------------ *
   * Command palette
   * ------------------------------------------------------------------ */
  const PALETTE_ACTIONS = [
    { label: "Go to Dashboard", icon: "i-dashboard", href: "/" },
    { label: "Open KVM Display", icon: "i-monitor", href: "/kvm" },
    { label: "Open Controls", icon: "i-toggle", href: "/controls" },
    { label: "Open Schedule", icon: "i-calendar", href: "/schedule" },
    { label: "Open Settings", icon: "i-settings", href: "/settings" },
    { label: "Toggle theme", icon: "i-sun", run: function () { document.getElementById("themeToggle").click(); } },
    { label: "Lock workstation", icon: "i-lock", run: function () { return SoftKVM.post("/api/action", { action: "lock" }); } },
    { label: "Take a screenshot", icon: "i-camera", run: function () { window.open("/snapshot", "_blank"); } },
    { label: "Refresh telemetry", icon: "i-refresh", run: function () { return SoftKVM.get("/api/metrics?force=1"); } },
    { label: "Sign out", icon: "i-logout", href: "/logout" },
  ];

  const paletteBackdrop = document.getElementById("paletteBackdrop");
  const paletteInput = document.getElementById("paletteInput");
  const paletteList = document.getElementById("paletteList");

  function openPalette() {
    if (!paletteBackdrop) return;
    paletteBackdrop.hidden = false;
    paletteInput.value = "";
    renderPalette("");
    paletteInput.focus();
  }

  function closePalette() {
    if (!paletteBackdrop) return;
    paletteBackdrop.hidden = true;
  }

  function renderPalette(query) {
    if (!paletteList) return;
    const needle = (query || "").toLowerCase();
    const matches = PALETTE_ACTIONS.filter(function (item) {
      return item.label.toLowerCase().includes(needle);
    });
    paletteList.innerHTML = "";
    if (!matches.length) {
      const empty = document.createElement("li");
      empty.className = "palette-empty";
      empty.textContent = "No matching commands";
      paletteList.appendChild(empty);
      return;
    }
    matches.forEach(function (item) {
      const li = document.createElement("li");
      li.className = "palette-item";
      li.tabIndex = 0;
      li.innerHTML =
        '<svg class="icon"><use href="#' + item.icon + '"></use></svg><span></span>';
      li.querySelector("span").textContent = item.label;
      const activate = function () {
        closePalette();
        if (item.href) window.location.href = item.href;
        else if (item.run) {
          Promise.resolve(item.run())
            .then(function (result) {
              if (result && result.ok && result.message) {
                SoftKVM.toast(result.message, "success");
              }
            })
            .catch(function (err) {
              SoftKVM.toast(err.message, "error");
            });
        }
      };
      li.addEventListener("click", activate);
      li.addEventListener("keydown", function (event) {
        if (event.key === "Enter") activate();
      });
      paletteList.appendChild(li);
    });
  }

  const commandTrigger = document.getElementById("commandTrigger");
  if (commandTrigger) commandTrigger.addEventListener("click", openPalette);
  if (paletteInput) paletteInput.addEventListener("input", function () { renderPalette(paletteInput.value); });
  if (paletteBackdrop) {
    paletteBackdrop.addEventListener("click", function (event) {
      if (event.target === paletteBackdrop) closePalette();
    });
  }

  document.addEventListener("keydown", function (event) {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if (paletteBackdrop && paletteBackdrop.hidden) openPalette();
      else closePalette();
    }
    if (event.key === "Escape") closePalette();
  });

  /* ------------------------------------------------------------------ *
   * Sidebar widgets + global telemetry
   * ------------------------------------------------------------------ */
  function setMeter(key, percent) {
    const bar = document.getElementById("meter-" + key);
    if (bar) bar.style.width = Math.max(0, Math.min(100, percent)) + "%";
  }

  function setUsage(key, percent, label) {
    const value = document.getElementById("usage-" + key);
    if (value) value.textContent = label;
    setMeter(key, percent);
  }

  function renderStatus(metrics) {
    if (!metrics) return;
    const cpu = metrics.cpu || {};
    const memory = metrics.memory || {};
    const disk = metrics.disk || {};
    const network = metrics.network || {};
    const system = metrics.system || {};

    setUsage("cpu", cpu.percent || 0, (cpu.percent || 0).toFixed(0) + "%");
    setUsage("memory", memory.percent || 0, (memory.percent || 0).toFixed(0) + "%");
    setUsage("disk", disk.percent || 0, (disk.percent || 0).toFixed(0) + "%");

    const netPercent = Math.min(
      100,
      ((network.down_bytes_per_sec || 0) + (network.up_bytes_per_sec || 0)) / 1048576
    );
    setUsage("network", netPercent, netPercent.toFixed(0) + "%");

    const up = document.getElementById("trafficUp");
    const down = document.getElementById("trafficDown");
    if (up) up.textContent = network.up_human || "-";
    if (down) down.textContent = network.down_human || "-";

    const trafficNote = document.getElementById("trafficNote");
    if (trafficNote) {
      trafficNote.textContent =
        "Received " + SoftKVM.formatBytes(network.total_recv) +
        " · sent " + SoftKVM.formatBytes(network.total_sent);
    }

    const hostName = document.getElementById("hostName");
    const hostMeta = document.getElementById("hostMeta");
    if (hostName) hostName.textContent = system.hostname || "-";
    if (hostMeta) {
      hostMeta.textContent = [
        system.platform || "-",
        system.uptime_human ? "up " + system.uptime_human : "",
      ]
        .filter(Boolean)
        .join(" · ");
    }

    const updated = document.getElementById("widgetUpdated");
    if (updated && metrics.server_time) {
      updated.textContent = new Date(metrics.server_time).toLocaleTimeString();
    }
  }

  SoftKVM.renderStatus = renderStatus;

  async function poll() {
    try {
      const metrics = await SoftKVM.get("/api/metrics");
      SoftKVM.metrics = metrics;
      renderStatus(metrics);
      document.dispatchEvent(new CustomEvent("softkvm:metrics", { detail: metrics }));
    } catch (err) {
      const pill = document.getElementById("hostPill");
      if (pill) {
        pill.classList.add("is-error");
        pill.querySelector(".dot") && (pill.querySelector(".dot").style.background = "var(--casa-red)");
        pill.lastChild.textContent = " Offline";
      }
    }
  }

  SoftKVM.poll = poll;

  function startPolling(interval) {
    if (interval) SoftKVM.pollInterval = interval;
    if (SoftKVM.pollTimer) clearInterval(SoftKVM.pollTimer);
    SoftKVM.pollTimer = setInterval(poll, SoftKVM.pollInterval);
    poll();
  }
  SoftKVM.startPolling = startPolling;

  /* ------------------------------------------------------------------ *
   * Small DOM helpers
   * ------------------------------------------------------------------ */
  SoftKVM.el = function (id) {
    return document.getElementById(id);
  };

  SoftKVM.setText = function (id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
    return el;
  };

  SoftKVM.formatBytes = function (bytes) {
    if (bytes === null || bytes === undefined) return "-";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let value = Number(bytes);
    let index = 0;
    while (value >= 1024 && index < units.length - 1) {
      value /= 1024;
      index += 1;
    }
    return value.toFixed(index === 0 ? 0 : 1) + " " + units[index];
  };

  SoftKVM.setGauge = function (circleId, percent) {
    const circle = document.getElementById(circleId);
    if (!circle) return;
    const clamped = Math.max(0, Math.min(100, percent || 0));
    circle.style.strokeDashoffset = 251.2 - (clamped / 100) * 251.2;
  };

  /* ------------------------------------------------------------------ *
   * Sparkline / area chart (dependency free)
   * ------------------------------------------------------------------ */
  SoftKVM.sparkline = function (svg, values, options) {
    if (!svg) return;
    const opts = Object.assign({ color: "#0d6cf3", fill: true, max: 100 }, options || {});
    const points = (values || []).filter(function (v) { return typeof v === "number"; });
    if (points.length < 2) {
      svg.innerHTML = "";
      return;
    }
    const width = 100;
    const height = 100;
    const max = opts.max || Math.max.apply(null, points) || 1;
    const step = width / (points.length - 1);
    const coords = points.map(function (value, index) {
      const x = index * step;
      const y = height - Math.max(0, Math.min(1, value / max)) * height;
      return x.toFixed(2) + "," + y.toFixed(2);
    });
    const line = "M" + coords.join(" L");
    const area = line + " L" + width + "," + height + " L0," + height + " Z";
    const gradientId = "grad-" + Math.random().toString(36).slice(2, 9);
    svg.innerHTML =
      '<defs><linearGradient id="' + gradientId + '" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0%" stop-color="' + opts.color + '" stop-opacity="0.45"/>' +
      '<stop offset="100%" stop-color="' + opts.color + '" stop-opacity="0"/>' +
      '</linearGradient></defs>' +
      (opts.fill ? '<path d="' + area + '" fill="url(#' + gradientId + ')"/>' : "") +
      '<path d="' + line + '" fill="none" stroke="' + opts.color + '" stroke-width="2" ' +
      'vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"/>';
  };

  /* ------------------------------------------------------------------ *
   * Service worker registration
   * ------------------------------------------------------------------ */
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/static/sw.js").catch(function () {
        /* offline support is optional */
      });
    });
  }

  /* ------------------------------------------------------------------ *
   * Boot
   * ------------------------------------------------------------------ */
  let storedTheme = null;
  try {
    storedTheme = localStorage.getItem("softkvm.theme");
  } catch (err) {
    /* ignore */
  }
  if (storedTheme) applyTheme(storedTheme);

  startPolling();
  window.SoftKVM = SoftKVM;
})();
