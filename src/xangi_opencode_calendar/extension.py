from __future__ import annotations

import argparse
import json
import os
import signal
import select
import sys
import threading
from pathlib import Path

from .opencode_cli import OpenCodeCli
from .server import CalendarServer


def serve_managed(workspace: Path) -> None:
    token = os.environ.get("XANGI_EXTENSION_AUTH_TOKEN")
    if not token:
        raise RuntimeError("XANGI_EXTENSION_AUTH_TOKEN is required")

    resolved_workspace = workspace.expanduser().resolve()
    server = CalendarServer(
        ("127.0.0.1", 0),
        auth_token=token,
        workspace=resolved_workspace,
        source=OpenCodeCli(resolved_workspace),
    )
    stopped = threading.Event()

    def shutdown() -> None:
        if stopped.is_set():
            return
        stopped.set()
        server.shutdown()

    def watch_parent() -> None:
        while not stopped.is_set():
            ready, _, _ = select.select([sys.stdin], [], [], 0.2)
            if ready and not os.read(sys.stdin.fileno(), 1):
                shutdown()

    signal.signal(signal.SIGTERM, lambda *_: shutdown())
    signal.signal(signal.SIGINT, lambda *_: shutdown())
    watcher = threading.Thread(
        target=watch_parent, daemon=True, name="calendar-parent-watch"
    )
    watcher.start()

    thread = threading.Thread(target=server.serve_forever, daemon=True, name="calendar-http")
    thread.start()
    print(
        json.dumps(
            {
                "schemaVersion": 2,
                "event": "ready",
                "id": "xangi-opencode-calendar",
                "baseUrl": f"http://127.0.0.1:{server.server_port}",
                "workspace": str(resolved_workspace),
                "pid": os.getpid(),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    thread.join()
    watcher.join(timeout=1)
    server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="xangi-opencode-calendar-extension")
    parser.add_argument("action", choices=["serve", "update"])
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    args = parser.parse_args()
    if args.action == "serve":
        serve_managed(args.workspace)
        return
    print(json.dumps({"schemaVersion": 2, "id": "xangi-opencode-calendar", "unsupported": True}))


if __name__ == "__main__":
    main()
