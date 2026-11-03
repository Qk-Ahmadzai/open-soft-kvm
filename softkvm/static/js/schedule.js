/* SoftKVM - schedule page */
(function () {
  "use strict";

  const K = window.SoftKVM;
  const form = document.getElementById("taskForm");
  const actionSelect = document.getElementById("taskAction");
  const targetField = document.getElementById("targetField");
  let actions = {};

  async function loadActions() {
    if (!actionSelect) return;
    try {
      const payload = await K.get("/api/schedule");
      actions = (payload && payload.actions) || {};
      actionSelect.innerHTML = "";
      Object.keys(actions).forEach(function (key) {
        const option = document.createElement("option");
        option.value = key;
        option.textContent = actions[key];
        actionSelect.appendChild(option);
      });
    } catch (err) {
      /* leave the select empty */
    }
  }

  async function loadTasks() {
    const rows = document.getElementById("taskRows");
    if (!rows) return;
    try {
      const payload = await K.get("/api/schedule");
      const tasks = (payload && payload.tasks) || [];
      const count = document.getElementById("taskCount");
      if (count) count.textContent = tasks.length + " task" + (tasks.length === 1 ? "" : "s");

      if (!tasks.length) {
        rows.innerHTML =
          '<tr><td colspan="5" class="empty-row">No scheduled tasks yet</td></tr>';
        return;
      }
      rows.innerHTML = "";
      tasks.forEach(function (task) {
        const tr = document.createElement("tr");

        const label = document.createElement("td");
        label.innerHTML = "<strong></strong>";
        label.querySelector("strong").textContent = task.label || task.action;
        if (task.target) {
          const sub = document.createElement("div");
          sub.className = "row-sub";
          sub.textContent = task.target;
          label.appendChild(sub);
        }

        const time = document.createElement("td");
        time.className = "mono";
        time.textContent = task.at;

        const repeats = document.createElement("td");
        repeats.textContent = task.daily ? "Daily" : "Once";

        const lastRun = document.createElement("td");
        if (task.last_run) {
          lastRun.textContent = task.last_run;
          if (task.last_result) {
            const sub = document.createElement("div");
            sub.className = "row-sub";
            sub.textContent = task.last_result;
            lastRun.appendChild(sub);
          }
        } else {
          lastRun.textContent = "-";
        }

        const action = document.createElement("td");
        action.className = "num";
        const remove = document.createElement("button");
        remove.className = "btn btn-sm btn-danger";
        remove.type = "button";
        remove.innerHTML = '<svg class="icon icon-sm"><use href="#i-trash"></use></svg>';
        remove.setAttribute("aria-label", "Remove task");
        remove.addEventListener("click", async function () {
          const ok = await K.confirm({
            title: "Remove task?",
            message: 'The "' + (task.label || task.action) + '" task will be removed.',
            confirmText: "Remove",
            danger: true,
          });
          if (!ok) return;
          try {
            await K.del("/api/schedule/" + encodeURIComponent(task.id));
            K.toast("Task removed", "success");
            loadTasks();
          } catch (err) {
            K.toast(err.message, "error");
          }
        });
        action.appendChild(remove);

        tr.appendChild(label);
        tr.appendChild(time);
        tr.appendChild(repeats);
        tr.appendChild(lastRun);
        tr.appendChild(action);
        rows.appendChild(tr);
      });
    } catch (err) {
      rows.innerHTML = '<tr><td colspan="5" class="empty-row">Schedule unavailable</td></tr>';
    }
  }

  if (actionSelect) {
    actionSelect.addEventListener("change", function () {
      if (targetField) {
        targetField.hidden = actionSelect.value !== "wake_on_lan";
      }
    });
  }

  if (form) {
    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      const payload = {
        action: actionSelect.value,
        at: document.getElementById("taskTime").value,
        label: document.getElementById("taskLabel").value.trim(),
        daily: document.getElementById("taskDaily").checked,
        target: document.getElementById("taskTarget").value.trim(),
      };
      try {
        await K.post("/api/schedule", payload);
        K.toast("Task scheduled", "success");
        form.reset();
        document.getElementById("taskDaily").checked = true;
        loadTasks();
      } catch (err) {
        K.toast(err.message, "error");
      }
    });
  }

  loadActions().then(loadTasks);
})();
