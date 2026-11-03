/* SoftKVM - settings page */
(function () {
  "use strict";

  const K = window.SoftKVM;

  const WALLPAPERS = [
    { id: "night-sky", name: "Night Sky" },
    { id: "galaxy", name: "Galaxy" },
    { id: "deep-forest", name: "Deep Forest" },
    { id: "alpine-lake", name: "Alpine Lake" },
    { id: "highland", name: "Highland" },
    { id: "ocean-drift", name: "Ocean Drift" },
    { id: "turquoise-shore", name: "Turquoise Shore" },
    { id: "golden-field", name: "Golden Field" },
    { id: "monument-valley", name: "Monument Valley" },
  ];

  /* ------------------------------------------------------------------ *
   * Wallpaper picker
   * ------------------------------------------------------------------ */
  function renderWallpapers(active) {
    const grid = document.getElementById("wallpaperGrid");
    if (!grid) return;
    grid.innerHTML = "";
    WALLPAPERS.forEach(function (paper) {
      const option = document.createElement("button");
      option.type = "button";
      option.className = "wallpaper-option" + (paper.id === active ? " is-active" : "");
      option.setAttribute("aria-label", "Use " + paper.name + " wallpaper");
      option.innerHTML =
        '<img src="/static/img/wallpapers/' + paper.id + '.jpg" alt="' + paper.name + '" loading="lazy">' +
        "<span></span>";
      option.querySelector("span").textContent = paper.name;
      option.addEventListener("click", async function () {
        K.applyWallpaper(paper.id);
        grid.querySelectorAll(".wallpaper-option").forEach(function (el) {
          el.classList.remove("is-active");
        });
        option.classList.add("is-active");
        try {
          await K.post("/api/settings", { settings: { wallpaper: paper.id } });
          K.toast("Wallpaper updated", "success");
        } catch (err) {
          K.toast(err.message, "error");
        }
      });
      grid.appendChild(option);
    });
  }

  /* ------------------------------------------------------------------ *
   * Load current settings
   * ------------------------------------------------------------------ */
  async function loadSettings() {
    try {
      const payload = await K.get("/api/settings");
      const settings = (payload && payload.settings) || {};

      const themeSelect = document.getElementById("themeSelect");
      if (themeSelect) themeSelect.value = settings.theme || "dark";

      const widthSelect = document.getElementById("widthSelect");
      const heightSelect = document.getElementById("heightSelect");
      const fpsSetting = document.getElementById("fpsSetting");
      const qualitySetting = document.getElementById("qualitySetting");
      if (widthSelect) widthSelect.value = String(settings.stream_width);
      if (heightSelect) heightSelect.value = String(settings.stream_height);
      if (fpsSetting) fpsSetting.value = String(settings.stream_fps);
      if (qualitySetting) qualitySetting.value = String(settings.stream_quality);

      const blurToggle = document.getElementById("blurToggle");
      if (blurToggle) {
        blurToggle.checked = settings.wallpaper_blur !== false;
        document.documentElement.setAttribute(
          "data-blur",
          blurToggle.checked ? "on" : "off"
        );
      }

      K.applyWallpaper(settings.wallpaper);
      renderWallpapers(settings.wallpaper);
    } catch (err) {
      renderWallpapers(null);
    }
  }

  /* ------------------------------------------------------------------ *
   * Theme + blur
   * ------------------------------------------------------------------ */
  const themeSelect = document.getElementById("themeSelect");
  if (themeSelect) {
    themeSelect.addEventListener("change", async function () {
      const theme = themeSelect.value;
      K.applyTheme(theme);
      try {
        await K.post("/api/settings", { settings: { theme: theme } });
        K.toast("Theme updated", "success");
      } catch (err) {
        K.toast(err.message, "error");
      }
    });
  }

  const blurToggle = document.getElementById("blurToggle");
  if (blurToggle) {
    blurToggle.addEventListener("change", async function () {
      document.documentElement.setAttribute("data-blur", blurToggle.checked ? "on" : "off");
      try {
        await K.post("/api/settings", { settings: { wallpaper_blur: blurToggle.checked } });
      } catch (err) {
        /* visual state already applied */
      }
    });
  }

  /* ------------------------------------------------------------------ *
   * Streaming settings
   * ------------------------------------------------------------------ */
  const btnSaveStream = document.getElementById("btnSaveStream");
  if (btnSaveStream) {
    btnSaveStream.addEventListener("click", async function () {
      const patch = {
        stream_width: parseInt(document.getElementById("widthSelect").value, 10),
        stream_height: parseInt(document.getElementById("heightSelect").value, 10),
        stream_fps: parseInt(document.getElementById("fpsSetting").value, 10),
        stream_quality: parseInt(document.getElementById("qualitySetting").value, 10),
      };
      const monitorSelect = document.getElementById("monitorSetting");
      if (monitorSelect && monitorSelect.value) {
        patch.monitor_index = parseInt(monitorSelect.value, 10);
      }
      try {
        await K.post("/api/settings", { settings: patch });
        K.toast("Streaming settings saved", "success");
      } catch (err) {
        K.toast(err.message, "error");
      }
    });
  }

  /* ------------------------------------------------------------------ *
   * Monitors
   * ------------------------------------------------------------------ */
  async function loadMonitors() {
    const select = document.getElementById("monitorSetting");
    if (!select) return;
    try {
      const payload = await K.get("/api/monitors");
      select.innerHTML = "";
      (payload.monitors || []).forEach(function (monitor) {
        const option = document.createElement("option");
        option.value = monitor.index;
        option.textContent =
          monitor.label + " - " + monitor.width + "x" + monitor.height +
          (monitor.is_primary ? " (primary)" : "");
        if (monitor.index === payload.active) option.selected = true;
        select.appendChild(option);
      });
      K.setText(
        "aboutMonitors",
        (payload.monitors || []).map(function (m) {
          return m.width + "x" + m.height;
        }).join(", ")
      );
    } catch (err) {
      K.setText("aboutMonitors", "Unavailable");
    }
  }

  /* ------------------------------------------------------------------ *
   * Password change
   * ------------------------------------------------------------------ */
  const passwordForm = document.getElementById("passwordForm");
  if (passwordForm) {
    passwordForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      const current = document.getElementById("currentPassword").value;
      const next = document.getElementById("newPassword").value;
      const confirmValue = document.getElementById("confirmPassword").value;

      if (next !== confirmValue) {
        K.toast("The new passwords do not match", "error");
        return;
      }
      try {
        const result = await K.post("/api/settings/password", { current: current, new: next });
        K.toast(result.message || "Password updated", "success");
        passwordForm.reset();
      } catch (err) {
        K.toast(err.message, "error");
      }
    });
  }

  /* ------------------------------------------------------------------ *
   * About + access URLs
   * ------------------------------------------------------------------ */
  async function loadInfo() {
    try {
      const info = await K.get("/api/info");
      K.setText("aboutInput", info.input && info.input.available ? "Ready" : "Unavailable");
      K.setText(
        "aboutClipboard",
        info.clipboard && info.clipboard.available ? info.clipboard.backend : "Unavailable"
      );
      K.setText("aboutHost", info.platform || "-");

      const list = document.getElementById("urlList");
      if (list) {
        list.innerHTML = "";
        (info.urls || []).forEach(function (entry) {
          const row = document.createElement("div");
          row.className = "row-item";
          const main = document.createElement("div");
          main.className = "row-main";
          const title = document.createElement("span");
          title.className = "row-title mono";
          title.textContent = entry.url;
          const sub = document.createElement("span");
          sub.className = "row-sub";
          sub.textContent = entry.interface
            ? entry.interface + " · " + entry.address
            : entry.address;
          main.appendChild(title);
          main.appendChild(sub);
          const copy = document.createElement("button");
          copy.className = "btn btn-sm";
          copy.type = "button";
          copy.textContent = "Copy";
          copy.addEventListener("click", function () {
            if (navigator.clipboard && navigator.clipboard.writeText) {
              navigator.clipboard.writeText(entry.url).then(
                function () {
                  K.toast("Address copied", "success");
                },
                function () {
                  K.toast("Could not copy - " + entry.url, "warning");
                }
              );
            } else {
              K.toast(entry.url, "info");
            }
          });
          row.appendChild(main);
          row.appendChild(copy);
          list.appendChild(row);
        });
        if (!(info.urls || []).length) {
          list.innerHTML = '<p class="hint">No LAN address detected.</p>';
        }
      }
    } catch (err) {
      /* non fatal */
    }
  }

  loadSettings();
  loadMonitors();
  loadInfo();
})();
