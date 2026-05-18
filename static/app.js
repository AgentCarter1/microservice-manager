const state = {
  services: [],
  commands: {},
  selected: new Set(),
  tabs: [],
  current: null,
  initialized: false,
  logs: {},
  offsets: {},
  gitLogs: {},
  gitOffsets: {},
  filter: "",
  terminalFilter: "",
  gitTerminalFilter: "",
  terminalFollow: {},
  gitTerminalFollow: {},
  terminalSelecting: false,
  gitTerminalSelecting: false,
  terminalMode: "output",
  modalMode: "add",
  editingService: null,
  contextService: null,
  gitBusy: false,
  gitTerminalBusy: false,
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
  refreshAppBtn: document.getElementById("refreshAppBtn"),
  stopBtn: document.getElementById("stopBtn"),
  clearBtn: document.getElementById("clearBtn"),
  terminalTitle: document.getElementById("terminalTitle"),
  outputTerminalTabBtn: document.getElementById("outputTerminalTabBtn"),
  gitTerminalTabBtn: document.getElementById("gitTerminalTabBtn"),
  outputTerminalTools: document.getElementById("outputTerminalTools"),
  gitTerminalTools: document.getElementById("gitTerminalTools"),
  terminalMetrics: document.getElementById("terminalMetrics"),
  terminalState: document.getElementById("terminalState"),
  terminalOutput: document.getElementById("terminalOutput"),
  copyTerminalBtn: document.getElementById("copyTerminalBtn"),
  gitTerminalTitle: document.getElementById("gitTerminalTitle"),
  gitTerminalOutput: document.getElementById("gitTerminalOutput"),
  gitTerminalFilterInput: document.getElementById("gitTerminalFilterInput"),
  gitTerminalForm: document.getElementById("gitTerminalForm"),
  gitCommandInput: document.getElementById("gitCommandInput"),
  gitCommandSubmitBtn: document.getElementById("gitCommandSubmitBtn"),
  copyGitTerminalBtn: document.getElementById("copyGitTerminalBtn"),
  clearGitTerminalBtn: document.getElementById("clearGitTerminalBtn"),
  openGitMacTerminalBtn: document.getElementById("openGitMacTerminalBtn"),
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
  gitSummary: document.getElementById("gitSummary"),
  gitBranchLabel: document.getElementById("gitBranchLabel"),
  gitSyncLabel: document.getElementById("gitSyncLabel"),
  gitLastSyncLabel: document.getElementById("gitLastSyncLabel"),
  gitActionsBtn: document.getElementById("gitActionsBtn"),
  gitActionsMenu: document.getElementById("gitActionsMenu"),
  terminalContextMenu: document.getElementById("terminalContextMenu"),
  copySelectionMenuBtn: document.getElementById("copySelectionMenuBtn"),
  copyAllMenuBtn: document.getElementById("copyAllMenuBtn"),
  serviceContextMenu: document.getElementById("serviceContextMenu"),
  openServiceContextBtn: document.getElementById("openServiceContextBtn"),
  refreshServiceContextBtn: document.getElementById("refreshServiceContextBtn"),
  stopServiceContextBtn: document.getElementById("stopServiceContextBtn"),
  editServiceContextBtn: document.getElementById("editServiceContextBtn"),
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
    edit: '<svg viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg>',
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
  state.gitTerminalFollow[name] = true;
  const service = serviceByName(name);
  els.commandInput.value = state.commands[name] || service.command || "pnpm start:dev";
  renderAll();
  fetchLogs(true);
  fetchGitLogs(true);
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
  renderTerminalMode();
  renderTerminal();
  renderGitTerminal();
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
  setActionButton(els.refreshAppBtn, "terminal", "Refresh App");
  setActionButton(els.stopBtn, "stop", "Stop");
  els.startSelectedBtn.disabled = false;
  if (els.refreshAppBtn) els.refreshAppBtn.disabled = !state.current;
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

      const branch = document.createElement("span");
      const branchText = service.git?.isRepo ? service.git.branch || "detached" : "no git";
      branch.className = `service-branch ${service.git?.isRepo ? "" : "muted"} ${service.git?.dirty ? "dirty" : ""}`;
      branch.textContent = branchText;
      branch.title = service.git?.isRepo
        ? `${service.name} branch: ${branchText}${service.git?.dirty ? ` · ${service.git.dirtyCount || 0} dirty` : ""}`
        : `${service.name}: git repository not detected`;

      const dot = document.createElement("span");
      dot.className = `dot ${service.status}`;

      const menu = document.createElement("button");
      menu.type = "button";
      menu.className = "service-row-menu-btn";
      menu.title = `${service.name} actions`;
      menu.textContent = "...";
      menu.addEventListener("click", (event) => {
        event.stopPropagation();
        showServiceContextMenu(event, service.name);
      });

    row.append(checkbox, name, branch, dot, menu);
    els.serviceList.append(row);
  });
}

