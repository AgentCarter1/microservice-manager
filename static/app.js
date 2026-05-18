const state = {
  services: [],
  commands: {},
  selected: new Set(),
  tabs: [],
  current: null,
  initialized: false,
  logs: {},
  offsets: {},
  filter: "",
  terminalFilter: "",
  terminalFollow: {},
  terminalSelecting: false,
  modalMode: "add",
  contextService: null,
};

const els = {
  activeInstances: document.getElementById("activeInstances"),
  serviceList: document.getElementById("serviceList"),
  modalServiceList: document.getElementById("modalServiceList"),
  tabBar: document.getElementById("tabBar"),
  searchInput: document.getElementById("searchInput"),
  terminalFilterInput: document.getElementById("terminalFilterInput"),
  emptyTitle: document.getElementById("emptyTitle"),
  emptyDescription: document.getElementById("emptyDescription"),
  commandInput: document.getElementById("commandInput"),
  commandBar: document.getElementById("commandBar"),
  terminalCard: document.getElementById("terminalCard"),
  emptyState: document.getElementById("emptyState"),
  emptyStartSelectedBtn: document.getElementById("emptyStartSelectedBtn"),
  startSelectedBtn: document.getElementById("startSelectedBtn"),
  stopSelectedBtn: document.getElementById("stopSelectedBtn"),
  stopAllBtn: document.getElementById("stopAllBtn"),
  refreshBtn: document.getElementById("refreshBtn"),
  addServiceBtn: document.getElementById("addServiceBtn"),
  selectAllBtn: document.getElementById("selectAllBtn"),
  runBtn: document.getElementById("runBtn"),
  stopBtn: document.getElementById("stopBtn"),
  clearBtn: document.getElementById("clearBtn"),
  terminalTitle: document.getElementById("terminalTitle"),
  terminalState: document.getElementById("terminalState"),
  terminalOutput: document.getElementById("terminalOutput"),
  copyTerminalBtn: document.getElementById("copyTerminalBtn"),
  uptimeLabel: document.getElementById("uptimeLabel"),
  portBadge: document.getElementById("portBadge"),
  envBadge: document.getElementById("envBadge"),
  cpuValue: document.getElementById("cpuValue"),
  memValue: document.getElementById("memValue"),
  totalMetrics: document.getElementById("totalMetrics"),
  runtimeLabel: document.getElementById("runtimeLabel"),
  terminalBtn: document.getElementById("terminalBtn"),
  serviceModal: document.getElementById("serviceModal"),
  serviceModalTitle: document.getElementById("serviceModalTitle"),
  serviceModalDescription: document.getElementById("serviceModalDescription"),
  serviceModalActionBtn: document.getElementById("serviceModalActionBtn"),
  closeServiceModalBtn: document.getElementById("closeServiceModalBtn"),
  serviceForm: document.getElementById("serviceForm"),
  serviceNameInput: document.getElementById("serviceNameInput"),
  servicePathInput: document.getElementById("servicePathInput"),
  serviceCommandInput: document.getElementById("serviceCommandInput"),
  browseServicePathBtn: document.getElementById("browseServicePathBtn"),
  terminalContextMenu: document.getElementById("terminalContextMenu"),
  copySelectionMenuBtn: document.getElementById("copySelectionMenuBtn"),
  copyAllMenuBtn: document.getElementById("copyAllMenuBtn"),
  serviceContextMenu: document.getElementById("serviceContextMenu"),
  openServiceContextBtn: document.getElementById("openServiceContextBtn"),
  deleteServiceContextBtn: document.getElementById("deleteServiceContextBtn"),
  helpBtn: document.getElementById("helpBtn"),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json();
}

async function post(path, payload = {}) {
  return api(path, { method: "POST", body: JSON.stringify(payload) });
}

function serviceByName(name) {
  return state.services.find((service) => service.name === name);
}

function ensureTab(name) {
  if (!state.tabs.includes(name)) {
    state.tabs.push(name);
  }
}

function actionIcon(name) {
  const icons = {
    play: '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>',
    stop: '<svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none"/></svg>',
    terminal: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M8 10l3 2-3 2"/><path d="M13 15h4"/></svg>',
  };
  return icons[name] || "";
}

