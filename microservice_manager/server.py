from __future__ import annotations

import argparse
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
from dataclasses import dataclass
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
        self.expected_stops: Dict[str, str] = {}
        self.commands: Dict[str, str] = {service.name: service.command or self.default_command for service in self.services}
        self.logs: Dict[str, List[Dict[str, Any]]] = {service.name: [] for service in self.services}
        self.log_sequence: Dict[str, int] = {service.name: 0 for service in self.services}

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
        return {
            "name": service.name,
            "directory": service.directory,
            "command": self.commands.get(name, service.command or self.default_command),
            "status": self.status(name),
            "uptime": self.format_uptime(name),
            "exitCode": self.last_exit_codes.get(name),
            "env": service.env,
            "port": service.port or self.discover_port(service),
            "path": str(self.service_path(service)),
            "metrics": self.metrics(name),
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
            self.last_runtime_seconds[name] = None
            self.last_exit_codes[name] = None
            self.save_config()
            self.append_log(name, f"Service added: {directory}", "system")
        return {"ok": True, "message": f"{name} added.", "service": self.service_state(name)}

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
            self.started_at.pop(name, None)
            self.last_runtime_seconds.pop(name, None)
            self.last_exit_codes.pop(name, None)
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

    def start(self, name: str, command: Optional[str] = None) -> Dict[str, Any]:
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
            if not cwd.exists():
                self.last_exit_codes[name] = 127
                self.append_log(name, f"Directory not found: {cwd}", "error")
                return {"ok": False, "message": f"Directory not found: {cwd}"}

            self.commands[name] = command
            if service.command != command:
                service.command = command
                self.save_config()
            self.last_exit_codes[name] = None
            self.last_runtime_seconds[name] = None

        env = os.environ.copy()
        env["PATH"] = self.extended_path(env.get("PATH", ""))
        env["FORCE_COLOR"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        env["TERM"] = env.get("TERM", "xterm-256color")

        shell = shutil.which("zsh") or shutil.which("bash") or "/bin/sh"
        args = [shell, "-lc", command] if shell.endswith("zsh") or shell.endswith("bash") else [shell, "-c", command]

        self.append_log(name, f"$ {command}", "command")
        self.append_log(name, f"cwd: {cwd}", "system")

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

    def clear(self, name: str) -> Dict[str, Any]:
        with self.lock:
            self.require_service(name)
            self.logs[name] = []
            return {"ok": True, "message": f"{name} logs cleared.", "next": self.log_sequence[name]}

    def logs_after(self, name: str, after: int) -> Dict[str, Any]:
        with self.lock:
            self.require_service(name)
            entries = [entry for entry in self.logs[name] if entry["id"] > after]
            return {"entries": entries, "next": self.log_sequence[name]}

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

    def extended_path(self, current: str) -> str:
        common = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
        existing = [item for item in current.split(os.pathsep) if item]
        combined: List[str] = []
        for item in [*common, *existing]:
            if item not in combined:
                combined.append(item)
        return os.pathsep.join(combined)

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
            elif parsed.path == "/api/interrupt":
                self.send_json(self.manager.interrupt(payload.get("service", "")))
            elif parsed.path == "/api/stop-all":
                self.send_json(self.manager.stop_all())
            elif parsed.path == "/api/clear":
                self.send_json(self.manager.clear(payload.get("service", "")))
            elif parsed.path == "/api/open":
                self.send_json(self.manager.open_path(payload.get("target", ""), payload.get("service")))
            elif parsed.path == "/api/open-terminal":
                self.send_json(self.manager.open_terminal(payload.get("service")))
            elif parsed.path == "/api/services/add":
                self.send_json(self.manager.add_service(payload))
            elif parsed.path == "/api/services/remove":
                self.send_json(self.manager.remove_service(payload.get("service", "")))
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