function renderModalServices() {
  if (!els.modalServiceList) return;
  const isStartMode = state.modalMode === "start";
  const isEditMode = state.modalMode === "edit";
  const editingService = isEditMode ? serviceByName(state.editingService) : null;
  const isServiceRunning = (service) => service.status === "running";
  els.modalServiceList.replaceChildren();
  els.modalServiceList.hidden = isEditMode;

  if (els.serviceModalTitle) {
    els.serviceModalTitle.textContent = isStartMode
      ? "Start services"
      : isEditMode
        ? `${editingService?.name || "Servis"} düzenle`
        : "Servisler";
  }
  if (els.serviceModalDescription) {
    els.serviceModalDescription.textContent = isStartMode
      ? "Başlatmak istediğin servisleri seç."
      : isEditMode
        ? "Servis adını, klasör yolunu ve varsayılan komutunu güncelle."
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
  const saveBtn = els.serviceForm?.querySelector('button[type="submit"]');
  if (saveBtn) saveBtn.textContent = isEditMode ? "Update Service" : "Add Service";

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
      const edit = document.createElement("button");
      edit.type = "button";
      edit.className = "edit-service";
      edit.textContent = "Edit";
      edit.addEventListener("click", (event) => {
        event.stopPropagation();
        openServiceModal("edit", service.name);
      });
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "delete-service";
      remove.textContent = "Remove";
      remove.addEventListener("click", async (event) => {
        event.stopPropagation();
        if (!confirm(`${service.name} servisini kaldırmak istiyor musun?`)) return;
        await removeService(service.name);
      });
      row.append(button, status, edit, remove);
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
  add.addEventListener("click", () => openServiceModal("add"));
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
    if (els.gitCommandInput) els.gitCommandInput.disabled = true;
    if (els.gitCommandSubmitBtn) els.gitCommandSubmitBtn.disabled = true;
    renderGitSummary(null);
    return;
  }
  els.portBadge.textContent = `PORT=${service.port || "auto"}`;
  els.envBadge.textContent = `ENV=${service.env || "DEV"}`;
  els.uptimeLabel.textContent = `UP: ${service.uptime || "--:--:--"}`;
  els.cpuValue.textContent = service.metrics?.cpu || "--";
  els.memValue.textContent = service.metrics?.memory || "--";
  els.terminalState.textContent = service.status.toUpperCase();
  els.terminalState.className = `state-pill ${service.status}`;
  if (els.gitCommandInput) {
    els.gitCommandInput.disabled = state.gitTerminalBusy;
    els.gitCommandInput.placeholder = service.git?.isRepo ? "git status" : "pwd";
  }
  if (els.gitCommandSubmitBtn) {
    els.gitCommandSubmitBtn.disabled = state.gitTerminalBusy;
    els.gitCommandSubmitBtn.textContent = state.gitTerminalBusy ? "Run..." : "Enter";
  }
  if (els.openGitMacTerminalBtn) {
    els.openGitMacTerminalBtn.disabled = !state.current;
  }
  renderGitSummary(service);
}