function setActionButton(button, iconName, label) {
  if (!button) return;
  const icon = document.createElement("span");
  icon.className = "button-icon";
  icon.innerHTML = actionIcon(iconName);
  const text = document.createElement("span");
  text.className = "button-label";
  text.textContent = label;
  button.replaceChildren(icon, text);
}

function selectService(name) {
  if (!serviceByName(name)) return;
  ensureTab(name);
  state.current = name;
  state.terminalFollow[name] = true;
  const service = serviceByName(name);
  els.commandInput.value = state.commands[name] || service.command || "pnpm start:dev";
  renderAll();
  fetchLogs(true);
}

function closeTab(name) {
  state.tabs = state.tabs.filter((tab) => tab !== name);
  if (state.current === name) {
    state.current = state.tabs[0] || null;
  }
  renderAll();
}

function renderAll() {
  renderHeader();
  renderServices();
  renderModalServices();
  renderTabs();
  renderWorkspaceMode();
  renderCommandBar();
  renderEmptyState();
  renderTerminal();
}

function renderWorkspaceMode() {
  const hasCurrent = !!state.current;
  els.emptyState.hidden = hasCurrent;
  els.commandBar.hidden = !hasCurrent;
  els.terminalCard.hidden = !hasCurrent;
}

function renderHeader() {
  const running = state.services.filter((service) => service.status === "running").length;
  if (els.activeInstances) els.activeInstances.textContent = `${running} active instances`;
  const startLabel =
    state.services.length === 0
      ? "Add First Service"
      : state.selected.size === 0
        ? "Start Service"
        : `Start Selected (${state.selected.size})`;
  setActionButton(els.startSelectedBtn, "play", startLabel);
  setActionButton(els.stopSelectedBtn, "stop", `Stop Selected (${state.selected.size})`);
  setActionButton(els.stopAllBtn, "stop", "Stop All");
  setActionButton(els.terminalBtn, "terminal", "Terminal");
  const emptyStartLabel =
    state.services.length === 0
      ? "Add First Service"
      : state.selected.size === 0
      ? "Start Service"
      : `Start Selected (${state.selected.size})`;
  setActionButton(els.emptyStartSelectedBtn, "play", emptyStartLabel);
  setActionButton(els.runBtn, "play", "Run");
  setActionButton(els.stopBtn, "stop", "Stop");
  els.startSelectedBtn.disabled = false;
  els.stopSelectedBtn.disabled = state.selected.size === 0;
  els.selectAllBtn.disabled = state.services.length === 0;
  els.selectAllBtn.textContent =
    state.services.length > 0 && state.selected.size === state.services.length ? "Clear All" : "Select All";
}

function renderServices() {
  const filter = state.filter.toLowerCase();
  els.serviceList.replaceChildren();
  const services = state.services
    .filter((service) => !filter || service.name.toLowerCase().includes(filter) || service.directory.toLowerCase().includes(filter));
  if (!services.length) {
    const empty = document.createElement("div");
    empty.className = "empty-list";
    empty.textContent = state.services.length ? "No matching services." : "No services yet. Add one to start.";
    els.serviceList.append(empty);
    return;
  }
  services
    .forEach((service) => {
    const row = document.createElement("div");
    row.className = `service-row ${service.name === state.current ? "active" : ""}`;
    row.dataset.service = service.name;
    row.addEventListener("click", () => selectService(service.name));
    row.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      showServiceContextMenu(event, service.name);
    });

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.selected.has(service.name);
    checkbox.addEventListener("click", (event) => {
        event.stopPropagation();
        if (checkbox.checked) {
          state.selected.add(service.name);
        } else {
          state.selected.delete(service.name);
        }
        renderHeader();
      });

      const name = document.createElement("span");
      name.className = "service-name";
      name.textContent = service.name;

      const dot = document.createElement("span");
      dot.className = `dot ${service.status}`;

    row.append(checkbox, name, dot);
    els.serviceList.append(row);
  });
}

