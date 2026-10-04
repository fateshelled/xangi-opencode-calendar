from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class InvalidTimezoneError(ValueError):
    pass


class InvalidDateError(ValueError):
    pass


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

    def details(
        self,
        session_id: str,
        allowed: set[str] | None = None,
        metadata: dict[str, dict] | None = None,
    ) -> dict:
        if not session_id.startswith("ses_") or "/" in session_id:
            raise RuntimeError("invalid session id")
        if allowed is None:
            listed = self.sessions()["sessions"]
            allowed = {item["id"] for item in listed}
            metadata = {item["id"]: item for item in listed}
        if session_id not in allowed:
            raise RuntimeError("session not found in workspace")
        value = self._run(["export", session_id, "--sanitize"])
        if not isinstance(value, dict) or not isinstance(value.get("info"), dict):
            raise RuntimeError("OpenCode export has an invalid shape")
        info = value["info"]
        if info.get("id") != session_id or not isinstance(value.get("messages"), list):
            raise RuntimeError("OpenCode export has an invalid session shape")
        item = metadata.get(session_id) if metadata else None
        if item:
            value["info"] = {
                **info,
                "title": item.get("title", ""),
                "directory": item.get("directory", ""),
            }
        return {"id": session_id, "export": value, "source": "opencode-cli"}

    @staticmethod
    def _week_start(
        now: datetime | None, timezone_name: str, date_name: str | None
    ) -> tuple[ZoneInfo, datetime]:
        try:
            zone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise InvalidTimezoneError(f"invalid timezone: {timezone_name}") from error
        if date_name is not None:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_name):
                raise InvalidDateError(f"invalid date: {date_name}")
            try:
                current = datetime.combine(date.fromisoformat(date_name), datetime.min.time(), zone)
            except ValueError as error:
                raise InvalidDateError(f"invalid date: {date_name}") from error
        else:
            current = now or datetime.now(zone)
            current = current.replace(tzinfo=zone) if current.tzinfo is None else current.astimezone(zone)
        monday = (current - timedelta(days=current.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return zone, monday

    def weekly(
        self,
        now: datetime | None = None,
        timezone_name: str | None = None,
        date_name: str | None = None,
    ) -> dict:
        name = timezone_name or "UTC"
        _, monday = self._week_start(now, name, date_name)
        start = monday.timestamp() * 1000
        end = (monday + timedelta(days=7)).timestamp() * 1000
        listed = self.sessions()
        sessions = [
            item
            for item in listed["sessions"]
            if start <= item["start"] < end
        ]
        projects: dict[str, dict] = {}
        total_minutes = 0
        total_cost = 0.0
        total_tokens = {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}}
        total_sessions = len(sessions)
        for item in sessions:
            minutes = max(0, round((item["end"] - item["start"]) / 60_000))
            project = projects.setdefault(
                item["project"], {"sessions": 0, "minutes": 0}
            )
            project["sessions"] += 1
            project["minutes"] += minutes
            total_minutes += minutes
        allowed = {item["id"] for item in sessions}
        metadata = {item["id"]: item for item in sessions}

        def usage(item: dict) -> tuple[dict, float, dict]:
            details = self.details(item["id"], allowed, metadata)["export"]["info"]
            cost = details.get("cost", 0)
            if not isinstance(cost, (int, float)) or isinstance(cost, bool) or not math.isfinite(cost):
                raise RuntimeError("OpenCode export has an invalid cost")
            tokens = details.get("tokens") or {}
            if not isinstance(tokens, dict):
                raise RuntimeError("OpenCode export has invalid tokens")
            cache = tokens.get("cache") or {}
            if not isinstance(cache, dict):
                raise RuntimeError("OpenCode export has invalid token cache")
            values = {key: tokens.get(key, 0) for key in ("input", "output", "reasoning")}
            values["cache"] = {key: cache.get(key, 0) for key in ("read", "write")}
            for value in (values["input"], values["output"], values["reasoning"], *values["cache"].values()):
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                    raise RuntimeError("OpenCode export has invalid token values")
            return values, cost, item

        failed = 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(usage, item) for item in sessions]
            usages = []
            for future in as_completed(futures):
                try:
                    usages.append(future.result())
                except RuntimeError:
                    failed += 1
        for tokens, cost, item in usages:
            total_cost += cost
            item["cost"] = cost
            item["tokens"] = tokens
            for key in ("input", "output", "reasoning"):
                total_tokens[key] += tokens.get(key) or 0
            cache = tokens.get("cache") or {}
            total_tokens["cache"]["read"] += cache.get("read") or 0
            total_tokens["cache"]["write"] += cache.get("write") or 0
        return {
            "from": monday.isoformat(),
            "to": (monday + timedelta(days=7)).isoformat(),
            "sessions": total_sessions,
            "calendarSessions": sessions,
            "minutes": total_minutes,
            "cost": total_cost,
            "tokens": total_tokens,
            "projects": projects,
            "truncated": listed["truncated"],
            "degraded": failed > 0,
            "failedSessions": failed,
            "timezone": name,
            "weekStarts": "Monday",
        }

    def calendar(
        self, timezone_name: str | None = None, date_name: str | None = None
    ) -> dict:
        name = timezone_name or "UTC"
        _, monday = self._week_start(None, name, date_name)
        start = monday.timestamp() * 1000
        end = (monday + timedelta(days=7)).timestamp() * 1000
        listed = self.sessions()
        sessions = [
            item for item in listed["sessions"] if start <= item["start"] < end
        ]
        return {
            "from": monday.isoformat(),
            "to": (monday + timedelta(days=7)).isoformat(),
            "timezone": name,
            "sessions": sessions,
            "truncated": listed["truncated"],
            "degraded": False,
        }