function setTerminalMode(mode) {
  state.terminalMode = mode === "git" ? "git" : "output";
  renderTerminalMode();
  if (state.terminalMode === "git") {
    renderGitTerminal();
    els.gitCommandInput?.focus();
  } else {
    renderTerminal();
  }
}

function renderTerminalMode() {
  const isGit = state.terminalMode === "git";
  els.outputTerminalTabBtn?.classList.toggle("active", !isGit);
  els.gitTerminalTabBtn?.classList.toggle("active", isGit);
  els.outputTerminalTabBtn?.setAttribute("aria-selected", String(!isGit));
  els.gitTerminalTabBtn?.setAttribute("aria-selected", String(isGit));
  if (els.terminalOutput) els.terminalOutput.hidden = isGit;
  if (els.gitTerminalOutput) els.gitTerminalOutput.hidden = !isGit;
  if (els.outputTerminalTools) els.outputTerminalTools.hidden = isGit;
  if (els.gitTerminalTools) els.gitTerminalTools.hidden = !isGit;
  if (els.terminalMetrics) els.terminalMetrics.hidden = isGit;
  if (els.gitTerminalForm) els.gitTerminalForm.hidden = !isGit;
}

function renderGitSummary(service) {
  if (!els.gitSummary || !els.gitBranchLabel || !els.gitSyncLabel || !els.gitLastSyncLabel) return;
  const git = service?.git;
  if (!service || !git) {
    els.gitSummary.hidden = true;
    if (els.gitActionsBtn) els.gitActionsBtn.disabled = true;
    return;
  }
  els.gitSummary.hidden = false;
  els.gitBranchLabel.textContent = git.isRepo ? git.branch || "DETACHED" : "NO REPO";
  els.gitSyncLabel.textContent = git.isRepo ? git.developmentLabel || "UNKNOWN" : "NO GIT";
  els.gitSyncLabel.className = "git-sync";
  const syncMeta = git.lastDevelopmentSync
    ? `LAST ${git.lastDevelopmentSync}${git.lastDevelopmentSyncStatus ? ` · ${git.lastDevelopmentSyncStatus.toUpperCase()}` : ""}`
    : git.isRepo
      ? "NOT SYNCED FROM APP"
      : "GIT NOT AVAILABLE";
  els.gitLastSyncLabel.textContent = git.dirty && git.dirtyCount
    ? `${syncMeta} · ${git.dirtyCount} dirty`
    : syncMeta;
  const mode = !git.isRepo
    ? "missing"
    : git.dirty
      ? "dirty"
      : git.developmentStatus || "clean";
  els.gitSummary.className = `git-summary ${mode}`;
  if (els.gitActionsBtn) {
    els.gitActionsBtn.disabled = !state.current || state.gitBusy;
    els.gitActionsBtn.textContent = state.gitBusy ? "Git Working..." : "Git Actions v";
  }
  if (els.gitActionsMenu) {
    els.gitActionsMenu.querySelectorAll("button[data-git-action]").forEach((button) => {
      const action = button.dataset.gitAction;
      button.disabled = state.gitBusy || (!git.isRepo && action !== "refresh");
    });
  }
}

function renderTerminal() {
  const serviceName = state.current;
  if (!serviceName) {
    if (!isTerminalSelectionLocked()) els.terminalOutput.replaceChildren();
    return;
  }
  if (isTerminalSelectionLocked()) return;
  const previousTop = els.terminalOutput.scrollTop;
  const shouldFollow = state.terminalFollow[serviceName] !== false || isOutputNearBottom(els.terminalOutput);
  const filter = state.terminalFilter.trim().toLowerCase();
  const lines = (state.logs[state.current] || []).filter((entry) => !filter || entry.line.toLowerCase().includes(filter));
  renderLogLines(els.terminalOutput, lines.slice(-1200));
  if (shouldFollow) {
    scrollOutputToBottom(els.terminalOutput);
  } else {
    els.terminalOutput.scrollTop = previousTop;
  }
}