function renderModalServices() {
  if (!els.modalServiceList) return;
  const isStartMode = state.modalMode === "start";
  const isServiceRunning = (service) => service.status === "running";
  els.modalServiceList.replaceChildren();

  if (els.serviceModalTitle) {
    els.serviceModalTitle.textContent = isStartMode ? "Start services" : "Servisler";
  }
  if (els.serviceModalDescription) {
    els.serviceModalDescription.textContent = isStartMode
      ? "Başlatmak istediğin servisleri seç."
      : "Yeni servis ekle veya mevcut servislerden bir terminal sekmesi aç.";
  }
  if (els.serviceForm) {
    els.serviceForm.hidden = isStartMode;
  }
  if (els.serviceModalActionBtn) {
    if (isStartMode) {
      els.serviceModalActionBtn.hidden = false;
      const actionLabel =
        state.selected.size === 0 ? "Start Service" : `Start Selected (${state.selected.size})`;
      els.serviceModalActionBtn.textContent = actionLabel;
      els.serviceModalActionBtn.disabled = state.selected.size === 0;
    } else {
      els.serviceModalActionBtn.hidden = true;
      els.serviceModalActionBtn.disabled = false;
    }
  }

  if (!state.services.length) {
    const empty = document.createElement("div");
    empty.className = "modal-empty";
    empty.textContent = "Henüz servis eklenmedi.";
    els.modalServiceList.append(empty);
    return;
  }
  state.services.forEach((service) => {
    const row = document.createElement("div");
    row.className = isStartMode ? "modal-service modal-service-start" : "modal-service";

    const onSelect = () => {
      if (isStartMode && service.status === "running") return;
      if (!isStartMode) {
        selectService(service.name);
        closeServiceModal();
        return;
      }
      if (state.selected.has(service.name)) {
        state.selected.delete(service.name);
      } else {
        state.selected.add(service.name);
      }
      renderHeader();
      renderServices();
      renderModalServices();
    };

    const button = document.createElement("button");
    button.className = "modal-service-main";
    button.type = "button";

    const text = document.createElement("span");
    text.className = "modal-service-name";
    text.textContent = service.name;

    const path = document.createElement("span");
    path.className = "modal-service-path";
    path.textContent = service.path || service.directory;

    const status = document.createElement("span");
    status.className = `dot ${service.status}`;
    const disabledSelection = isStartMode && isServiceRunning(service);

    button.append(text, path);
    if (isStartMode) {
      const check = document.createElement("input");
      check.type = "checkbox";
      check.checked = state.selected.has(service.name);
      check.disabled = disabledSelection;
      if (disabledSelection) {
        check.title = "Çalışan servisler seçilemez.";
      }
      check.addEventListener("click", (event) => {
        event.stopPropagation();
        if (disabledSelection) {
          check.checked = false;
          return;
        }
        onSelect();
      });
      if (disabledSelection) row.classList.add("modal-service-disabled");
      button.addEventListener("click", onSelect);
      row.append(check, button, status);
    } else {
      button.addEventListener("click", onSelect);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "delete-service";
      remove.textContent = "Remove";
      remove.addEventListener("click", async (event) => {
        event.stopPropagation();
        if (!confirm(`${service.name} servisini kaldırmak istiyor musun?`)) return;
        await removeService(service.name);
      });
      row.append(button, status, remove);
    }
    els.modalServiceList.append(row);
  });
}

function renderTabs() {
  els.tabBar.replaceChildren();
  state.tabs.forEach((name) => {
    const tab = document.createElement("div");
    tab.className = `tab ${name === state.current ? "active" : ""}`;
    tab.dataset.service = name;

    const button = document.createElement("button");
    button.title = name;
    button.addEventListener("click", () => selectService(name));
    button.addEventListener("auxclick", (event) => {
      if (event.button === 1) {
        event.preventDefault();
        closeTab(name);
      }
    });
    button.addEventListener("mousedown", (event) => {
      if (event.button === 1) event.preventDefault();
    });

    const label = document.createElement("span");
    label.className = "tab-name";
    label.textContent = name;

    const close = document.createElement("span");
    close.className = "close";
    close.textContent = "x";
    close.addEventListener("click", () => closeTab(name));

    tab.addEventListener("auxclick", (event) => {
      if (event.button === 1) {
        event.preventDefault();
        closeTab(name);
      }
    });
    tab.addEventListener("mousedown", (event) => {
      if (event.button === 1) event.preventDefault();
    });

    button.append(label);
    tab.append(button, close);
    els.tabBar.append(tab);
  });

  const add = document.createElement("button");
  add.className = "add-tab";
  add.textContent = "+";
  add.addEventListener("click", openServiceModal);
  els.tabBar.append(add);
}

