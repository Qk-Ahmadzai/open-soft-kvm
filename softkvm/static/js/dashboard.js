/* SoftKVM - dashboard page */
(function () {
  "use strict";

  const K = window.SoftKVM;
  let processSort = "cpu";

  function renderOverview(metrics) {
    const cpu = metrics.cpu || {};
    const memory = metrics.memory || {};
    const disk = metrics.disk || {};
    const network = metrics.network || {};
    const system = metrics.system || {};

    K.setText("cpuValue", (cpu.percent || 0).toFixed(0) + "%");
    K.setText("cpuCores", (cpu.physical_cores || 0) + " cores / " + (cpu.logical_cores || 0) + " threads");
    K.setText("cpuFreq", cpu.freq_ghz ? cpu.freq_ghz.toFixed(2) + " GHz" : cpu.model || "-");
    K.setGauge("cpuArc", cpu.percent);

    K.setText("memValue", (memory.percent || 0).toFixed(0) + "%");
    K.setText("memRaw", memory.used_human + " / " + memory.total_human);
    K.setText("memFree", memory.available_human + " available");
    K.setGauge("memArc", memory.percent);

    K.setText("diskValue", (disk.percent || 0).toFixed(0) + "%");
    K.setText("diskRaw", disk.used_human + " / " + disk.total_human);
    K.setText("diskFree", disk.free_human + " free");
    K.setGauge("diskArc", disk.percent);

    K.setText("netDown", network.down_human || "-");
    K.setText("netUp", network.up_human || "-");
    K.setText("netTotal", K.formatBytes(network.total_recv));
    K.setText("netSent", K.formatBytes(network.total_sent));
    const ips = (network.host_ips || []).slice(0, 2);
    K.setText("netAddresses", ips.length ? ips.join(", ") : "-");

    K.setText("overviewNote", metrics.server_time ? new Date(metrics.server_time).toLocaleTimeString() : "-");

    // system panel
    K.setText("sysHost", system.hostname || "-");
    K.setText("sysOs", system.platform || "-");
    K.setText("sysCpu", system.cpu_model || "-");
    K.setText("sysGpu", system.gpu || "Not reported");
    K.setText("sysUptime", system.uptime_human || "-");
    K.setText("sysBoot", system.boot_time ? new Date(system.boot_time).toLocaleString() : "-");
    K.setText("sysIps", (network.host_ips || []).join(", ") || "-");

    renderSensors(metrics.sensors || {});
  }

  function renderSensors(sensors) {
    const block = document.getElementById("sensorBlock");
    const list = document.getElementById("sensorList");
    if (!block || !list) return;

    const rows = [];
    (sensors.temperatures || []).forEach(function (temp) {
      rows.push({
        title: temp.name,
        sub: temp.group,
        value: temp.current + " °C",
        warn: temp.high && temp.current >= temp.high,
      });
    });
    (sensors.fans || []).forEach(function (fan) {
      rows.push({ title: fan.name, sub: "Fan", value: fan.rpm + " RPM" });
    });
    if (sensors.battery) {
      rows.push({
        title: "Battery",
        sub: sensors.battery.plugged ? "Charging" : "On battery",
        value: sensors.battery.percent + "%",
      });
    }

    if (!rows.length) {
      block.hidden = true;
      return;
    }
    block.hidden = false;
    list.innerHTML = "";
    rows.forEach(function (row) {
      const el = document.createElement("div");
      el.className = "row-item";
      const main = document.createElement("div");
      main.className = "row-main";
      const title = document.createElement("span");
      title.className = "row-title";
      title.textContent = row.title;
      const sub = document.createElement("span");
      sub.className = "row-sub";
      sub.textContent = row.sub;
      main.appendChild(title);
      main.appendChild(sub);
      const value = document.createElement("span");
      value.className = row.warn ? "tag tag-warn" : "tag";
      value.textContent = row.value;
      el.appendChild(main);
      el.appendChild(value);
      list.appendChild(el);
    });
  }

  function renderCharts(history) {
    if (!history) return;
    const cpu = (history.cpu && history.cpu.v) || [];
    const mem = (history.ram && history.ram.v) || [];
    const down = (history.net_down && history.net_down.v) || [];
    const up = (history.net_up && history.net_up.v) || [];

    K.sparkline(document.getElementById("cpuSpark"), cpu, { color: "#0d6cf3", max: 100 });
    K.sparkline(document.getElementById("memSpark"), mem, { color: "#a855f7", max: 100 });

    K.sparkline(document.getElementById("chartCpu"), cpu, { color: "#0d6cf3", max: 100 });
    K.sparkline(document.getElementById("chartMem"), mem, { color: "#a855f7", max: 100 });

    const netMax = Math.max.apply(null, down.concat(up).concat([1]));
    const net = K.sparkline(document.getElementById("chartNet"), down, {
      color: "#38bdf8",
      max: netMax,
      fill: false,
    });
    // overlay the upload series on the same axis
    if (net && up.length > 1) {
      const svg = document.getElementById("chartNet");
      const points = up
        .map(function (value, index) {
          const x = (index / (up.length - 1)) * 100;
          const y = 100 - Math.max(0, Math.min(1, value / netMax)) * 100;
          return x.toFixed(2) + "," + y.toFixed(2);
        })
        .join(" L");
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", "M" + points);
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", "#22c55e");
      path.setAttribute("stroke-width", "2");
      path.setAttribute("vector-effect", "non-scaling-stroke");
      path.setAttribute("stroke-linejoin", "round");
      svg.appendChild(path);
    }

    K.setText("chartCpuNow", cpu.length ? cpu[cpu.length - 1].toFixed(0) + "%" : "-");
    K.setText("chartMemNow", mem.length ? mem[mem.length - 1].toFixed(0) + "%" : "-");
    K.setText(
      "chartNetNow",
      down.length ? K.formatBytes(down[down.length - 1]) + "/s down" : "-"
    );
  }

  function renderProcesses(payload) {
    const rows = document.getElementById("processRows");
    if (!rows) return;
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
      kill.addEventListener("click", function () {
        confirmKill(proc);
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
  }

  async function confirmKill(proc) {
    const ok = await K.confirm({
      title: "Terminate process?",
      message:
        proc.name + " (PID " + proc.pid + ") will be terminated. Unsaved work in that " +
        "application will be lost.",
      confirmText: "Terminate",
      danger: true,
    });
    if (!ok) return;
    try {
      const result = await K.post("/api/kill", { pid: proc.pid });
      K.toast(result.message || "Process terminated", "success");
      refreshProcesses();
    } catch (err) {
      K.toast(err.message, "error");
    }
  }

  async function refreshProcesses() {
    try {
      const payload = await K.get("/api/processes?limit=12&sort=" + processSort);
      renderProcesses(payload);
    } catch (err) {
      const rows = document.getElementById("processRows");
      if (rows) {
        rows.innerHTML = '<tr><td colspan="6" class="empty-row">Process list unavailable</td></tr>';
      }
    }
  }

  function renderVolumes(disk) {
    const rows = document.getElementById("volumeRows");
    if (!rows) return;
    const partitions = (disk && disk.partitions) || [];
    if (!partitions.length) {
      rows.innerHTML = '<tr><td colspan="7" class="empty-row">No volumes reported</td></tr>';
      return;
    }
    rows.innerHTML = "";
    partitions.forEach(function (part) {
      const tr = document.createElement("tr");
      const cells = [
        { text: part.mountpoint, cls: "mono" },
        { text: part.device || "-", cls: "mono" },
        { text: part.fstype || "-" },
        { text: part.used_human, cls: "num" },
        { text: part.free_human, cls: "num" },
        { text: part.total_human, cls: "num" },
      ];
      cells.forEach(function (cell) {
        const td = document.createElement("td");
        if (cell.cls) td.className = cell.cls;
        td.textContent = cell.text;
        tr.appendChild(td);
      });
      const usage = document.createElement("td");
      usage.className = "num";
      usage.textContent = part.percent + "%";
      tr.appendChild(usage);
      rows.appendChild(tr);
    });
  }

  async function loadApplications() {
    const grid = document.getElementById("appGrid");
    if (!grid) return;
    try {
      const payload = await K.get("/api/applications");
      const apps = (payload && payload.applications) || [];
      if (!apps.length) {
        grid.innerHTML = '<p class="section-note">No launchable applications on this host.</p>';
        return;
      }
      grid.innerHTML = "";
      apps.forEach(function (app) {
        const tile = document.createElement("button");
        tile.type = "button";
        tile.className = "app-tile";
        tile.innerHTML =
          '<span class="app-icon"><svg class="icon"><use href="#i-' + app.icon + '"></use></svg></span>' +
          "<span></span>";
        tile.querySelector("span:last-child").textContent = app.label;
        tile.addEventListener("click", function () {
          launch(app);
        });
        grid.appendChild(tile);
      });
    } catch (err) {
      grid.innerHTML = '<p class="section-note">Applications unavailable</p>';
    }
  }

  async function launch(app) {
    try {
      const result = await K.post("/api/launch", { app: app.id });
      K.toast(result.message || "Launched", "success");
    } catch (err) {
      K.toast(err.message, "error");
    }
  }

  document.addEventListener("softkvm:metrics", function (event) {
    const metrics = event.detail;
    renderOverview(metrics);
    renderCharts(metrics.history);
    renderVolumes(metrics.disk);
  });

  document.addEventListener("click", function (event) {
    const button = event.target.closest("[data-sort]");
    if (!button) return;
    const group = button.parentElement;
    group.querySelectorAll("button").forEach(function (b) {
      b.classList.remove("is-active");
    });
    button.classList.add("is-active");
    processSort = button.getAttribute("data-sort");
    refreshProcesses();
  });

  loadApplications();
  refreshProcesses();
  setInterval(refreshProcesses, 8000);
})();
