# Miroservice Manager

Miroservice Manager is a small Python-powered desktop dashboard for starting and watching local microservices from one place. It runs a local manager at `127.0.0.1` and opens the UI in your browser or a native WebView window.

## Run as a Desktop App

```bash
cd microservice-manager
./run_desktop.sh
```

On macOS you can also double-click `MiroserviceManager.command`.

The desktop launcher creates a local `.venv`, installs `pywebview`, starts the Python service manager, and opens Miroservice Manager in a native WebView window.

## Build a Shareable macOS App

```bash
cd microservice-manager
./build_macos_app.sh
```

The generated app will be available at:

```text
dist/Miroservice Manager.app
```

Users do not need this source folder to run the built app. Their service configuration is stored outside the app bundle at:

```text
~/Library/Application Support/Miroservice Manager/services.json
```

## Run in Browser Mode

```bash
cd microservice-manager
./run.sh
```

The app has no pip dependencies. The Python process owns all start/stop/log streaming work; the browser is only the UI shell.

## What it does

- Lets each user add their own services by name and project folder.
- Persists service configuration in the user's application support directory.
- Starts selected services with `pnpm start:dev` by default.
- Lets you override the command per active service tab.
- Streams each service terminal output into its own tab.
- Filters visible terminal output and can copy selected or visible terminal text.
- Stops individual services or all running services.
- Sends `SIGINT` to the active service with `Cmd+C` or `Ctrl+C`.
- Opens the current service folder in the system Terminal.
- Reads `.env` files to show a detected port when a common key such as `PORT`, `APP_PORT`, `HTTP_PORT`, or `GRPC_PORT` exists.

## Configure services

Add services from the UI with a name, folder path, and optional command.

```json
{
  "root": "/Users/example",
  "default_command": "pnpm start:dev",
  "services": [
    {
      "name": "auth",
      "directory": "/Users/example/projects/auth-service",
      "command": "pnpm start:dev",
      "env": "DEV"
    }
  ]
}
```

## Notes

- Processes are started in their own process group so stopping a service also stops child processes spawned by `pnpm`.
- The app extends `PATH` with common macOS Node locations such as `/opt/homebrew/bin` and `/usr/local/bin`.
- If a service exits with a non-zero code, it is marked red until you refresh or run it again.