function renderEmptyState() {
  if (!els.emptyTitle || !els.emptyDescription) return;
  if (!state.services.length) {
    els.emptyTitle.textContent = "Başlamak için bir servis ekle";
    els.emptyDescription.textContent = "Servis adını ve proje klasörünü seç; konfigürasyon bu bilgisayarda kalıcı olarak saklanır.";
    setActionButton(els.emptyStartSelectedBtn, "play", "Add First Service");
  } else {
    els.emptyTitle.textContent = "Başlamak için bir servis seç";
    els.emptyDescription.textContent = "Soldaki listeden bir mikroservise tıkla, üstteki artı butonundan servis seç veya seçili servisleri topluca başlat.";
    setActionButton(
      els.emptyStartSelectedBtn,
      "play",
      state.selected.size === 0 ? "Start Service" : `Start Selected (${state.selected.size})`,
    );
  }
}

function renderCommandBar() {
  const service = serviceByName(state.current);
  if (!service) {
    els.commandInput.value = "";
    return;
  }
  els.terminalTitle.textContent = service.name.toUpperCase();
  els.portBadge.textContent = `PORT=${service.port || "auto"}`;
  els.envBadge.textContent = `ENV=${service.env || "DEV"}`;
  els.uptimeLabel.textContent = `UP: ${service.uptime || "--:--:--"}`;
  els.cpuValue.textContent = service.metrics?.cpu || "--";
  els.memValue.textContent = service.metrics?.memory || "--";
  els.terminalState.textContent = service.status.toUpperCase();
  els.terminalState.className = `state-pill ${service.status}`;
}

function renderTerminal() {
  const serviceName = state.current;
  if (!serviceName) {
    if (!isTerminalSelectionLocked()) els.terminalOutput.replaceChildren();
    return;
  }
  if (isTerminalSelectionLocked()) return;
  const previousTop = els.terminalOutput.scrollTop;
  const shouldFollow = state.terminalFollow[serviceName] !== false || isTerminalNearBottom();
  const filter = state.terminalFilter.trim().toLowerCase();
  const lines = (state.logs[state.current] || []).filter((entry) => !filter || entry.line.toLowerCase().includes(filter));
  els.terminalOutput.replaceChildren();
  lines.slice(-1200).forEach((entry) => {
    const div = document.createElement("div");
    div.className = `log-line ${entry.level || "plain"}`;
    div.textContent = entry.line;
    els.terminalOutput.append(div);
  });
  if (shouldFollow) {
    scrollTerminalToBottom();
  } else {
    els.terminalOutput.scrollTop = previousTop;
  }
}

function isTerminalNearBottom() {
  const distance = els.terminalOutput.scrollHeight - els.terminalOutput.scrollTop - els.terminalOutput.clientHeight;
  return distance < 36;
}

function scrollTerminalToBottom() {
  els.terminalOutput.scrollTop = els.terminalOutput.scrollHeight;
}

function isTerminalSelectionActive() {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) return false;
  return els.terminalOutput.contains(selection.anchorNode) || els.terminalOutput.contains(selection.focusNode);
}

function isTerminalSelectionLocked() {
  return state.terminalSelecting || isTerminalSelectionActive();
}

async function refreshState() {
  const data = await api("/api/state");
  state.services = data.services;
  const validServices = new Set(state.services.map((service) => service.name));
  state.selected = new Set([...state.selected].filter((name) => validServices.has(name)));
  state.tabs = state.tabs.filter((name) => validServices.has(name));
  if (state.current && !validServices.has(state.current)) state.current = state.tabs[0] || null;
  els.runtimeLabel.textContent = data.runtime || "";
  els.totalMetrics.textContent = `TOTAL CPU ${data.totalMetrics?.cpu || "--"} · RAM ${data.totalMetrics?.memory || "--"}`;
  state.services.forEach((service) => {
    if (!state.commands[service.name]) state.commands[service.name] = service.command;
    if (!state.logs[service.name]) state.logs[service.name] = [];
    if (!state.offsets[service.name]) state.offsets[service.name] = 0;
  });
  if (!state.initialized) {
    state.initialized = true;
    renderAll();
    return;
  }
  renderAll();
}

