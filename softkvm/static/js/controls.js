/* SoftKVM - controls page */
(function () {
  "use strict";

  const K = window.SoftKVM;
  let processSort = "cpu";

  async function runAction(action) {
    try {
      const result = await K.post("/api/action", { action: action });
      K.toast(result.message || "Done", result.ok === false ? "warning" : "success");
      refreshWifi();
    } catch (err) {
      K.toast(err.message, "error");
    }
  }

  async function confirmPower(action) {
    const label = action === "reboot" ? "restart" : "shut down";
    const ok = await K.confirm({
      title: "Confirm " + label,
      message:
        "This will " + label + " the host machine in 3 seconds. " +
        "Anyone using this machine will be disconnected immediately.",
      confirmText: "Yes, " + label,
      danger: true,
    });
    if (!ok) return;
    await runAction(action);
  }

  async function refreshWifi() {
    const label = document.getElementById("wifiState");
    if (!label) return;
    try {
      const metrics = await K.get("/api/metrics");
      const active = (metrics.network.interfaces || []).filter(function (iface) {
        return iface.ipv4 && iface.up;
      });
      const names = active.map(function (iface) {
        return iface.name + " (" + iface.ipv4 + ")";
      });
      label.textContent = names.length ? names.join(", ") : "No active interface";
    } catch (err) {
      label.textContent = "Status unavailable";
    }
  }

  async function loadApplications() {
    const grid = document.getElementById("controlApps");
    if (!grid) return;
    try {
      const payload = await K.get("/api/applications");
      const apps = (payload && payload.applications) || [];
      grid.innerHTML = "";
      if (!apps.length) {
        grid.innerHTML = '<p class="section-note">No launchable applications on this host.</p>';
        return;
      }
      apps.forEach(function (app) {
        const tile = document.createElement("button");
        tile.type = "button";
        tile.className = "app-tile";
        tile.innerHTML =
          '<span class="app-icon"><svg class="icon"><use href="#i-' + app.icon + '"></use></svg></span>' +
          "<span></span>";
        tile.querySelector("span:last-child").textContent = app.label;
        tile.addEventListener("click", async function () {
          try {
            const result = await K.post("/api/launch", { app: app.id });
            K.toast(result.message || "Launched", "success");
          } catch (err) {
            K.toast(err.message, "error");
          }
        });
        grid.appendChild(tile);
      });
    } catch (err) {
      grid.innerHTML = '<p class="section-note">Applications unavailable</p>';
    }
  }

  async function loadProcesses() {
    const rows = document.getElementById("controlProcesses");
    if (!rows) return;
    try {
      const payload = await K.get("/api/processes?limit=15&sort=" + processSort);
      const processes = (payload && payload.processes) || [];
      if (!processes.length) {
        rows.innerHTML = '<tr><td colspan="6" class="empty-row">No processes reported</td></tr>';
        return;
      }
      rows.innerHTML = "";
      processes.forEach(function (proc) {
        const tr = document.createElement("tr");

        const name = document.createElement("td");
        name.innerHTML = "<strong></strong>";
        name.querySelector("strong").textContent = proc.name;

        const user = document.createElement("td");
        user.textContent = proc.username;

        const pid = document.createElement("td");
        pid.className = "num mono";
        pid.textContent = proc.pid;

        const cpu = document.createElement("td");
        cpu.className = "num";
        cpu.textContent = proc.cpu + "%";

        const mem = document.createElement("td");
        mem.className = "num";
        mem.textContent = proc.memory + "%";

        const action = document.createElement("td");
        action.className = "num";
        const kill = document.createElement("button");
        kill.className = "btn btn-sm btn-danger";
        kill.type = "button";
        kill.textContent = "End";
        kill.addEventListener("click", async function () {
          const ok = await K.confirm({
            title: "Terminate process?",
            message: proc.name + " (PID " + proc.pid + ") will be terminated.",
            confirmText: "Terminate",
            danger: true,
          });
          if (!ok) return;
          try {
            const result = await K.post("/api/kill", { pid: proc.pid });
            K.toast(result.message || "Terminated", "success");
            loadProcesses();
          } catch (err) {
            K.toast(err.message, "error");
          }
        });
        action.appendChild(kill);

        tr.appendChild(name);
        tr.appendChild(user);
        tr.appendChild(pid);
        tr.appendChild(cpu);
        tr.appendChild(mem);
        tr.appendChild(action);
        rows.appendChild(tr);
      });
    } catch (err) {
      rows.innerHTML = '<tr><td colspan="6" class="empty-row">Process list unavailable</td></tr>';
    }
  }

  async function loadAudit() {
    const rows = document.getElementById("auditRows");
    if (!rows) return;
    try {
      const payload = await K.get("/api/audit?limit=25");
      const events = (payload && payload.events) || [];
      if (!events.length) {
        rows.innerHTML = '<tr><td colspan="4" class="empty-row">No activity recorded yet</td></tr>';
        return;
      }
      rows.innerHTML = "";
      events.forEach(function (event) {
        const tr = document.createElement("tr");
        const time = document.createElement("td");
        time.className = "mono";
        time.textContent = new Date(event.ts).toLocaleString();
        const action = document.createElement("td");
        action.textContent = event.action;
        const actor = document.createElement("td");
        actor.className = "mono";
        actor.textContent = event.actor;
        const result = document.createElement("td");
        const tag = document.createElement("span");
        tag.className = event.ok ? "tag tag-ok" : "tag tag-warn";
        tag.textContent = event.ok ? "OK" : "Failed";
        result.appendChild(tag);
        tr.appendChild(time);
        tr.appendChild(action);
        tr.appendChild(actor);
        tr.appendChild(result);
        rows.appendChild(tr);
      });
    } catch (err) {
      rows.innerHTML = '<tr><td colspan="4" class="empty-row">Activity unavailable</td></tr>';
    }
  }

  document.addEventListener("click", function (event) {
    const actionBtn = event.target.closest("[data-action]");
    if (actionBtn) {
      runAction(actionBtn.getAttribute("data-action"));
      return;
    }
    const powerBtn = event.target.closest("[data-power]");
    if (powerBtn) {
      confirmPower(powerBtn.getAttribute("data-power"));
      return;
    }
    const sortBtn = event.target.closest("[data-sort]");
    if (sortBtn) {
      sortBtn.parentElement.querySelectorAll("button").forEach(function (b) {
        b.classList.remove("is-active");
      });
      sortBtn.classList.add("is-active");
      processSort = sortBtn.getAttribute("data-sort");
      loadProcesses();
    }
  });

  const wolForm = document.getElementById("wolForm");
  if (wolForm) {
    wolForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      const mac = document.getElementById("wolMac").value.trim();
      try {
        const result = await K.post("/api/action", { action: "wake_on_lan", mac: mac });
        K.toast(result.message || "Magic packet sent", "success");
      } catch (err) {
        K.toast(err.message, "error");
      }
    });
  }

  loadApplications();
  loadProcesses();
  loadAudit();
  refreshWifi();
  setInterval(loadProcesses, 8000);
  setInterval(loadAudit, 10000);
})();
