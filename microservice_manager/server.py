from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse


APP_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = APP_DIR / "static"
ANSI_RE = re.compile(r"(?:\x1B[@-Z\\-_]|\x1B\[[0-?]*[ -/]*[@-~]|\x1B\][^\x07]*(?:\x07|\x1B\\))")

if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    APP_DIR = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    STATIC_DIR = APP_DIR / "static"


def user_config_dir() -> Path:
    override = os.environ.get("MIROSERVICE_MANAGER_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Miroservice Manager"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Miroservice Manager"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "miroservice-manager"


CONFIG_PATH = Path(os.environ.get("MIROSERVICE_MANAGER_CONFIG", user_config_dir() / "services.json")).expanduser()


@dataclass
class ServiceConfig:
    name: str
    directory: str
    command: str
    env: str = "DEV"
    port: str = ""
    git: Dict[str, str] = field(default_factory=dict)


def load_config() -> Tuple[Path, str, List[ServiceConfig]]:
    if CONFIG_PATH.exists():
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    else:
        raw = {}

    root_dir = Path(raw.get("root") or str(Path.home())).expanduser().resolve()
    default_command = raw.get("default_command", "pnpm start:dev")
    raw_services = raw.get("services") or []

    services = [
        ServiceConfig(
            name=item["name"],
            directory=item["directory"],
            command=item.get("command", default_command),
            env=item.get("env", "DEV"),
            port=str(item.get("port", "")),
            git=dict(item.get("git") or {}),
        )
        for item in raw_services
    ]
    return root_dir, default_command, services


class ServiceManager:
    def __init__(self) -> None:
        self.root_dir, self.default_command, self.services = load_config()
        self.service_by_name = {service.name: service for service in self.services}
        self.lock = threading.RLock()
        self.processes: Dict[str, subprocess.Popen[str]] = {}
        self.started_at: Dict[str, float] = {}
        self.last_runtime_seconds: Dict[str, Optional[int]] = {service.name: None for service in self.services}
        self.last_exit_codes: Dict[str, Optional[int]] = {service.name: None for service in self.services}
        self.last_exit_reasons: Dict[str, str] = {service.name: "" for service in self.services}
        self.health_states: Dict[str, str] = {service.name: "idle" for service in self.services}
        self.expected_stops: Dict[str, str] = {}
        self.commands: Dict[str, str] = {service.name: service.command or self.default_command for service in self.services}
        self.logs: Dict[str, List[Dict[str, Any]]] = {service.name: [] for service in self.services}
        self.log_sequence: Dict[str, int] = {service.name: 0 for service in self.services}
        self.git_logs: Dict[str, List[Dict[str, Any]]] = {service.name: [] for service in self.services}
        self.git_log_sequence: Dict[str, int] = {service.name: 0 for service in self.services}
        self.git_cache: Dict[str, Dict[str, Any]] = {}

        first = self.services[0].name if self.services else "system"
        self.append_log(first, "Miroservice Manager backend ready.", "system")

    def state(self) -> Dict[str, Any]:
        with self.lock:
            running_count = sum(1 for service in self.services if self.is_running(service.name))
            error_count = sum(1 for service in self.services if self.status(service.name) == "error")
            service_states = [self.service_state(service.name) for service in self.services]
            return {
                "root": str(self.root_dir),
                "defaultCommand": self.default_command,
                "configPath": str(CONFIG_PATH),
                "runningCount": running_count,
                "errorCount": error_count,
                "services": service_states,
                "totalMetrics": self.total_metrics(service_states),
                "runtime": self.runtime_label(),
            }

    def service_state(self, name: str) -> Dict[str, Any]:
        service = self.service_by_name[name]
        port = service.port or self.discover_port(service)
        health = self.health_state(name, service, port)
        self.track_health_transition(name, health)
        return {
            "name": service.name,
            "directory": service.directory,
            "command": self.commands.get(name, service.command or self.default_command),
            "status": self.status(name),
            "uptime": self.format_uptime(name),
            "exitCode": self.last_exit_codes.get(name),
            "exitReason": self.last_exit_reasons.get(name, ""),
            "env": service.env,
            "port": port,
            "path": str(self.service_path(service)),
            "metrics": self.metrics(name),
            "health": health,
            "git": self.git_state(service),
        }

    def service_path(self, service: ServiceConfig) -> Path:
        directory = Path(service.directory).expanduser()
        if directory.is_absolute():
            return directory
        return self.root_dir / directory

    def save_config(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "root": str(self.root_dir),
            "default_command": self.default_command,
            "services": [
                {
                    "name": service.name,
                    "directory": service.directory,
                    "command": service.command or self.default_command,
                    "env": service.env,
                    "port": service.port,
                    "git": service.git,
                }
                for service in self.services
            ],
        }
        tmp_path = CONFIG_PATH.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(CONFIG_PATH)

    def add_service(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        name = str(payload.get("name", "")).strip()
        path = str(payload.get("path", "") or payload.get("directory", "")).strip()
        command = str(payload.get("command", "") or self.default_command).strip()
        env = str(payload.get("env", "DEV") or "DEV").strip()
        port = str(payload.get("port", "") or "").strip()

        if not name:
            return {"ok": False, "message": "Service name is required."}
        if not path:
            return {"ok": False, "message": "Service path is required."}

        directory = Path(path).expanduser().resolve()
        if not directory.exists() or not directory.is_dir():
            return {"ok": False, "message": f"Directory not found: {directory}"}

        with self.lock:
            if name in self.service_by_name:
                return {"ok": False, "message": f"Service already exists: {name}"}
            service = ServiceConfig(name=name, directory=str(directory), command=command, env=env, port=port)
            self.services.append(service)
            self.service_by_name[name] = service
            self.commands[name] = command
            self.logs[name] = []
            self.log_sequence[name] = 0
            self.git_logs[name] = []
            self.git_log_sequence[name] = 0
            self.last_runtime_seconds[name] = None
            self.last_exit_codes[name] = None
            self.last_exit_reasons[name] = ""
            self.health_states[name] = "idle"
            self.save_config()
            self.append_log(name, f"Service added: {directory}", "system")
            self.append_git_log(name, f"Git terminal ready in {directory}", "system")
        return {"ok": True, "message": f"{name} added.", "service": self.service_state(name)}

    def update_service(self, current_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        current_name = str(current_name or payload.get("service", "")).strip()
        name = str(payload.get("name", "")).strip()
        path = str(payload.get("path", "") or payload.get("directory", "")).strip()
        command = str(payload.get("command", "") or self.default_command).strip()
        env = str(payload.get("env", "DEV") or "DEV").strip()
        port = str(payload.get("port", "") or "").strip()

        if not current_name:
            return {"ok": False, "message": "Service name is required."}
        if not name:
            return {"ok": False, "message": "Service name is required."}
        if not path:
            return {"ok": False, "message": "Service path is required."}

        directory = Path(path).expanduser().resolve()
        if not directory.exists() or not directory.is_dir():
            return {"ok": False, "message": f"Directory not found: {directory}"}

        with self.lock:
            self.require_service(current_name)
            service = self.service_by_name[current_name]
            if name != current_name and name in self.service_by_name:
                return {"ok": False, "message": f"Service already exists: {name}"}
            current_directory = self.service_path(service).expanduser().resolve()
            if self.is_running(current_name) and (name != current_name or directory != current_directory):
                return {"ok": False, "message": f"Stop {current_name} before renaming it or changing its path."}

            if name != current_name:
                self.service_by_name.pop(current_name, None)
                self.service_by_name[name] = service
                self.commands[name] = self.commands.pop(current_name, command)
                self.logs[name] = self.logs.pop(current_name, [])
                self.log_sequence[name] = self.log_sequence.pop(current_name, 0)
                self.git_logs[name] = self.git_logs.pop(current_name, [])
                self.git_log_sequence[name] = self.git_log_sequence.pop(current_name, 0)
                started = self.started_at.pop(current_name, None)
                if started is not None:
                    self.started_at[name] = started
                self.last_runtime_seconds[name] = self.last_runtime_seconds.pop(current_name, None)
                self.last_exit_codes[name] = self.last_exit_codes.pop(current_name, None)
                self.last_exit_reasons[name] = self.last_exit_reasons.pop(current_name, "")
                self.health_states[name] = self.health_states.pop(current_name, "idle")
                expected_stop = self.expected_stops.pop(current_name, None)
                if expected_stop:
                    self.expected_stops[name] = expected_stop
                self.processes.pop(current_name, None)
                self.git_cache.pop(current_name, None)

            service.name = name
            service.directory = str(directory)
            service.command = command
            service.env = env
            service.port = port
            self.commands[name] = command
            self.git_cache.pop(name, None)
            self.save_config()
            self.append_log(name, f"Service updated: {directory}", "system")
            self.append_git_log(name, f"Service config updated: {directory}", "system")
        return {"ok": True, "message": f"{name} updated.", "service": self.service_state(name)}

    def remove_service(self, name: str) -> Dict[str, Any]:
        name = str(name or "").strip()
        with self.lock:
            self.require_service(name)
            if self.is_running(name):
                return {"ok": False, "message": f"Stop {name} before removing it."}
            self.services = [service for service in self.services if service.name != name]
            self.service_by_name.pop(name, None)
            self.commands.pop(name, None)
            self.logs.pop(name, None)
            self.log_sequence.pop(name, None)
            self.git_logs.pop(name, None)
            self.git_log_sequence.pop(name, None)
            self.started_at.pop(name, None)
            self.last_runtime_seconds.pop(name, None)
            self.last_exit_codes.pop(name, None)
            self.last_exit_reasons.pop(name, None)
            self.health_states.pop(name, None)
            self.expected_stops.pop(name, None)
            self.processes.pop(name, None)
            self.save_config()
        return {"ok": True, "message": f"{name} removed."}

    def choose_folder(self) -> Dict[str, Any]:
        try:
            if sys.platform == "darwin":
                result = subprocess.run(
                    ["osascript", "-e", 'POSIX path of (choose folder with prompt "Servis klasörünü seç")'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
                path = result.stdout.strip()
                if result.returncode == 0 and path:
                    return {"ok": True, "path": str(Path(path).resolve())}
                return {"ok": False, "message": "Folder selection cancelled."}

            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            path = filedialog.askdirectory(title="Select service folder")
            root.destroy()
            if path:
                return {"ok": True, "path": str(Path(path).resolve())}
            return {"ok": False, "message": "Folder selection cancelled."}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def start(self, name: str, command: Optional[str] = None, clear_logs: bool = True) -> Dict[str, Any]:
        with self.lock:
            self.require_service(name)
            service = self.service_by_name[name]
            command = (command or self.commands.get(name) or service.command or self.default_command).strip()
            if not command:
                return {"ok": False, "message": f"No command set for {name}."}
            if self.is_running(name):
                self.append_log(name, f"{name} is already running.", "warn")
                return {"ok": True, "message": f"{name} is already running."}

            cwd = self.service_path(service)
            if clear_logs:
                self.logs[name] = []
            if not cwd.exists():
                self.last_exit_codes[name] = 127
                self.append_log(name, f"Directory not found: {cwd}", "error")
                return {"ok": False, "message": f"Directory not found: {cwd}"}

            self.commands[name] = command
            if service.command != command:
                service.command = command
                self.save_config()
            self.last_exit_codes[name] = None
            self.last_exit_reasons[name] = ""
            self.last_runtime_seconds[name] = None
            self.health_states[name] = "starting"

        env = self.command_env({"FORCE_COLOR": "1", "PYTHONUNBUFFERED": "1"})
        args = self.shell_args(command)

        self.append_log(name, f"$ {command}", "command")
        self.append_log(name, f"cwd: {cwd}", "system")
        start_port = service.port or self.discover_port(service)
        if start_port:
            self.append_log(name, f"Health: starting; waiting for port {start_port}.", "system")
        else:
            self.append_log(name, "Health: starting; no port configured, process liveness will be used.", "system")

        try:
            process = subprocess.Popen(
                args,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                preexec_fn=os.setsid if os.name == "posix" else None,
            )
        except Exception as exc:
            with self.lock:
                self.last_exit_codes[name] = 1
            self.append_log(name, f"Failed to start: {exc}", "error")
            return {"ok": False, "message": str(exc)}

        with self.lock:
            self.processes[name] = process
            self.started_at[name] = time.time()
            self.last_runtime_seconds[name] = None

        thread = threading.Thread(target=self.read_process_output, args=(name, process), daemon=True)
        thread.start()
        return {"ok": True, "message": f"{name} started."}

    def stop(self, name: str) -> Dict[str, Any]:
        with self.lock:
            self.require_service(name)
            process = self.processes.get(name)
            if not process or process.poll() is not None:
                self.processes.pop(name, None)
                self.capture_runtime(name)
                return {"ok": True, "message": f"{name} is not running."}

        self.append_log(name, f"Stopping {name}...", "system")
        with self.lock:
            self.expected_stops[name] = "stopped"
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            pass
        except Exception as exc:
            with self.lock:
                self.expected_stops.pop(name, None)
            self.append_log(name, f"Stop failed: {exc}", "error")
            return {"ok": False, "message": str(exc)}

        timer = threading.Timer(2.5, self.kill_if_needed, args=(name, process))
        timer.daemon = True
        timer.start()
        return {"ok": True, "message": f"{name} stopping."}

    def interrupt(self, name: str) -> Dict[str, Any]:
        with self.lock:
            self.require_service(name)
            process = self.processes.get(name)
            if not process or process.poll() is not None:
                self.processes.pop(name, None)
                self.capture_runtime(name)
                return {"ok": True, "message": f"{name} is not running."}

        self.append_log(name, f"Sending SIGINT to {name}...", "system")
        with self.lock:
            self.expected_stops[name] = "interrupted"
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(process.pid), signal.SIGINT)
            else:
                process.send_signal(signal.SIGINT)
        except ProcessLookupError:
            pass
        except Exception as exc:
            with self.lock:
                self.expected_stops.pop(name, None)
            self.append_log(name, f"Interrupt failed: {exc}", "error")
            return {"ok": False, "message": str(exc)}

        timer = threading.Timer(2.5, self.kill_if_needed, args=(name, process))
        timer.daemon = True
        timer.start()
        return {"ok": True, "message": f"{name} interrupted."}

    def stop_all(self) -> Dict[str, Any]:
        names = list(self.service_by_name)
        results = [self.stop(name) for name in names]
        return {"ok": all(result.get("ok") for result in results), "message": "Stop signal sent."}

    def start_all(self, commands: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        names = [service.name for service in self.services]
        command_map = commands if isinstance(commands, dict) else {}
        results = [{"service": name, **self.start(name, command_map.get(name))} for name in names]
        failed = [result for result in results if not result.get("ok")]
        message = (
            f"{len(results) - len(failed)} of {len(results)} services started."
            if failed
            else f"Start signal sent to {len(results)} service(s)."
        )
        return {"ok": not failed, "message": message, "results": results}

    def restart_selected(self, names: List[str], commands: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        if not isinstance(names, list):
            names = []
        cleaned_names = [str(name).strip() for name in names if str(name).strip()]
        command_map = commands if isinstance(commands, dict) else {}
        results: List[Dict[str, Any]] = []
        if not cleaned_names:
            return {"ok": False, "message": "Select at least one service.", "results": results}

        for name in cleaned_names:
            try:
                result = self.restart(name, command_map.get(name))
                results.append({"service": name, **result})
            except Exception as exc:
                results.append({"service": name, "ok": False, "message": str(exc)})

        failed = [result for result in results if not result.get("ok")]
        message = (
            f"{len(results) - len(failed)} of {len(results)} services refreshed."
            if failed
            else f"Refresh completed for {len(results)} service(s)."
        )
        return {"ok": not failed, "message": message, "results": results}

    def restart(self, name: str, command: Optional[str] = None) -> Dict[str, Any]:
        name = str(name or "").strip()
        with self.lock:
            self.require_service(name)
            service = self.service_by_name[name]
            command = (command or self.commands.get(name) or service.command or self.default_command).strip()
            was_running = self.is_running(name)
            self.logs[name] = []

        self.append_log(name, "Refreshing app...", "system")
        if was_running:
            stop_result = self.stop(name)
            if not stop_result.get("ok"):
                return stop_result
            deadline = time.time() + 8.0
            while time.time() < deadline:
                if not self.is_running(name):
                    break
                time.sleep(0.1)
            if self.is_running(name):
                return {"ok": False, "message": f"{name} did not stop in time."}
        else:
            self.append_log(name, f"{name} is not running; starting it.", "system")

        start_result = self.start(name, command, clear_logs=False)
        return {
            "ok": bool(start_result.get("ok")),
            "message": f"{name} refreshed." if start_result.get("ok") else start_result.get("message", "Refresh failed."),
            "start": start_result,
        }

    def clear(self, name: str) -> Dict[str, Any]:
        with self.lock:
            self.require_service(name)
            self.logs[name] = []
            return {"ok": True, "message": f"{name} logs cleared.", "next": self.log_sequence[name]}

    def clear_git(self, name: str) -> Dict[str, Any]:
        with self.lock:
            self.require_service(name)
            self.git_logs[name] = []
            return {"ok": True, "message": f"{name} git logs cleared.", "next": self.git_log_sequence[name]}

    def logs_after(self, name: str, after: int) -> Dict[str, Any]:
        with self.lock:
            self.require_service(name)
            entries = [entry for entry in self.logs[name] if entry["id"] > after]
            return {"entries": entries, "next": self.log_sequence[name]}

    def git_logs_after(self, name: str, after: int) -> Dict[str, Any]:
        with self.lock:
            self.require_service(name)
            entries = [entry for entry in self.git_logs[name] if entry["id"] > after]
            return {"entries": entries, "next": self.git_log_sequence[name]}

    def read_process_output(self, name: str, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                self.append_log(name, line.rstrip("\n"), self.classify_line(line))
        finally:
            code = process.wait()
            with self.lock:
                expected_stop = self.expected_stops.pop(name, None)
                self.capture_runtime(name)
                if self.processes.get(name) is process:
                    self.processes.pop(name, None)
                    self.last_exit_codes[name] = 0 if expected_stop else code
                    self.last_exit_reasons[name] = expected_stop or ("exited" if code == 0 else "crashed")
            if expected_stop:
                self.append_log(name, f"Process {expected_stop} with code {code}.", "system")
            else:
                level = "success" if code == 0 else "error"
                self.append_log(name, f"Process exited with code {code}.", level)

    def kill_if_needed(self, name: str, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        self.append_log(name, f"{name} did not exit after SIGTERM; killing process group.", "warn")
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass

    def append_log(self, name: str, text: str, level: str = "plain") -> None:
        timestamp = time.strftime("%H:%M:%S")
        clean_text = self.clean_output(text)
        line = clean_text if clean_text.startswith("$ ") else f"[{timestamp}] {clean_text}"
        with self.lock:
            if name not in self.logs:
                return
            self.log_sequence[name] += 1
            self.logs[name].append(
                {
                    "id": self.log_sequence[name],
                    "time": timestamp,
                    "line": line,
                    "level": level,
                }
            )
            if len(self.logs[name]) > 5000:
                self.logs[name] = self.logs[name][-5000:]

    def append_git_log(self, name: str, text: str, level: str = "plain") -> None:
        timestamp = time.strftime("%H:%M:%S")
        clean_text = self.clean_output(text)
        line = clean_text if clean_text.startswith("$ ") else f"[{timestamp}] {clean_text}"
        with self.lock:
            if name not in self.git_logs:
                return
            self.git_log_sequence[name] += 1
            self.git_logs[name].append(
                {
                    "id": self.git_log_sequence[name],
                    "time": timestamp,
                    "line": line,
                    "level": level,
                }
            )
            if len(self.git_logs[name]) > 5000:
                self.git_logs[name] = self.git_logs[name][-5000:]

    def classify_line(self, line: str) -> str:
        lowered = self.clean_output(line).lower()
        if line.startswith("$ "):
            return "command"
        if "error" in lowered or "exception" in lowered or "failed" in lowered:
            return "error"
        if "warn" in lowered:
            return "warn"
        if "success" in lowered or "running" in lowered or "compiled successfully" in lowered:
            return "success"
        if "debug" in lowered:
            return "debug"
        if "info" in lowered or "listening" in lowered:
            return "info"
        return "plain"

    def clean_output(self, text: str) -> str:
        cleaned = ANSI_RE.sub("", text)
        cleaned = cleaned.replace("\r", "")
        return "".join(char for char in cleaned if char == "\t" or char == "\n" or ord(char) >= 32)

    def status(self, name: str) -> str:
        service = self.service_by_name[name]
        if self.is_running(name):
            return "running"
        if not self.service_path(service).exists():
            return "missing"
        if self.last_exit_codes.get(name) not in (None, 0):
            return "error"
        return "idle"

    def health_state(self, name: str, service: ServiceConfig, port: str) -> Dict[str, Any]:
        if not self.service_path(service).exists():
            return {
                "state": "missing",
                "label": "MISSING",
                "message": "Service folder was not found.",
                "port": port,
                "portOpen": False,
            }

        if self.is_running(name):
            runtime = int(time.time() - self.started_at.get(name, time.time()))
            port_number = self.parse_port(port)
            if port_number:
                port_open = self.is_port_open(port_number)
                if port_open:
                    return {
                        "state": "ready",
                        "label": "READY",
                        "message": f"Process is running and port {port_number} is listening.",
                        "port": str(port_number),
                        "portOpen": True,
                    }
                if runtime < 12:
                    return {
                        "state": "starting",
                        "label": "STARTING",
                        "message": f"Process is running; waiting for port {port_number}.",
                        "port": str(port_number),
                        "portOpen": False,
                    }
                return {
                    "state": "unhealthy",
                    "label": "NO PORT",
                    "message": f"Process is running but port {port_number} is not reachable.",
                    "port": str(port_number),
                    "portOpen": False,
                }

            return {
                "state": "running",
                "label": "RUNNING",
                "message": "Process is running. No port is configured for readiness checks.",
                "port": "",
                "portOpen": None,
            }

        exit_code = self.last_exit_codes.get(name)
        exit_reason = self.last_exit_reasons.get(name, "")
        if exit_code is None:
            return {
                "state": "idle",
                "label": "IDLE",
                "message": "Service is not running yet.",
                "port": port,
                "portOpen": False,
            }
        if exit_code == 0:
            if exit_reason in {"stopped", "interrupted"}:
                return {
                    "state": "stopped",
                    "label": "STOPPED",
                    "message": f"Process was {exit_reason}.",
                    "port": port,
                    "portOpen": False,
                }
            return {
                "state": "exited",
                "label": "EXITED",
                "message": "Process exited successfully.",
                "port": port,
                "portOpen": False,
            }
        return {
            "state": "crashed",
            "label": "CRASHED",
            "message": f"Process exited with code {exit_code}.",
            "port": port,
            "portOpen": False,
        }

    def track_health_transition(self, name: str, health: Dict[str, Any]) -> None:
        state = str(health.get("state", "idle"))
        previous = self.health_states.get(name)
        if previous == state:
            return
        self.health_states[name] = state
        if state == "ready":
            self.append_log(name, str(health.get("message", "Service is ready.")), "success")
        elif state == "running":
            self.append_log(name, str(health.get("message", "Service is running.")), "success")
        elif state == "unhealthy":
            self.append_log(name, str(health.get("message", "Service health check failed.")), "warn")
        elif state == "crashed":
            self.append_log(name, str(health.get("message", "Service crashed.")), "error")
        elif state == "exited":
            self.append_log(name, str(health.get("message", "Service exited.")), "system")

    def parse_port(self, port: str) -> Optional[int]:
        match = re.search(r"\d+", str(port or ""))
        if not match:
            return None
        number = int(match.group(0))
        if 1 <= number <= 65535:
            return number
        return None

    def is_port_open(self, port: int) -> bool:
        for host in ("127.0.0.1", "localhost"):
            try:
                with socket.create_connection((host, port), timeout=0.15):
                    return True
            except OSError:
                continue
        return False

    def is_running(self, name: str) -> bool:
        process = self.processes.get(name)
        return bool(process and process.poll() is None)

    def require_service(self, name: str) -> None:
        if name not in self.service_by_name:
            raise ValueError(f"Unknown service: {name}")

    def format_uptime(self, name: str) -> str:
        started = self.started_at.get(name)
        if started:
            seconds = max(0, int(time.time() - started))
            return self.format_duration(seconds)
        last_runtime = self.last_runtime_seconds.get(name)
        if last_runtime is None:
            return "--:--:--"
        return self.format_duration(last_runtime)

    def capture_runtime(self, name: str) -> None:
        started = self.started_at.pop(name, None)
        if started is not None:
            self.last_runtime_seconds[name] = max(0, int(time.time() - started))

    def format_duration(self, seconds: int) -> str:
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def metrics(self, name: str) -> Dict[str, Any]:
        process = self.processes.get(name)
        if not process or process.poll() is not None:
            return {"cpu": "--", "memory": "--", "cpuValue": 0.0, "memoryMb": 0.0}
        cpu, memory_mb = self.process_subtree_metrics(process.pid)
        if cpu == 0.0 and memory_mb == 0.0:
            return {"cpu": "--", "memory": "--", "cpuValue": 0.0, "memoryMb": 0.0}
        return {
            "cpu": f"{cpu:.1f}%",
            "memory": f"{memory_mb:.0f} MB",
            "cpuValue": round(cpu, 1),
            "memoryMb": round(memory_mb, 1),
        }

    def total_metrics(self, service_states: List[Dict[str, Any]]) -> Dict[str, Any]:
        cpu = sum(float(service["metrics"].get("cpuValue", 0.0)) for service in service_states)
        memory_mb = sum(float(service["metrics"].get("memoryMb", 0.0)) for service in service_states)
        if cpu == 0.0 and memory_mb == 0.0:
            return {"cpu": "--", "memory": "--", "cpuValue": 0.0, "memoryMb": 0.0}
        return {
            "cpu": f"{cpu:.1f}%",
            "memory": f"{memory_mb:.0f} MB",
            "cpuValue": round(cpu, 1),
            "memoryMb": round(memory_mb, 1),
        }

    def process_subtree_metrics(self, root_pid: int) -> Tuple[float, float]:
        try:
            result = subprocess.run(
                ["ps", "-axo", "pid=", "-o", "ppid=", "-o", "%cpu=", "-o", "rss="],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
            )
        except Exception:
            return 0.0, 0.0

        rows: Dict[int, Tuple[int, float, int]] = {}
        children: Dict[int, List[int]] = {}
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            try:
                pid = int(parts[0])
                ppid = int(parts[1])
                cpu = float(parts[2])
                rss = int(parts[3])
            except ValueError:
                continue
            rows[pid] = (ppid, cpu, rss)
            children.setdefault(ppid, []).append(pid)

        total_cpu = 0.0
        total_rss = 0
        stack = [root_pid]
        seen = set()
        while stack:
            pid = stack.pop()
            if pid in seen:
                continue
            seen.add(pid)
            row = rows.get(pid)
            if row:
                _, cpu, rss = row
                total_cpu += cpu
                total_rss += rss
            stack.extend(children.get(pid, []))
        return total_cpu, total_rss / 1024

    def discover_port(self, service: ServiceConfig) -> str:
        env_file = self.service_path(service) / ".env"
        if not env_file.exists():
            return ""
        try:
            content = env_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""
        for key in ["PORT", "APP_PORT", "HTTP_PORT", "GRPC_PORT"]:
            match = re.search(rf"^{key}\s*=\s*([^\n#]+)", content, flags=re.MULTILINE)
            if match:
                return match.group(1).strip().strip("\"'")
        return ""

    def runtime_label(self) -> str:
        node = self.run_short(["node", "--version"]) or "Node n/a"
        pnpm = self.run_short(["pnpm", "--version"]) or "pnpm n/a"
        return f"UTF-8   {node}   pnpm {pnpm}"

    def run_short(self, args: List[str]) -> str:
        try:
            result = subprocess.run(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
                env={**os.environ, "PATH": self.extended_path(os.environ.get("PATH", ""))},
            )
            return result.stdout.strip().splitlines()[0]
        except Exception:
            return ""

    def git_state(self, service: ServiceConfig, force: bool = False) -> Dict[str, Any]:
        cached = self.git_cache.get(service.name)
        now = time.time()
        if not force and cached and now - float(cached.get("at", 0)) < 3.0:
            return dict(cached["state"])

        state = self.compute_git_state(service)
        self.git_cache[service.name] = {"at": now, "state": state}
        return dict(state)

    def compute_git_state(self, service: ServiceConfig) -> Dict[str, Any]:
        path = self.service_path(service)
        base: Dict[str, Any] = {
            "isRepo": False,
            "branch": "",
            "upstream": "",
            "ahead": 0,
            "behind": 0,
            "dirty": False,
            "dirtyCount": 0,
            "originDevelopment": False,
            "developmentAhead": 0,
            "developmentBehind": 0,
            "developmentStatus": "unknown",
            "developmentLabel": "NO GIT",
            "lastDevelopmentSync": service.git.get("development_synced_at", ""),
            "lastDevelopmentSyncStatus": service.git.get("development_sync_status", ""),
            "lastDevelopmentSyncMessage": service.git.get("development_sync_message", ""),
        }
        if not path.exists():
            base["developmentLabel"] = "MISSING"
            return base

        ok, _, _, _ = self.run_git(path, ["rev-parse", "--is-inside-work-tree"], timeout=2)
        if not ok:
            return base

        branch_ok, branch, _, _ = self.run_git(path, ["rev-parse", "--abbrev-ref", "HEAD"], timeout=2)
        if not branch_ok or branch == "HEAD":
            _, branch, _, _ = self.run_git(path, ["rev-parse", "--short", "HEAD"], timeout=2)

        dirty_ok, dirty_out, _, _ = self.run_git(path, ["status", "--porcelain"], timeout=3)
        dirty_lines = [line for line in dirty_out.splitlines() if line.strip()] if dirty_ok else []

        upstream = ""
        ahead = 0
        behind = 0
        upstream_ok, upstream_out, _, _ = self.run_git(
            path,
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            timeout=2,
        )
        if upstream_ok:
            upstream = upstream_out.strip()
            counts_ok, counts, _, _ = self.run_git(path, ["rev-list", "--left-right", "--count", "HEAD...@{u}"], timeout=3)
            if counts_ok:
                ahead, behind = self.parse_ahead_behind(counts)

        origin_dev_ok, _, _, _ = self.run_git(
            path,
            ["show-ref", "--verify", "--quiet", "refs/remotes/origin/development"],
            timeout=2,
        )
        local_dev_ok, _, _, _ = self.run_git(
            path,
            ["show-ref", "--verify", "--quiet", "refs/heads/development"],
            timeout=2,
        )

        development_ahead = 0
        development_behind = 0
        development_status = "unavailable"
        development_label = "NO ORIGIN/DEV"
        if origin_dev_ok:
            compare_ref = "HEAD" if branch != "development" else "development"
            if branch == "development" or local_dev_ok:
                compare_ref = "development" if local_dev_ok else "HEAD"
            counts_ok, counts, _, _ = self.run_git(
                path,
                ["rev-list", "--left-right", "--count", f"{compare_ref}...origin/development"],
                timeout=3,
            )
            if counts_ok:
                development_ahead, development_behind = self.parse_ahead_behind(counts)
                if development_ahead == 0 and development_behind == 0:
                    development_status = "synced"
                    development_label = "SYNCED"
                elif development_behind > 0 and development_ahead > 0:
                    development_status = "diverged"
                    development_label = f"{development_ahead} ahead / {development_behind} behind"
                elif development_behind > 0:
                    development_status = "behind"
                    development_label = f"{development_behind} behind"
                else:
                    development_status = "ahead"
                    development_label = f"{development_ahead} ahead"

        return {
            **base,
            "isRepo": True,
            "branch": branch.strip(),
            "upstream": upstream,
            "ahead": ahead,
            "behind": behind,
            "dirty": bool(dirty_lines),
            "dirtyCount": len(dirty_lines),
            "originDevelopment": origin_dev_ok,
            "localDevelopment": local_dev_ok,
            "developmentAhead": development_ahead,
            "developmentBehind": development_behind,
            "developmentStatus": development_status,
            "developmentLabel": development_label,
        }

    def parse_ahead_behind(self, counts: str) -> Tuple[int, int]:
        parts = counts.strip().split()
        if len(parts) < 2:
            return 0, 0
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            return 0, 0

    def git_action(self, name: str, action: str, value: Optional[str] = None) -> Dict[str, Any]:
        name = str(name or "").strip()
        action = str(action or "").strip()
        value = str(value or "").strip()
        with self.lock:
            self.require_service(name)
            service = self.service_by_name[name]

        if action == "refresh":
            result = self.git_refresh(service)
            git = self.git_state(service, force=True)
            return {**result, "git": git}

        path = self.service_path(service)
        ok, _, stderr, _ = self.run_git(path, ["rev-parse", "--is-inside-work-tree"], timeout=2)
        if not ok:
            return {"ok": False, "message": stderr.strip() or f"{name} is not a git repository."}

        actions = {
            "switch-development": self.git_switch_development,
            "pull-development": self.git_pull_development,
            "sync-current-branch": self.git_sync_current_branch,
            "checkout-development": self.git_checkout_development,
            "checkout-pull-development": self.git_pull_development,
            "sync-development": self.git_sync_development,
            "pull-current-branch": self.git_pull_current_branch,
            "add-all": self.git_add_all,
            "push": self.git_push,
        }
        value_actions = {
            "checkout-new-branch": self.git_checkout_new_branch,
            "checkout-branch": self.git_checkout_branch,
            "commit": self.git_commit,
        }
        handler = actions.get(action)
        value_handler = value_actions.get(action)
        if not handler and not value_handler:
            return {"ok": False, "message": f"Unknown git action: {action}"}

        result = value_handler(service, value) if value_handler else handler(service)
        with self.lock:
            sync_key = (
                "development"
                if action in {
                    "switch-development",
                    "pull-development",
                    "checkout-development",
                    "checkout-pull-development",
                    "sync-development",
                }
                else "current"
            )
            service.git[f"{sync_key}_synced_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            service.git[f"{sync_key}_sync_status"] = "ok" if result.get("ok") else "error"
            service.git[f"{sync_key}_sync_message"] = str(result.get("message", ""))[:500]
            if sync_key == "development":
                service.git["development_synced_at"] = service.git[f"{sync_key}_synced_at"]
                service.git["development_sync_status"] = service.git[f"{sync_key}_sync_status"]
                service.git["development_sync_message"] = service.git[f"{sync_key}_sync_message"]
            self.save_config()
            self.git_cache.pop(service.name, None)
        git = self.git_state(service, force=True)
        return {**result, "git": git}

    def git_action_selected(self, names: List[str], action: str, value: Optional[str] = None) -> Dict[str, Any]:
        if not isinstance(names, list):
            names = []
        cleaned_names = [str(name).strip() for name in names if str(name).strip()]
        results: List[Dict[str, Any]] = []
        if not cleaned_names:
            return {"ok": False, "message": "Select at least one service.", "results": results}

        for name in cleaned_names:
            try:
                result = self.git_action(name, action, value)
                results.append({"service": name, **result})
            except Exception as exc:
                message = str(exc)
                results.append({"service": name, "ok": False, "message": message})
                if name in self.git_logs:
                    self.append_git_log(name, message, "error")

        failed = [result for result in results if not result.get("ok")]
        message = (
            f"{len(results) - len(failed)} of {len(results)} git actions completed."
            if failed
            else f"Git action completed for {len(results)} service(s)."
        )
        return {"ok": not failed, "message": message, "results": results}

    def git_refresh(self, service: ServiceConfig) -> Dict[str, Any]:
        self.git_cache.pop(service.name, None)
        self.append_git_log(service.name, "Git status refreshed.", "system")
        return {"ok": True, "message": "Git status refreshed."}

    def git_fetch(self, service: ServiceConfig) -> Dict[str, Any]:
        return self.run_git_action(service, "fetch origin --prune", ["fetch", "origin", "--prune"])

    def git_checkout_new_branch(self, service: ServiceConfig, branch: str) -> Dict[str, Any]:
        branch = str(branch or "").strip()
        if not branch:
            return {"ok": False, "message": "Branch name is required."}
        if branch.startswith("-") or any(char.isspace() for char in branch):
            return {"ok": False, "message": f"Invalid branch name: {branch}"}

        path = self.service_path(service)
        valid, normalized, stderr, _ = self.run_git(path, ["check-ref-format", "--branch", branch], timeout=2)
        if not valid:
            return {"ok": False, "message": stderr.strip() or f"Invalid branch name: {branch}"}

        branch_name = normalized.strip() or branch
        return self.run_git_action(service, f"checkout -b {branch_name}", ["checkout", "-b", branch_name])

    def git_add_all(self, service: ServiceConfig) -> Dict[str, Any]:
        return self.run_git_action(service, "add -A", ["add", "-A"])

    def git_commit(self, service: ServiceConfig, message: str) -> Dict[str, Any]:
        message = str(message or "").strip()
        if not message:
            return {"ok": False, "message": "Commit message is required."}
        add_result = self.git_add_all(service)
        if not add_result.get("ok"):
            return add_result
        commit_result = self.run_git_action(service, f"commit -m {message}", ["commit", "-m", message])
        return {
            "ok": bool(commit_result.get("ok")),
            "message": "\n".join(
                part
                for part in [str(add_result.get("message", "")).strip(), str(commit_result.get("message", "")).strip()]
                if part
            ),
        }

    def git_push(self, service: ServiceConfig) -> Dict[str, Any]:
        git = self.compute_git_state(service)
        branch = str(git.get("branch", "")).strip()
        upstream = str(git.get("upstream", "")).strip()
        if not branch or branch == "HEAD":
            return {"ok": False, "message": "Current branch could not be resolved."}
        if upstream:
            return self.run_git_action(service, "push", ["push"])
        return self.run_git_action(service, f"push -u origin {branch}", ["push", "-u", "origin", branch])

    def git_pull_current_branch(self, service: ServiceConfig) -> Dict[str, Any]:
        git = self.compute_git_state(service)
        branch = str(git.get("branch", "")).strip()
        upstream = str(git.get("upstream", "")).strip()
        if not branch or branch == "HEAD":
            return {"ok": False, "message": "Current branch could not be resolved."}
        if upstream:
            return self.run_git_action(service, "pull --ff-only", ["pull", "--ff-only"])
        return self.run_git_action(service, f"pull --ff-only origin {branch}", ["pull", "--ff-only", "origin", branch])

    def git_switch_development(self, service: ServiceConfig) -> Dict[str, Any]:
        steps = [("fetch origin --prune", ["fetch", "origin", "--prune"])]
        fetch_result = self.run_git_steps(service, steps)
        if not fetch_result.get("ok"):
            return fetch_result
        checkout_result = self.git_checkout_development(service)
        return {
            "ok": bool(checkout_result.get("ok")),
            "message": "\n".join(
                part
                for part in [str(fetch_result.get("message", "")).strip(), str(checkout_result.get("message", "")).strip()]
                if part
            ),
        }

    def git_pull_development(self, service: ServiceConfig) -> Dict[str, Any]:
        switch_result = self.git_switch_development(service)
        if not switch_result.get("ok"):
            return switch_result
        pull_result = self.run_git_action(service, "pull --ff-only origin development", ["pull", "--ff-only", "origin", "development"])
        return {
            "ok": bool(pull_result.get("ok")),
            "message": "\n".join(
                part
                for part in [str(switch_result.get("message", "")).strip(), str(pull_result.get("message", "")).strip()]
                if part
            ),
        }

    def git_sync_current_branch(self, service: ServiceConfig) -> Dict[str, Any]:
        fetch_result = self.run_git_action(service, "fetch origin --prune", ["fetch", "origin", "--prune"])
        if not fetch_result.get("ok"):
            return fetch_result

        git = self.compute_git_state(service)
        branch = str(git.get("branch", "")).strip()
        upstream = str(git.get("upstream", "")).strip()
        if not branch or branch == "HEAD":
            return {"ok": False, "message": "Current branch could not be resolved."}

        target = upstream or f"origin/{branch}"
        if not upstream:
            path = self.service_path(service)
            remote_ok, _, _, _ = self.run_git(
                path,
                ["show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"],
                timeout=2,
            )
            if not remote_ok:
                return {"ok": False, "message": f"No upstream or origin/{branch} branch was found."}

        merge_result = self.run_git_action(service, f"merge {target}", ["merge", target])
        return {
            "ok": bool(merge_result.get("ok")),
            "message": "\n".join(
                part for part in [str(fetch_result.get("message", "")).strip(), str(merge_result.get("message", "")).strip()] if part
            ),
        }

    def git_checkout_development(self, service: ServiceConfig) -> Dict[str, Any]:
        path = self.service_path(service)
        local_ok, _, _, _ = self.run_git(path, ["show-ref", "--verify", "--quiet", "refs/heads/development"], timeout=2)
        origin_ok, _, _, _ = self.run_git(path, ["show-ref", "--verify", "--quiet", "refs/remotes/origin/development"], timeout=2)
        if local_ok:
            return self.run_git_action(service, "checkout development", ["checkout", "development"])
        if origin_ok:
            return self.run_git_action(
                service,
                "checkout -b development --track origin/development",
                ["checkout", "-b", "development", "--track", "origin/development"],
            )
        return {"ok": False, "message": "origin/development branch was not found. Run fetch first."}

    def git_checkout_branch(self, service: ServiceConfig, branch: str) -> Dict[str, Any]:
        branch = str(branch or "").strip()
        if not branch:
            return {"ok": False, "message": "Branch name is required."}
        if branch.startswith("-") or any(char.isspace() for char in branch):
            return {"ok": False, "message": f"Invalid branch name: {branch}"}

        path = self.service_path(service)
        valid, normalized, stderr, _ = self.run_git(path, ["check-ref-format", "--branch", branch], timeout=2)
        branch = (normalized.strip() if valid else "") or branch

        local_ok, _, _, _ = self.run_git(path, ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], timeout=2)
        if local_ok:
            return self.run_git_action(service, f"checkout {branch}", ["checkout", branch])

        origin_ok, _, _, _ = self.run_git(path, ["show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"], timeout=2)
        if origin_ok:
            return self.run_git_action(
                service,
                f"checkout -b {branch} --track origin/{branch}",
                ["checkout", "-b", branch, "--track", f"origin/{branch}"],
            )
        return {"ok": False, "message": f"Branch not found locally or on origin: {branch}"}

    def git_list_branches(self, name: str) -> Dict[str, Any]:
        name = str(name or "").strip()
        with self.lock:
            self.require_service(name)
            service = self.service_by_name[name]

        path = self.service_path(service)
        if not path.exists():
            return {"ok": False, "message": f"Directory not found: {path}", "branches": [], "current": ""}

        repo_ok, _, stderr, _ = self.run_git(path, ["rev-parse", "--is-inside-work-tree"], timeout=2)
        if not repo_ok:
            return {"ok": False, "message": stderr.strip() or f"{name} is not a git repository.", "branches": [], "current": ""}

        cur_ok, current, _, _ = self.run_git(path, ["rev-parse", "--abbrev-ref", "HEAD"], timeout=2)
        current = current.strip() if cur_ok else ""
        local_ok, local_out, _, _ = self.run_git(path, ["for-each-ref", "--format=%(refname:short)", "refs/heads"], timeout=5)
        remote_ok, remote_out, _, _ = self.run_git(
            path, ["for-each-ref", "--format=%(refname:short)", "refs/remotes/origin"], timeout=5
        )

        local = [line.strip() for line in local_out.splitlines() if line.strip()] if local_ok else []
        remote = []
        if remote_ok:
            for line in remote_out.splitlines():
                ref = line.strip()
                if not ref or ref.endswith("/HEAD"):
                    continue
                remote.append(ref[len("origin/"):] if ref.startswith("origin/") else ref)

        branches: List[Dict[str, Any]] = []
        seen = set()
        for branch in local:
            branches.append({"name": branch, "local": True, "remote": branch in remote, "current": branch == current})
            seen.add(branch)
        for branch in remote:
            if branch in seen:
                continue
            branches.append({"name": branch, "local": False, "remote": True, "current": False})
            seen.add(branch)
        return {"ok": True, "current": current, "branches": branches}

    def git_sync_development(self, service: ServiceConfig) -> Dict[str, Any]:
        steps = [
            ("fetch origin --prune", ["fetch", "origin", "--prune"]),
            ("merge origin/development", ["merge", "origin/development"]),
        ]
        return self.run_git_steps(service, steps)

    def git_merge_origin_development(self, service: ServiceConfig) -> Dict[str, Any]:
        steps = [
            ("fetch origin --prune", ["fetch", "origin", "--prune"]),
            ("merge --no-edit origin/development", ["merge", "--no-edit", "origin/development"]),
        ]
        return self.run_git_steps(service, steps)

    def run_git_steps(self, service: ServiceConfig, steps: List[Tuple[str, List[str]]]) -> Dict[str, Any]:
        messages = []
        for label, args in steps:
            result = self.run_git_action(service, label, args)
            messages.append(result.get("message", label))
            if not result.get("ok"):
                # Surface only the failing step so the reason is not buried under
                # the (successful) output of earlier steps such as `fetch`.
                return {"ok": False, "message": result.get("message") or f"git {label} failed."}
        return {"ok": True, "message": "\n".join(messages)}

    def run_git_action(self, service: ServiceConfig, label: str, args: List[str]) -> Dict[str, Any]:
        path = self.service_path(service)
        self.append_git_log(service.name, f"$ git {label}", "command")
        ok, stdout, stderr, code = self.run_git(path, args, timeout=90)
        output = "\n".join(part for part in [stdout.strip(), stderr.strip()] if part)
        if output:
            for line in output.splitlines():
                self.append_git_log(service.name, line, self.classify_line(line) if ok else "error")
        if ok:
            message = output or f"git {label} completed."
            self.append_git_log(service.name, message.splitlines()[-1], "success")
            return {"ok": True, "message": message}
        message = output or f"git {label} failed with code {code}."
        self.append_git_log(service.name, message.splitlines()[-1], "error")
        return {"ok": False, "message": message}

    def run_git_terminal_command(self, name: str, command: str) -> Dict[str, Any]:
        name = str(name or "").strip()
        command = str(command or "").strip()
        if not command:
            return {"ok": False, "message": "Command is required."}

        with self.lock:
            self.require_service(name)
            service = self.service_by_name[name]

        if command == "clear":
            return self.clear_git(name)

        cwd = self.service_path(service)
        if not cwd.exists():
            self.append_git_log(name, f"Directory not found: {cwd}", "error")
            return {"ok": False, "message": f"Directory not found: {cwd}"}

        args = self.shell_args(command)
        self.append_git_log(name, f"$ {command}", "command")
        try:
            result = subprocess.run(
                args,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=120,
                env=self.command_env({"GIT_MERGE_AUTOEDIT": "no"}),
            )
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            for line in output.splitlines():
                self.append_git_log(name, line, self.classify_line(line))
            self.append_git_log(name, "Command timed out.", "error")
            return {"ok": False, "message": "Command timed out."}
        except Exception as exc:
            self.append_git_log(name, str(exc), "error")
            return {"ok": False, "message": str(exc)}

        output = result.stdout.strip()
        if output:
            for line in output.splitlines():
                self.append_git_log(name, line, self.classify_line(line) if result.returncode == 0 else "error")
        level = "success" if result.returncode == 0 else "error"
        self.append_git_log(name, f"Process exited with code {result.returncode}.", level)
        with self.lock:
            self.git_cache.pop(name, None)
        return {
            "ok": result.returncode == 0,
            "message": output or f"Process exited with code {result.returncode}.",
            "git": self.git_state(service, force=True),
        }

    def run_git(self, path: Path, args: List[str], timeout: int = 20) -> Tuple[bool, str, str, int]:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=str(path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=timeout,
                env={
                    **os.environ,
                    "PATH": self.extended_path(os.environ.get("PATH", "")),
                    "GIT_MERGE_AUTOEDIT": "no",
                },
            )
            return result.returncode == 0, result.stdout.strip(), result.stderr.strip(), result.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return False, stdout.strip(), (stderr.strip() or "Git command timed out."), 124
        except Exception as exc:
            return False, "", str(exc), 1

    def extended_path(self, current: str) -> str:
        home = Path.home()
        nvm_bins = sorted(glob.glob(str(home / ".nvm" / "versions" / "node" / "*" / "bin")), reverse=True)
        common = [
            str(home / ".local" / "share" / "pnpm"),
            str(home / "Library" / "pnpm"),
            str(home / ".volta" / "bin"),
            str(home / ".asdf" / "shims"),
            str(home / ".bun" / "bin"),
            str(home / ".yarn" / "bin"),
            str(home / ".npm-global" / "bin"),
            *nvm_bins,
            "/opt/homebrew/bin",
            "/opt/homebrew/sbin",
            "/usr/local/bin",
            "/usr/local/sbin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        ]
        existing = [item for item in current.split(os.pathsep) if item]
        combined: List[str] = []
        for item in [*common, *existing]:
            if item not in combined:
                combined.append(item)
        return os.pathsep.join(combined)

    def command_env(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        env = os.environ.copy()
        env["PATH"] = self.extended_path(env.get("PATH", ""))
        env["TERM"] = env.get("TERM", "xterm-256color")
        if extra:
            env.update(extra)
        if env.get("FORCE_COLOR"):
            env.pop("NO_COLOR", None)
        return env

    def shell_args(self, command: str) -> List[str]:
        shell = shutil.which("zsh") or shutil.which("bash") or "/bin/sh"
        if shell.endswith("zsh"):
            startup = (
                'for f in "$HOME/.zprofile" "$HOME/.zshrc"; do '
                '[ -r "$f" ] && source "$f"; '
                "done; hash -r 2>/dev/null || true"
            )
            return [shell, "-lc", f"{startup}\n{command}"]
        if shell.endswith("bash"):
            startup = (
                'for f in "$HOME/.bash_profile" "$HOME/.bashrc" "$HOME/.profile"; do '
                '[ -r "$f" ] && source "$f"; '
                "done; hash -r 2>/dev/null || true"
            )
            return [shell, "-lc", f"{startup}\n{command}"]
        return [shell, "-c", command]

    def open_path(self, target: str, service_name: Optional[str] = None) -> Dict[str, Any]:
        if target == "config":
            path = CONFIG_PATH
        elif target == "readme":
            path = APP_DIR / "README.md"
        elif target == "folder" and service_name:
            service = self.service_by_name[service_name]
            path = self.service_path(service)
        else:
            path = self.root_dir

        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            elif os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(path)])
            return {"ok": True, "message": str(path)}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def open_terminal(self, service_name: Optional[str] = None) -> Dict[str, Any]:
        if service_name and service_name in self.service_by_name:
            service = self.service_by_name[service_name]
            path = self.service_path(service)
        else:
            path = self.root_dir

        if not path.exists():
            return {"ok": False, "message": f"Path not found: {path}"}

        try:
            if sys.platform == "darwin":
                escaped = str(path).replace("\\", "\\\\").replace('"', '\\"')
                subprocess.Popen(
                    [
                        "osascript",
                        "-e",
                        'tell application "Terminal"',
                        "-e",
                        "activate",
                        "-e",
                        f'do script "cd \\"{escaped}\\""',
                        "-e",
                        "end tell",
                    ]
                )
            elif os.name == "nt":
                subprocess.Popen(["cmd.exe", "/c", "start", "cmd.exe", "/K", f"cd /d {path}"])
            else:
                terminal = shutil.which("x-terminal-emulator") or shutil.which("gnome-terminal") or shutil.which("konsole")
                if not terminal:
                    return {"ok": False, "message": "No terminal application found."}
                subprocess.Popen([terminal], cwd=str(path))
            return {"ok": True, "message": str(path)}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}


class MiroserviceManagerHandler(BaseHTTPRequestHandler):
    manager: ServiceManager

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/state":
                self.send_json(self.manager.state())
            elif parsed.path == "/api/logs":
                params = parse_qs(parsed.query)
                service = params.get("service", [""])[0]
                after = int(params.get("after", ["0"])[0] or 0)
                self.send_json(self.manager.logs_after(service, after))
            elif parsed.path == "/api/git/logs":
                params = parse_qs(parsed.query)
                service = params.get("service", [""])[0]
                after = int(params.get("after", ["0"])[0] or 0)
                self.send_json(self.manager.git_logs_after(service, after))
            elif parsed.path == "/api/git/branches":
                params = parse_qs(parsed.query)
                service = params.get("service", [""])[0]
                self.send_json(self.manager.git_list_branches(service))
            else:
                self.serve_static(parsed.path)
        except Exception as exc:
            self.send_json({"ok": False, "message": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        payload = self.read_json()
        try:
            if parsed.path == "/api/start":
                self.send_json(self.manager.start(payload.get("service", ""), payload.get("command")))
            elif parsed.path == "/api/start-all":
                self.send_json(self.manager.start_all(payload.get("commands", {})))
            elif parsed.path == "/api/start-selected":
                services = payload.get("services", [])
                commands = payload.get("commands", {})
                results = [self.manager.start(name, commands.get(name)) for name in services]
                self.send_json({"ok": all(result.get("ok") for result in results), "results": results})
            elif parsed.path == "/api/stop-selected":
                services = payload.get("services", [])
                results = [self.manager.stop(name) for name in services]
                self.send_json({"ok": all(result.get("ok") for result in results), "results": results})
            elif parsed.path == "/api/stop":
                self.send_json(self.manager.stop(payload.get("service", "")))
            elif parsed.path == "/api/refresh":
                self.send_json(self.manager.restart(payload.get("service", ""), payload.get("command")))
            elif parsed.path == "/api/refresh-selected":
                self.send_json(self.manager.restart_selected(payload.get("services", []), payload.get("commands", {})))
            elif parsed.path == "/api/interrupt":
                self.send_json(self.manager.interrupt(payload.get("service", "")))
            elif parsed.path == "/api/stop-all":
                self.send_json(self.manager.stop_all())
            elif parsed.path == "/api/clear":
                self.send_json(self.manager.clear(payload.get("service", "")))
            elif parsed.path == "/api/git/clear":
                self.send_json(self.manager.clear_git(payload.get("service", "")))
            elif parsed.path == "/api/open":
                self.send_json(self.manager.open_path(payload.get("target", ""), payload.get("service")))
            elif parsed.path == "/api/open-terminal":
                self.send_json(self.manager.open_terminal(payload.get("service")))
            elif parsed.path == "/api/services/add":
                self.send_json(self.manager.add_service(payload))
            elif parsed.path == "/api/services/update":
                self.send_json(self.manager.update_service(payload.get("service", ""), payload))
            elif parsed.path == "/api/services/remove":
                self.send_json(self.manager.remove_service(payload.get("service", "")))
            elif parsed.path == "/api/git/action":
                self.send_json(self.manager.git_action(payload.get("service", ""), payload.get("action", ""), payload.get("value", "")))
            elif parsed.path == "/api/git/action-selected":
                self.send_json(
                    self.manager.git_action_selected(payload.get("services", []), payload.get("action", ""), payload.get("value", ""))
                )
            elif parsed.path == "/api/git/terminal":
                self.send_json(self.manager.run_git_terminal_command(payload.get("service", ""), payload.get("command", "")))
            elif parsed.path == "/api/choose-folder":
                self.send_json(self.manager.choose_folder())
            else:
                self.send_json({"ok": False, "message": "Unknown endpoint."}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"ok": False, "message": str(exc)}, HTTPStatus.BAD_REQUEST)

    def read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def serve_static(self, path: str) -> None:
        clean = unquote(path).lstrip("/") or "index.html"
        file_path = (STATIC_DIR / clean).resolve()
        if not str(file_path).startswith(str(STATIC_DIR.resolve())) or not file_path.exists() or file_path.is_dir():
            file_path = STATIC_DIR / "index.html"
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
        }.get(file_path.suffix, "application/octet-stream")
        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: Dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        return


class LocalThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def server_bind(self) -> None:
        if self.allow_reuse_address and hasattr(socket, "SO_REUSEADDR"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(self.server_address)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


def find_free_port(preferred: int) -> int:
    for port in range(preferred, preferred + 80):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("No free local port found.")


def create_server(port: int) -> Tuple[ServiceManager, LocalThreadingHTTPServer, str]:
    manager = ServiceManager()
    actual_port = find_free_port(port)

    class Handler(MiroserviceManagerHandler):
        pass

    Handler.manager = manager
    server = LocalThreadingHTTPServer(("127.0.0.1", actual_port), Handler)
    url = f"http://127.0.0.1:{actual_port}"
    return manager, server, url


def run_server_loop(manager: ServiceManager, server: LocalThreadingHTTPServer, url: str, open_browser: bool) -> None:
    if open_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()

    print(f"Miroservice Manager is running at {url}", flush=True)
    print("Press Ctrl+C to stop the manager. Running services can be stopped from the UI.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping services...", flush=True)
        manager.stop_all()
    finally:
        server.server_close()


def run_browser(port: int, open_browser: bool) -> None:
    manager, server, url = create_server(port)
    run_server_loop(manager, server, url, open_browser)


def run_desktop(port: int, fallback_browser: bool) -> None:
    manager, server, url = create_server(port)
    try:
        import webview  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        print(
            "pywebview is not installed; falling back to browser mode. "
            "Run ./run_desktop.sh to create a desktop-capable environment.",
            flush=True,
        )
        run_server_loop(manager, server, url, fallback_browser)
        return

    print(f"Miroservice Manager desktop is running at {url}", flush=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        webview.create_window(
            "Miroservice Manager",
            url,
            width=1450,
            height=900,
            min_size=(1180, 720),
            text_select=True,
        )
        webview.start(debug=False)
    except Exception as exc:
        print(f"Desktop window failed: {exc}", flush=True)
        if fallback_browser:
            webbrowser.open(url)
            print("Browser fallback is open. Press Ctrl+C here to stop Miroservice Manager.", flush=True)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\nStopping services...", flush=True)
    finally:
        manager.stop_all()
        server.shutdown()
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Miroservice Manager local service manager")
    parser.add_argument("--port", type=int, default=8765, help="Preferred local port")
    default_desktop = bool(getattr(sys, "frozen", False))
    parser.add_argument(
        "--desktop",
        action="store_true",
        default=default_desktop,
        help="Open Miroservice Manager in a native WebView window",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Force browser mode even in desktop package",
    )
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically")
    args = parser.parse_args()
    if args.browser:
        run_browser(args.port, not args.no_browser)
    elif args.desktop:
        run_desktop(args.port, not args.no_browser)
    else:
        run_browser(args.port, not args.no_browser)


if __name__ == "__main__":
    main()