async function fetchLogs(reset = false) {
  if (!state.current) return;
  if (reset) {
    state.logs[state.current] = [];
    state.offsets[state.current] = 0;
  }
  const after = state.offsets[state.current] || 0;
  const data = await api(`/api/logs?service=${encodeURIComponent(state.current)}&after=${after}`);
  if (data.entries?.length) {
    state.logs[state.current].push(...data.entries);
    state.logs[state.current] = state.logs[state.current].slice(-1600);
  }
  state.offsets[state.current] = data.next || after;
  renderTerminal();
}

async function startCurrent() {
  if (!state.current) return;
  state.commands[state.current] = els.commandInput.value.trim();
  ensureTab(state.current);
  await post("/api/start", { service: state.current, command: state.commands[state.current] });
  await refreshState();
  await fetchLogs();
}

async function stopCurrent() {
  if (!state.current) return;
  await post("/api/stop", { service: state.current });
  await refreshState();
}

async function interruptCurrent() {
  if (!state.current) return;
  const service = serviceByName(state.current);
  if (service?.status !== "running") return;
  await post("/api/interrupt", { service: state.current });
  await refreshState();
}

async function startSelected() {
  const services = [...state.selected];
  if (!services.length) {
    if (!state.services.length) {
      openServiceModal("add");
    } else {
      openServiceModal("start");
    }
    return;
  }
  if (state.current) state.commands[state.current] = els.commandInput.value.trim();
  await post("/api/start-selected", { services, commands: state.commands });
  services.forEach(ensureTab);
  clearSelection();
  selectService(services[0]);
  await refreshState();
}

async function startSelectedFromModal() {
  if (state.modalMode !== "start") return;
  const services = [...state.selected];
  if (!services.length) return;
  await post("/api/start-selected", { services, commands: state.commands });
  services.forEach(ensureTab);
  closeServiceModal();
  clearSelection();
  selectService(services[0]);
  await refreshState();
}

async function stopSelected() {
  const services = [...state.selected];
  if (!services.length) return;
  await post("/api/stop-selected", { services });
  clearSelection();
  await refreshState();
}

async function addService(event) {
  event.preventDefault();
  const payload = {
    name: els.serviceNameInput.value.trim(),
    path: els.servicePathInput.value.trim(),
    command: els.serviceCommandInput.value.trim() || "pnpm start:dev",
  };
  const result = await post("/api/services/add", payload);
  if (!result.ok) {
    alert(result.message || "Service could not be added.");
    return;
  }
  els.serviceForm.reset();
  els.serviceCommandInput.value = "pnpm start:dev";
  await refreshState();
  renderModalServices();
}

async function removeService(name) {
  const result = await post("/api/services/remove", { service: name });
  if (!result.ok) {
    alert(result.message || "Service could not be removed.");
    return;
  }
  state.selected.delete(name);
  state.tabs = state.tabs.filter((tab) => tab !== name);
  if (state.current === name) state.current = state.tabs[0] || null;
  await refreshState();
  renderModalServices();
}

async function chooseServiceFolder() {
  const result = await post("/api/choose-folder");
  if (result.ok && result.path) {
    els.servicePathInput.value = result.path;
    if (!els.serviceNameInput.value.trim()) {
      els.serviceNameInput.value = result.path.split("/").filter(Boolean).pop() || "";
    }
  }
}

function toggleSelectAll() {
  if (state.selected.size === state.services.length) {
    clearSelection();
    return;
  }
  state.selected = new Set(state.services.map((service) => service.name));
  renderHeader();
  renderServices();
}

function clearSelection() {
  state.selected.clear();
  renderHeader();
  renderServices();
}

async function clearCurrent() {
  if (!state.current) return;
  const data = await post("/api/clear", { service: state.current });
  state.logs[state.current] = [];
  state.offsets[state.current] = data.next || 0;
  renderTerminal();
}

async function openTarget(target) {
  await post("/api/open", { target, service: state.current });
}

async function openSystemTerminal() {
  await post("/api/open-terminal", { service: state.current });
}

function visibleTerminalText() {
  return [...els.terminalOutput.querySelectorAll(".log-line")].map((line) => line.textContent).join("\n");
}

async function copyTerminalText() {
  const selected = window.getSelection()?.toString() || "";
  const text = selected.trim() ? selected : visibleTerminalText();
  await copyText(text);
}

