/* SoftKVM - KVM stream controller.
   Handles pointer mapping, keyboard forwarding, the on-screen keyboard and the
   clipboard bridge. */
(function () {
  "use strict";

  const K = window.SoftKVM;
  const img = document.getElementById("streamImg");
  const stage = document.getElementById("kvmStage");
  const overlay = document.getElementById("kvmOverlay");
  const osk = document.getElementById("osk");
  const nativeInput = document.getElementById("nativeMobileFocus");
  const statusLabel = document.getElementById("kvmStatus");

  let currentMonitor = null;
  let streamToken = 0;
  let dragging = false;
  let dragStart = null;
  let lastClick = 0;
  let keyboardCaptured = false;

  /* ------------------------------------------------------------------ *
   * Stream lifecycle
   * ------------------------------------------------------------------ */
  function connect() {
    const token = ++streamToken;
    overlay.hidden = false;
    statusLabel.textContent = "Connecting...";
    const url = "/stream?monitor=" + encodeURIComponent(currentMonitor || "") + "&t=" + Date.now();
    img.onload = function () {
      if (token !== streamToken) return;
      overlay.hidden = true;
      statusLabel.textContent = "Live";
    };
    img.onerror = function () {
      if (token !== streamToken) return;
      overlay.hidden = true;
      statusLabel.textContent = "Stream unavailable";
    };
    img.src = url;
  }

  /* ------------------------------------------------------------------ *
   * Pointer mapping
   *
   * The stage uses object-fit: contain, so the image can be letterboxed.
   * Mapping must use the *rendered image* box, not the element box.
   * ------------------------------------------------------------------ */
  function imageBox() {
    const rect = img.getBoundingClientRect();
    const naturalWidth = img.naturalWidth || 16;
    const naturalHeight = img.naturalHeight || 9;
    const imageRatio = naturalWidth / naturalHeight;
    const boxRatio = rect.width / rect.height;

    let width;
    let height;
    if (imageRatio > boxRatio) {
      width = rect.width;
      height = rect.width / imageRatio;
    } else {
      height = rect.height;
      width = rect.height * imageRatio;
    }
    return {
      left: rect.left + (rect.width - width) / 2,
      top: rect.top + (rect.height - height) / 2,
      width: width,
      height: height,
    };
  }

  function normalise(event) {
    const box = imageBox();
    if (!box.width || !box.height) return null;
    const x = (event.clientX - box.left) / box.width;
    const y = (event.clientY - box.top) / box.height;
    if (x < -0.02 || x > 1.02 || y < -0.02 || y > 1.02) return null;
    return { x: Math.min(1, Math.max(0, x)), y: Math.min(1, Math.max(0, y)) };
  }

  function send(payload) {
    return K.post("/api/input", payload).catch(function (err) {
      K.toast(err.message, "error");
    });
  }

  img.addEventListener("contextmenu", function (event) {
    event.preventDefault();
    const point = normalise(event);
    if (!point) return;
    send({ type: "click", x: point.x, y: point.y, button: "right" });
  });

  img.addEventListener("mousedown", function (event) {
    if (event.button !== 0) return;
    const point = normalise(event);
    if (!point) return;
    dragging = true;
    dragStart = point;
  });

  window.addEventListener("mouseup", function (event) {
    if (!dragging || event.button !== 0) return;
    dragging = false;
    const point = normalise(event);
    if (!point || !dragStart) {
      dragStart = null;
      return;
    }
    const distance = Math.abs(point.x - dragStart.x) + Math.abs(point.y - dragStart.y);
    if (distance > 0.012) {
      send({
        type: "drag",
        x1: dragStart.x,
        y1: dragStart.y,
        x2: point.x,
        y2: point.y,
      });
    } else {
      const now = Date.now();
      const count = now - lastClick < 400 ? 2 : 1;
      lastClick = now;
      send({ type: "click", x: point.x, y: point.y, button: "left", count: count });
    }
    dragStart = null;
  });

  img.addEventListener(
    "wheel",
    function (event) {
      event.preventDefault();
      const delta = event.deltaY > 0 ? -120 : 120;
      send({ type: "scroll", dy: delta });
    },
    { passive: false }
  );

  /* --- touch (mobile) --- */
  img.addEventListener(
    "touchstart",
    function (event) {
      if (!event.touches.length) return;
      const touch = event.touches[0];
      const point = normalise(touch);
      dragging = true;
      dragStart = point;
    },
    { passive: true }
  );

  img.addEventListener(
    "touchmove",
    function (event) {
      if (!dragging || !event.touches.length) return;
      event.preventDefault();
      const point = normalise(event.touches[0]);
      if (!point || !dragStart) return;
      if (Math.abs(point.x - dragStart.x) + Math.abs(point.y - dragStart.y) > 0.01) {
        dragging = false;
        send({
          type: "drag",
          x1: dragStart.x,
          y1: dragStart.y,
          x2: point.x,
          y2: point.y,
        });
        dragStart = null;
      }
    },
    { passive: false }
  );

  img.addEventListener(
    "touchend",
    function (event) {
      if (!dragging) return;
      dragging = false;
      if (!dragStart) return;
      const touch = (event.changedTouches && event.changedTouches[0]) || null;
      if (!touch) {
        dragStart = null;
        return;
      }
      const point = normalise(touch);
      if (point) {
        send({ type: "click", x: point.x, y: point.y, button: "left" });
      }
      dragStart = null;
    },
    { passive: true }
  );

  /* ------------------------------------------------------------------ *
   * Keyboard forwarding
   * ------------------------------------------------------------------ */
  const PASSTHROUGH_KEYS = {
    Backspace: 1,
    Enter: 1,
    Tab: 1,
    Escape: 1,
    ArrowUp: 1,
    ArrowDown: 1,
    ArrowLeft: 1,
    ArrowRight: 1,
    Home: 1,
    End: 1,
    PageUp: 1,
    PageDown: 1,
    Delete: 1,
    Insert: 1,
  };

  function inTextField(target) {
    if (!target) return false;
    const tag = target.tagName;
    return (
      tag === "INPUT" ||
      tag === "TEXTAREA" ||
      tag === "SELECT" ||
      target.isContentEditable === true
    );
  }

  document.addEventListener("keydown", function (event) {
    if (document.activeElement === nativeInput) return;
    if (inTextField(event.target)) return;

    // Escape releases keyboard capture and returns focus to the page.
    if (event.key === "Escape" && keyboardCaptured) {
      keyboardCaptured = false;
      statusLabel.textContent = "Live";
      return;
    }

    if (!keyboardCaptured && event.key !== "Tab") return;
    if (event.ctrlKey || event.metaKey || event.altKey) return;

    const modifiers = [];
    if (event.shiftKey) modifiers.push("shift");
    if (event.ctrlKey) modifiers.push("ctrl");

    if (event.key.length === 1 || PASSTHROUGH_KEYS[event.key]) {
      event.preventDefault();
      send({
        type: "key",
        key: event.key,
        modifiers: modifiers,
      });
    }
  });

  // Clicking the stream captures the keyboard for typing.
  img.addEventListener("click", function () {
    if (!keyboardCaptured) {
      keyboardCaptured = true;
      statusLabel.textContent = "Live - keyboard captured";
      K.toast("Keyboard captured. Press Esc to release.", "info");
    }
  });

  document.addEventListener("click", function (event) {
    if (!img.contains(event.target) && !inTextField(event.target)) {
      if (keyboardCaptured) {
        keyboardCaptured = false;
        statusLabel.textContent = "Live";
      }
    }
  });

  /* --- mobile soft keyboard bridge --- */
  if (nativeInput) {
    nativeInput.addEventListener("input", function (event) {
      if (event.data) {
        send({ type: "key", key: event.data });
      }
      nativeInput.value = "";
    });
    nativeInput.addEventListener("keydown", function (event) {
      if (event.key === "Backspace" || event.key === "Enter") {
        event.preventDefault();
        send({ type: "key", key: event.key });
        nativeInput.value = "";
      }
    });
  }

  /* ------------------------------------------------------------------ *
   * On-screen keyboard
   * ------------------------------------------------------------------ */
  document.querySelectorAll(".osk-key").forEach(function (button) {
    const press = function (event) {
      event.preventDefault();
      button.classList.add("is-down");
      const key = button.getAttribute("data-k");
      send({ type: "key", key: key });
    };
    button.addEventListener("pointerdown", press);
    button.addEventListener("pointerup", function () {
      button.classList.remove("is-down");
    });
    button.addEventListener("pointercancel", function () {
      button.classList.remove("is-down");
    });
    button.addEventListener("pointerleave", function () {
      button.classList.remove("is-down");
    });
  });

  const btnKeyboard = document.getElementById("btnKeyboard");
  if (btnKeyboard) {
    btnKeyboard.addEventListener("click", function () {
      const open = osk.classList.toggle("is-open");
      btnKeyboard.classList.toggle("is-active", open);
      btnKeyboard.setAttribute("aria-pressed", open ? "true" : "false");
    });
  }

  const btnNative = document.getElementById("btnNativeKeyboard");
  if (btnNative && nativeInput) {
    btnNative.addEventListener("click", function () {
      nativeInput.focus();
      if (nativeInput.setSelectionRange) {
        try {
          nativeInput.setSelectionRange(0, 0);
        } catch (err) {
          /* some mobile browsers reject selection on hidden inputs */
        }
      }
      K.toast("Type on your phone keyboard to send keys to the host.", "info");
    });
  }

  /* ------------------------------------------------------------------ *
   * Toolbar
   * ------------------------------------------------------------------ */
  const btnRefresh = document.getElementById("btnRefresh");
  if (btnRefresh) {
    btnRefresh.addEventListener("click", function () {
      connect();
      K.toast("Reconnecting to the stream...", "info");
    });
  }

  const btnScreenshot = document.getElementById("btnScreenshot");
  if (btnScreenshot) {
    btnScreenshot.addEventListener("click", function () {
      const url = "/snapshot?monitor=" + encodeURIComponent(currentMonitor || "") + "&t=" + Date.now();
      const link = document.createElement("a");
      link.href = url;
      link.download = "softkvm-capture.jpg";
      document.body.appendChild(link);
      link.click();
      link.remove();
      K.toast("Screenshot saved", "success");
    });
  }

  const btnFullscreen = document.getElementById("btnFullscreen");
  if (btnFullscreen) {
    btnFullscreen.addEventListener("click", function () {
      if (document.fullscreenElement) {
        document.exitFullscreen();
      } else if (stage.requestFullscreen) {
        stage.requestFullscreen();
      }
    });
  }

  const btnFit = document.getElementById("btnFit");
  if (btnFit) {
    btnFit.addEventListener("click", function () {
      const current = img.style.objectFit || "contain";
      img.style.objectFit = current === "contain" ? "cover" : "contain";
      btnFit.textContent = img.style.objectFit === "contain" ? "Fit to window" : "Fill window";
    });
  }

  /* ------------------------------------------------------------------ *
   * Monitors
   * ------------------------------------------------------------------ */
  async function loadMonitors() {
    const select = document.getElementById("monitorSelect");
    if (!select) return;
    try {
      const payload = await K.get("/api/monitors");
      const monitors = (payload && payload.monitors) || [];
      currentMonitor = payload.active;
      select.innerHTML = "";
      monitors.forEach(function (monitor) {
        const option = document.createElement("option");
        option.value = monitor.index;
        option.textContent =
          monitor.label + " - " + monitor.width + "x" + monitor.height +
          (monitor.is_primary ? " (primary)" : "");
        if (monitor.index === payload.active) option.selected = true;
        select.appendChild(option);
      });
      const active = monitors.find(function (m) { return m.index === payload.active; });
      K.setText(
        "monitorLabel",
        active ? active.width + "x" + active.height : "Display " + payload.active
      );
    } catch (err) {
      K.setText("monitorLabel", "Unavailable");
    }
  }

  const monitorSelect = document.getElementById("monitorSelect");
  if (monitorSelect) {
    monitorSelect.addEventListener("change", async function () {
      const index = parseInt(monitorSelect.value, 10);
      try {
        await K.post("/api/monitor", { index: index });
        currentMonitor = index;
        connect();
        K.toast("Switched to display " + index, "success");
        loadMonitors();
      } catch (err) {
        K.toast(err.message, "error");
      }
    });
  }

  /* ------------------------------------------------------------------ *
   * Quality controls
   * ------------------------------------------------------------------ */
  const qualitySelect = document.getElementById("qualitySelect");
  const fpsSelect = document.getElementById("fpsSelect");
  const btnApply = document.getElementById("btnApplyQuality");

  async function loadSettings() {
    try {
      const payload = await K.get("/api/settings");
      const settings = (payload && payload.settings) || {};
      if (qualitySelect) qualitySelect.value = String(settings.stream_quality);
      if (fpsSelect) fpsSelect.value = String(settings.stream_fps);
      K.setText(
        "qualityNote",
        settings.stream_width + "x" + settings.stream_height + " @ " + settings.stream_fps + " fps"
      );
    } catch (err) {
      /* keep defaults */
    }
  }

  if (btnApply) {
    btnApply.addEventListener("click", async function () {
      const patch = {
        stream_quality: parseInt(qualitySelect.value, 10),
        stream_fps: parseInt(fpsSelect.value, 10),
      };
      try {
        await K.post("/api/settings", { settings: patch });
        K.toast("Stream settings applied", "success");
        setTimeout(connect, 400);
        loadSettings();
      } catch (err) {
        K.toast(err.message, "error");
      }
    });
  }

  /* ------------------------------------------------------------------ *
   * Clipboard
   * ------------------------------------------------------------------ */
  const clipboardText = document.getElementById("clipboardText");
  const clipboardStatus = document.getElementById("clipboardStatus");

  const btnClipboardRead = document.getElementById("btnClipboardRead");
  if (btnClipboardRead) {
    btnClipboardRead.addEventListener("click", async function () {
      try {
        const payload = await K.get("/api/clipboard");
        if (clipboardText) clipboardText.value = payload.text || "";
        K.setText("clipboardStatus", "Read " + (payload.text || "").length + " chars");
        K.toast("Host clipboard loaded", "success");
      } catch (err) {
        K.toast(err.message, "error");
      }
    });
  }

  const btnClipboardWrite = document.getElementById("btnClipboardWrite");
  if (btnClipboardWrite) {
    btnClipboardWrite.addEventListener("click", async function () {
      if (!clipboardText) return;
      try {
        const result = await K.post("/api/clipboard", { text: clipboardText.value });
        K.setText("clipboardStatus", "Pushed " + clipboardText.value.length + " chars");
        K.toast(result.message || "Clipboard updated", "success");
      } catch (err) {
        K.toast(err.message, "error");
      }
    });
  }

  /* ------------------------------------------------------------------ *
   * Boot
   * ------------------------------------------------------------------ */
  loadMonitors().then(connect);
  loadSettings();
})();
