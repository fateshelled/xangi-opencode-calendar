from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from pathlib import Path


class OpenCodeCli:
    def __init__(self, workspace: Path, command: str | None = None):
        self.workspace = workspace.resolve()
        self.command = command or os.environ.get("OPENCODE_BIN", "opencode")

    def _run(self, args: list[str]) -> object:
        with tempfile.TemporaryFile(mode="w+b") as output, tempfile.TemporaryFile(
            mode="w+b"
        ) as error_output:
            try:
                result = subprocess.run(
                    [self.command, *args],
                    cwd=self.workspace,
                    stdout=output,
                    stderr=error_output,
                    text=False,
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise RuntimeError(f"OpenCode CLI failed: {error}") from error
            error_output.seek(0)
            error = error_output.read(16 * 1024).decode("utf-8", errors="replace")
            if result.returncode != 0:
                raise RuntimeError(f"OpenCode CLI failed: {error.strip() or result.returncode}")
            output.seek(0)
            raw = output.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise RuntimeError("OpenCode CLI output is too large")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise RuntimeError("OpenCode CLI returned invalid JSON") from error

    def sessions(self) -> dict:
        value = self._run(["session", "list", "--format", "json", "--max-count", "5000"])
        if not isinstance(value, list):
            raise RuntimeError("OpenCode session list must be an array")
        sessions = []
        for item in value:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("id"), str)
                or not item["id"].startswith("ses_")
                or not isinstance(item.get("title"), str)
                or not isinstance(item.get("directory"), str)
                or not self._time(item.get("created"))
                or not self._time(item.get("updated"))
            ):
                continue
            try:
                directory = Path(item["directory"]).resolve()
            except (OSError, ValueError):
                continue
            try:
                directory.relative_to(self.workspace)
            except ValueError:
                continue
            sessions.append(
                {
                    "id": item.get("id"),
                    "title": item.get("title", ""),
                    "directory": str(directory),
                    "project": directory.name,
                    "status": "done",
                    "start": item["created"],
                    "end": max(item["created"], item["updated"]),
                }
            )
        return {
            "sessions": sorted(sessions, key=lambda item: item["start"]),
            "truncated": len(value) >= 5000,
            "degraded": False,
            "source": "opencode-cli",
        }

    @staticmethod
    def _time(value: object) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    def details(self, session_id: str) -> dict:
        if not session_id.startswith("ses_") or "/" in session_id:
            raise RuntimeError("invalid session id")
        allowed = {item["id"] for item in self.sessions()["sessions"]}
        if session_id not in allowed:
            raise RuntimeError("session not found in workspace")
        value = self._run(["export", session_id, "--sanitize"])
        if not isinstance(value, dict) or not isinstance(value.get("info"), dict):
            raise RuntimeError("OpenCode export has an invalid shape")
        info = value["info"]
        if info.get("id") != session_id or not isinstance(value.get("messages"), list):
            raise RuntimeError("OpenCode export has an invalid session shape")
        return {"id": session_id, "export": value, "source": "opencode-cli"}