async function copyText(text) {
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    document.body.append(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }
}

function showTerminalContextMenu(event) {
  if (!els.terminalContextMenu) return;
  event.preventDefault();
  hideServiceContextMenu();
  const selected = window.getSelection()?.toString().trim() || "";
  els.copySelectionMenuBtn.textContent = selected ? "Copy" : "Copy Visible";
  els.terminalContextMenu.hidden = false;
  const rect = els.terminalContextMenu.getBoundingClientRect();
  const left = Math.min(event.clientX, window.innerWidth - rect.width - 8);
  const top = Math.min(event.clientY, window.innerHeight - rect.height - 8);
  els.terminalContextMenu.style.left = `${Math.max(8, left)}px`;
  els.terminalContextMenu.style.top = `${Math.max(8, top)}px`;
}

function hideTerminalContextMenu() {
  if (els.terminalContextMenu) els.terminalContextMenu.hidden = true;
}

function showServiceContextMenu(event, serviceName) {
  const service = serviceByName(serviceName);
  if (!service || !els.serviceContextMenu || !els.openServiceContextBtn || !els.deleteServiceContextBtn) return;

  hideTerminalContextMenu();
  hideServiceContextMenu();

  state.contextService = service.name;
  els.openServiceContextBtn.textContent = "Open";
  els.deleteServiceContextBtn.textContent = `Delete ${service.name}`;
  els.serviceContextMenu.hidden = false;

  const rect = els.serviceContextMenu.getBoundingClientRect();
  const left = Math.min(event.clientX, window.innerWidth - rect.width - 8);
  const top = Math.min(event.clientY, window.innerHeight - rect.height - 8);
  els.serviceContextMenu.style.left = `${Math.max(8, left)}px`;
  els.serviceContextMenu.style.top = `${Math.max(8, top)}px`;
}

function hideServiceContextMenu() {
  if (!els.serviceContextMenu) return;
  els.serviceContextMenu.hidden = true;
  state.contextService = null;
}

function openServiceModal(mode = "add") {
  if (state.services.length === 0 && mode === "start") mode = "add";
  state.modalMode = mode;
  if (els.serviceForm) {
    els.serviceForm.hidden = mode !== "start";
  }
  if (els.serviceModalTitle) {
    els.serviceModalTitle.textContent = mode === "start" ? "Servisleri başlat" : "Servisler";
  }
  if (els.serviceModalDescription) {
    els.serviceModalDescription.textContent =
      mode === "start"
        ? "Başlatmak istediğin servisleri işaretle."
        : "Yeni servis ekle veya mevcut servislerden bir terminal sekmesi aç.";
  }
  if (els.serviceModalActionBtn) {
    els.serviceModalActionBtn.hidden = mode !== "start";
    els.serviceModalActionBtn.disabled = true;
  }
  renderModalServices();
  els.serviceModal.hidden = false;
}

function closeServiceModal() {
  state.modalMode = "add";
  hideServiceContextMenu();
  if (els.serviceForm) {
    els.serviceForm.hidden = false;
  }
  if (els.serviceModalTitle) {
    els.serviceModalTitle.textContent = "Servisler";
  }
  if (els.serviceModalDescription) {
    els.serviceModalDescription.textContent = "Yeni servis ekle veya mevcut servislerden bir terminal sekmesi aç.";
  }
  if (els.serviceModalActionBtn) {
    els.serviceModalActionBtn.hidden = true;
    els.serviceModalActionBtn.disabled = false;
  }
  els.serviceModal.hidden = true;
}