function renderGitTerminal() {
  if (!els.gitTerminalOutput) return;
  const serviceName = state.current;
  if (!serviceName) {
    if (!isGitTerminalSelectionLocked()) els.gitTerminalOutput.replaceChildren();
    return;
  }
  if (isGitTerminalSelectionLocked()) return;
  const previousTop = els.gitTerminalOutput.scrollTop;
  const shouldFollow = state.gitTerminalFollow[serviceName] !== false || isOutputNearBottom(els.gitTerminalOutput);
  const filter = state.gitTerminalFilter.trim().toLowerCase();
  const lines = (state.gitLogs[state.current] || []).filter((entry) => !filter || entry.line.toLowerCase().includes(filter));
  renderLogLines(els.gitTerminalOutput, lines.slice(-1200));
  if (shouldFollow) {
    scrollOutputToBottom(els.gitTerminalOutput);
  } else {
    els.gitTerminalOutput.scrollTop = previousTop;
  }
}

function renderLogLines(output, lines) {
  output.replaceChildren();
  lines.forEach((entry) => {
    const div = document.createElement("div");
    div.className = `log-line ${entry.level || "plain"}`;
    div.textContent = entry.line;
    output.append(div);
  });
}

function isOutputNearBottom(output) {
  const distance = output.scrollHeight - output.scrollTop - output.clientHeight;
  return distance < 36;
}

function scrollOutputToBottom(output) {
  output.scrollTop = output.scrollHeight;
}

function isTerminalSelectionActive(output = els.terminalOutput) {
  if (!output) return false;
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) return false;
  return output.contains(selection.anchorNode) || output.contains(selection.focusNode);
}

function isTerminalSelectionLocked() {
  return state.terminalSelecting || isTerminalSelectionActive(els.terminalOutput);
}