function bindEvents() {
  els.searchInput.addEventListener("input", () => {
    state.filter = els.searchInput.value.trim();
    renderServices();
  });
  els.terminalFilterInput.addEventListener("input", () => {
    state.terminalFilter = els.terminalFilterInput.value;
    renderTerminal();
  });
  els.commandInput.addEventListener("input", () => {
    if (state.current) state.commands[state.current] = els.commandInput.value;
  });
  els.commandInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") startCurrent();
  });
  els.terminalOutput.addEventListener("scroll", () => {
    if (!state.current) return;
    state.terminalFollow[state.current] = isTerminalNearBottom();
  });
  els.terminalOutput.addEventListener("mousedown", () => {
    state.terminalSelecting = true;
    hideTerminalContextMenu();
  });
  document.addEventListener("mouseup", () => {
    if (!state.terminalSelecting) return;
    setTimeout(() => {
      state.terminalSelecting = false;
      renderTerminal();
    }, 250);
  });
  els.terminalOutput.addEventListener("contextmenu", showTerminalContextMenu);
  els.terminalOutput.addEventListener(
    "wheel",
    () => {
      if (!state.current) return;
      state.terminalFollow[state.current] = false;
    },
    { passive: true },
  );
  els.startSelectedBtn.addEventListener("click", startSelected);
  els.stopSelectedBtn.addEventListener("click", stopSelected);
  els.addServiceBtn.addEventListener("click", openServiceModal);
  els.serviceForm.addEventListener("submit", addService);
  els.browseServicePathBtn.addEventListener("click", chooseServiceFolder);
  els.selectAllBtn.addEventListener("click", toggleSelectAll);
  els.stopAllBtn.addEventListener("click", async () => {
    await post("/api/stop-all");
    await refreshState();
  });
  if (els.refreshBtn) els.refreshBtn.addEventListener("click", refreshState);
  els.runBtn.addEventListener("click", startCurrent);
  els.stopBtn.addEventListener("click", stopCurrent);
  els.copyTerminalBtn.addEventListener("click", copyTerminalText);
  els.copySelectionMenuBtn?.addEventListener("click", async () => {
    hideTerminalContextMenu();
    const selected = window.getSelection()?.toString() || "";
    await copyText(selected.trim() ? selected : visibleTerminalText());
  });
  els.copyAllMenuBtn?.addEventListener("click", async () => {
    hideTerminalContextMenu();
    await copyText(visibleTerminalText());
  });
  if (els.openServiceContextBtn) {
    els.openServiceContextBtn.addEventListener("click", () => {
      const serviceName = state.contextService;
      hideServiceContextMenu();
      if (!serviceName) return;
      selectService(serviceName);
    });
  }
  if (els.deleteServiceContextBtn) {
    els.deleteServiceContextBtn.addEventListener("click", async () => {
      const serviceName = state.contextService;
      hideServiceContextMenu();
      if (!serviceName) return;
      if (!confirm(`${serviceName} servisini kaldırmak istiyor musun?`)) return;
      await removeService(serviceName);
    });
  }
  els.clearBtn.addEventListener("click", clearCurrent);
  els.terminalBtn.addEventListener("click", openSystemTerminal);
  els.emptyStartSelectedBtn.addEventListener("click", startSelected);
  if (els.serviceModalActionBtn) {
    els.serviceModalActionBtn.addEventListener("click", startSelectedFromModal);
  }
  els.closeServiceModalBtn.addEventListener("click", closeServiceModal);
  els.serviceModal.addEventListener("click", (event) => {
    if (event.target === els.serviceModal) closeServiceModal();
  });
  if (els.helpBtn) els.helpBtn.addEventListener("click", () => {
    alert("Select services on the left, edit the command for the active tab, then run them. Default command: pnpm start:dev.");
  });
  els.tabBar.addEventListener(
    "wheel",
    (event) => {
      if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
      event.preventDefault();
      els.tabBar.scrollLeft += event.deltaY;
    },
    { passive: false },
  );
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      hideTerminalContextMenu();
      hideServiceContextMenu();
      if (!els.serviceModal.hidden) {
        closeServiceModal();
        return;
      }
    }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      els.searchInput.focus();
      els.searchInput.select();
      return;
    }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "c") {
      if ((window.getSelection()?.toString() || "").length > 0) return;
      const service = serviceByName(state.current);
      if (service?.status === "running") {
        event.preventDefault();
        interruptCurrent();
      }
    }
  }, true);
  document.addEventListener("click", (event) => {
    if (els.terminalContextMenu && !els.terminalContextMenu.hidden && !els.terminalContextMenu.contains(event.target)) {
      hideTerminalContextMenu();
    }
    if (els.serviceContextMenu && !els.serviceContextMenu.hidden && !els.serviceContextMenu.contains(event.target)) {
      hideServiceContextMenu();
    }
  });
}

bindEvents();
refreshState();
setInterval(() => refreshState().catch(console.error), 1000);
setInterval(() => fetchLogs().catch(console.error), 450);