function isGitTerminalSelectionLocked() {
  return state.gitTerminalSelecting || isTerminalSelectionActive(els.gitTerminalOutput);
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
    if (!state.gitLogs[service.name]) state.gitLogs[service.name] = [];
    if (!state.gitOffsets[service.name]) state.gitOffsets[service.name] = 0;
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

async function fetchGitLogs(reset = false) {
  if (!state.current) return;
  if (reset) {
    state.gitLogs[state.current] = [];
    state.gitOffsets[state.current] = 0;
  }
  const after = state.gitOffsets[state.current] || 0;
  const data = await api(`/api/git/logs?service=${encodeURIComponent(state.current)}&after=${after}`);
  if (data.entries?.length) {
    state.gitLogs[state.current].push(...data.entries);
    state.gitLogs[state.current] = state.gitLogs[state.current].slice(-1600);
  }
  state.gitOffsets[state.current] = data.next || after;
  renderGitTerminal();
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

async function refreshCurrent() {
  if (!state.current) return;
  state.commands[state.current] = els.commandInput.value.trim();
  ensureTab(state.current);
  const result = await post("/api/refresh", { service: state.current, command: state.commands[state.current] });
  if (!result.ok) {
    alert(result.message || "Service could not be refreshed.");
  }
  await refreshState();
  await fetchLogs();
}

async function refreshService(name) {
  if (!serviceByName(name)) return;
  if (state.current === name) {
    await refreshCurrent();
    return;
  }
  const result = await post("/api/refresh", { service: name, command: state.commands[name] });
  if (!result.ok) {
    alert(result.message || "Service could not be refreshed.");
  }
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

async function saveService(event) {
  event.preventDefault();
  const payload = {
    name: els.serviceNameInput.value.trim(),
    path: els.servicePathInput.value.trim(),
    command: els.serviceCommandInput.value.trim() || "pnpm start:dev",
  };
  const editingName = state.editingService;
  const result =
    state.modalMode === "edit" && editingName
      ? await post("/api/services/update", { ...payload, service: editingName })
      : await post("/api/services/add", payload);
  if (!result.ok) {
    alert(result.message || "Service could not be saved.");
    return;
  }
  if (state.modalMode === "edit" && result.service) {
    replaceServiceReferences(editingName, result.service.name);
    closeServiceModal();
    ensureTab(result.service.name);
    state.current = result.service.name;
  } else {
    els.serviceForm.reset();
    els.serviceCommandInput.value = "pnpm start:dev";
  }
  await refreshState();
  renderModalServices();
}

function replaceServiceReferences(previousName, nextName) {
  if (!previousName || !nextName || previousName === nextName) return;
  if (state.selected.delete(previousName)) state.selected.add(nextName);
  state.tabs = state.tabs.map((tab) => (tab === previousName ? nextName : tab));
  if (state.current === previousName) state.current = nextName;
  if (state.commands[previousName]) {
    state.commands[nextName] = state.commands[previousName];
    delete state.commands[previousName];
  }
  if (state.logs[previousName]) {
    state.logs[nextName] = state.logs[previousName];
    delete state.logs[previousName];
  }
  if (state.offsets[previousName]) {
    state.offsets[nextName] = state.offsets[previousName];
    delete state.offsets[previousName];
  }
  if (state.gitLogs[previousName]) {
    state.gitLogs[nextName] = state.gitLogs[previousName];
    delete state.gitLogs[previousName];
  }
  if (state.gitOffsets[previousName]) {
    state.gitOffsets[nextName] = state.gitOffsets[previousName];
    delete state.gitOffsets[previousName];
  }
  if (state.terminalFollow[previousName] !== undefined) {
    state.terminalFollow[nextName] = state.terminalFollow[previousName];
    delete state.terminalFollow[previousName];
  }
  if (state.gitTerminalFollow[previousName] !== undefined) {
    state.gitTerminalFollow[nextName] = state.gitTerminalFollow[previousName];
    delete state.gitTerminalFollow[previousName];
  }
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
  if (state.editingService === name) closeServiceModal();
  delete state.commands[name];
  delete state.logs[name];
  delete state.offsets[name];
  delete state.gitLogs[name];
  delete state.gitOffsets[name];
  delete state.terminalFollow[name];
  delete state.gitTerminalFollow[name];
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

async function clearGitCurrent() {
  if (!state.current) return;
  const data = await post("/api/git/clear", { service: state.current });
  state.gitLogs[state.current] = [];
  state.gitOffsets[state.current] = data.next || 0;
  renderGitTerminal();
}

async function openTarget(target) {
  await post("/api/open", { target, service: state.current });
}

async function openSystemTerminal() {
  await post("/api/open-terminal", { service: state.current });
}

async function openGitMacTerminal() {
  if (!state.current) return;
  await post("/api/open-terminal", { service: state.current });
}

function toggleGitActionsMenu() {
  if (!els.gitActionsMenu || !state.current) return;
  hideTerminalContextMenu();
  hideServiceContextMenu();
  els.gitActionsMenu.hidden = !els.gitActionsMenu.hidden;
}

function hideGitActionsMenu() {
  if (els.gitActionsMenu) els.gitActionsMenu.hidden = true;
}

async function runGitAction(action) {
  if (!state.current || !action) return;
  hideGitActionsMenu();
  state.gitBusy = true;
  renderCommandBar();
  try {
    const result = await post("/api/git/action", { service: state.current, action });
    if (!result.ok) {
      alert(result.message || "Git action failed.");
    }
    await refreshState();
    await fetchGitLogs();
  } finally {
    state.gitBusy = false;
    renderCommandBar();
  }
}

async function runGitTerminalCommand(event) {
  event.preventDefault();
  if (!state.current || !els.gitCommandInput || state.gitTerminalBusy) return;
  const command = els.gitCommandInput.value.trim();
  if (!command) return;
  els.gitCommandInput.value = "";
  state.gitTerminalBusy = true;
  renderCommandBar();
  try {
    const result = await post("/api/git/terminal", { service: state.current, command });
    if (!result.ok) {
      console.warn(result.message || "Git terminal command failed.");
    }
    await refreshState();
    await fetchGitLogs();
  } finally {
    state.gitTerminalBusy = false;
    renderCommandBar();
    els.gitCommandInput?.focus();
  }
}

function visibleTerminalText() {
  return [...els.terminalOutput.querySelectorAll(".log-line")].map((line) => line.textContent).join("\n");
}

function visibleGitTerminalText() {
  if (!els.gitTerminalOutput) return "";
  return [...els.gitTerminalOutput.querySelectorAll(".log-line")].map((line) => line.textContent).join("\n");
}

async function copyTerminalText() {
  const selected = window.getSelection()?.toString() || "";
  const text = selected.trim() ? selected : visibleTerminalText();
  await copyText(text);
}

async function copyGitTerminalText() {
  const selected = window.getSelection()?.toString() || "";
  const text = selected.trim() ? selected : visibleGitTerminalText();
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
  hideGitActionsMenu();
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
  hideGitActionsMenu();

  state.contextService = service.name;
  els.openServiceContextBtn.textContent = "Open";
  if (els.refreshServiceContextBtn) els.refreshServiceContextBtn.textContent = "Refresh";
  if (els.stopServiceContextBtn) els.stopServiceContextBtn.textContent = "Stop";
  if (els.editServiceContextBtn) els.editServiceContextBtn.textContent = "Edit";
  els.deleteServiceContextBtn.textContent = "Delete";
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

function openServiceModal(mode = "add", serviceName = null) {
  if (state.services.length === 0 && mode === "start") mode = "add";
  state.modalMode = mode;
  state.editingService = mode === "edit" ? serviceName : null;
  const editingService = state.editingService ? serviceByName(state.editingService) : null;
  if (mode === "edit" && editingService) {
    els.serviceNameInput.value = editingService.name;
    els.servicePathInput.value = editingService.path || editingService.directory || "";
    els.serviceCommandInput.value = editingService.command || "pnpm start:dev";
  } else if (mode !== "start") {
    els.serviceForm.reset();
    els.serviceCommandInput.value = "pnpm start:dev";
  }
  if (els.serviceForm) {
    els.serviceForm.hidden = mode === "start";
  }
  if (els.serviceModalTitle) {
    els.serviceModalTitle.textContent =
      mode === "start" ? "Servisleri başlat" : mode === "edit" ? `${editingService?.name || "Servis"} düzenle` : "Servisler";
  }
  if (els.serviceModalDescription) {
    els.serviceModalDescription.textContent =
      mode === "start"
        ? "Başlatmak istediğin servisleri işaretle."
        : mode === "edit"
          ? "Servis adını, klasör yolunu ve varsayılan komutunu güncelle."
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
  state.editingService = null;
  hideServiceContextMenu();
  hideGitActionsMenu();
  els.serviceForm.reset();
  els.serviceCommandInput.value = "pnpm start:dev";
  els.modalServiceList.hidden = false;
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
  els.gitTerminalFilterInput?.addEventListener("input", () => {
    state.gitTerminalFilter = els.gitTerminalFilterInput.value;
    renderGitTerminal();
  });
  els.commandInput.addEventListener("input", () => {
    if (state.current) state.commands[state.current] = els.commandInput.value;
  });
  els.commandInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") startCurrent();
  });
  els.terminalOutput.addEventListener("scroll", () => {
    if (!state.current) return;
    state.terminalFollow[state.current] = isOutputNearBottom(els.terminalOutput);
  });
  els.gitTerminalOutput?.addEventListener("scroll", () => {
    if (!state.current) return;
    state.gitTerminalFollow[state.current] = isOutputNearBottom(els.gitTerminalOutput);
  });
  els.terminalOutput.addEventListener("mousedown", () => {
    state.terminalSelecting = true;
    hideTerminalContextMenu();
  });
  els.gitTerminalOutput?.addEventListener("mousedown", () => {
    state.gitTerminalSelecting = true;
    hideTerminalContextMenu();
  });
  document.addEventListener("mouseup", () => {
    if (state.terminalSelecting) {
      setTimeout(() => {
        state.terminalSelecting = false;
        renderTerminal();
      }, 250);
    }
    if (state.gitTerminalSelecting) {
      setTimeout(() => {
        state.gitTerminalSelecting = false;
        renderGitTerminal();
      }, 250);
    }
  });
  els.outputTerminalTabBtn?.addEventListener("click", () => setTerminalMode("output"));
  els.gitTerminalTabBtn?.addEventListener("click", () => setTerminalMode("git"));
  els.terminalOutput.addEventListener("contextmenu", showTerminalContextMenu);
  els.gitTerminalOutput?.addEventListener("contextmenu", showTerminalContextMenu);
  els.terminalOutput.addEventListener(
    "wheel",
    () => {
      if (!state.current) return;
      state.terminalFollow[state.current] = false;
    },
    { passive: true },
  );
  els.gitTerminalOutput?.addEventListener(
    "wheel",
    () => {
      if (!state.current) return;
      state.gitTerminalFollow[state.current] = false;
    },
    { passive: true },
  );
  els.startSelectedBtn.addEventListener("click", startSelected);
  els.stopSelectedBtn.addEventListener("click", stopSelected);
  els.addServiceBtn.addEventListener("click", () => openServiceModal("add"));
  els.serviceForm.addEventListener("submit", saveService);
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
  els.copyGitTerminalBtn?.addEventListener("click", copyGitTerminalText);
  els.clearGitTerminalBtn?.addEventListener("click", clearGitCurrent);
  els.openGitMacTerminalBtn?.addEventListener("click", openGitMacTerminal);
  els.gitTerminalForm?.addEventListener("submit", runGitTerminalCommand);
  els.gitCommandInput?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    runGitTerminalCommand(event);
  });
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
  if (els.refreshServiceContextBtn) {
    els.refreshServiceContextBtn.addEventListener("click", async () => {
      const serviceName = state.contextService;
      hideServiceContextMenu();
      if (!serviceName) return;
      await refreshService(serviceName);
    });
  }
  if (els.stopServiceContextBtn) {
    els.stopServiceContextBtn.addEventListener("click", async () => {
      const serviceName = state.contextService;
      hideServiceContextMenu();
      if (!serviceName) return;
      await post("/api/stop", { service: serviceName });
      await refreshState();
    });
  }
  if (els.editServiceContextBtn) {
    els.editServiceContextBtn.addEventListener("click", () => {
      const serviceName = state.contextService;
      hideServiceContextMenu();
      if (!serviceName) return;
      openServiceModal("edit", serviceName);
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
  els.refreshAppBtn?.addEventListener("click", refreshCurrent);
  els.terminalBtn.addEventListener("click", openSystemTerminal);
  els.gitActionsBtn?.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleGitActionsMenu();
  });
  els.gitActionsMenu?.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-git-action]");
    if (!button) return;
    runGitAction(button.dataset.gitAction);
  });
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
      hideGitActionsMenu();
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
    if (
      els.gitActionsMenu &&
      !els.gitActionsMenu.hidden &&
      !els.gitActionsMenu.contains(event.target) &&
      !els.gitActionsBtn.contains(event.target)
    ) {
      hideGitActionsMenu();
    }
  });
}

bindEvents();
refreshState();
setInterval(() => refreshState().catch(console.error), 1000);
setInterval(() => fetchLogs().catch(console.error), 450);
setInterval(() => fetchGitLogs().catch(console.error), 650);
