"""App-owned mutable runtime state for the Gateway Admin control plane."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any


DEFAULT_LOGIN_MAX_ATTEMPTS = 5
DEFAULT_LOGIN_LOCKOUT_SECONDS = 300.0
DEFAULT_LOGIN_ENTRY_TTL_SECONDS = 600.0
DEFAULT_LOGIN_FAILURE_CAPACITY = 4_096

DEFAULT_TEST_TASK_TTL_SECONDS = 300.0
DEFAULT_TEST_TASK_TIMEOUT_SECONDS = 120.0
DEFAULT_TEST_TASK_MAX_ACTIVE = 4
DEFAULT_TEST_TASK_MAX_COUNT = 128
DEFAULT_TEST_TASK_MAX_PAYLOAD_BYTES = 4 * 1024 * 1024
DEFAULT_TEST_TASK_MAX_COMPLETED_BYTES = 32 * 1024 * 1024


class AdminLoginLimiter:
    """Concurrent, per-app Admin login failure limiter."""

    def __init__(
        self,
        *,
        max_attempts: int = DEFAULT_LOGIN_MAX_ATTEMPTS,
        lockout_seconds: float = DEFAULT_LOGIN_LOCKOUT_SECONDS,
        entry_ttl_seconds: float = DEFAULT_LOGIN_ENTRY_TTL_SECONDS,
        capacity: int = DEFAULT_LOGIN_FAILURE_CAPACITY,
    ) -> None:
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self.entry_ttl_seconds = entry_ttl_seconds
        self.capacity = capacity
        self._failures: dict[str, dict[str, float | int]] = {}
        self._lock = threading.RLock()

    def _sweep_locked(self, now: float) -> None:
        expired = [
            ip
            for ip, record in self._failures.items()
            if float(record.get("locked_until", 0.0)) <= now
            and now - float(record.get("last_seen", 0.0)) >= self.entry_ttl_seconds
        ]
        for ip in expired:
            self._failures.pop(ip, None)

        overflow = len(self._failures) - self.capacity
        if overflow > 0:
            oldest = sorted(
                self._failures,
                key=lambda ip: float(self._failures[ip].get("last_seen", 0.0)),
            )[:overflow]
            for ip in oldest:
                self._failures.pop(ip, None)

    def check(self, ip: str) -> tuple[bool, float]:
        """Return whether *ip* is locked and its remaining lockout seconds."""
        now = time.monotonic()
        with self._lock:
            self._sweep_locked(now)
            record = self._failures.get(ip)
            if record is None:
                return False, 0.0
            locked_until = float(record.get("locked_until", 0.0))
            if locked_until > now:
                return True, locked_until - now
            return False, 0.0

    def record_failure(self, ip: str) -> None:
        """Record one failed login and start lockout at the configured threshold."""
        now = time.monotonic()
        with self._lock:
            self._sweep_locked(now)
            if ip not in self._failures and len(self._failures) >= self.capacity:
                oldest_ip = min(
                    self._failures,
                    key=lambda candidate: float(
                        self._failures[candidate].get("last_seen", 0.0)
                    ),
                )
                self._failures.pop(oldest_ip, None)
            record = self._failures.setdefault(
                ip,
                {"count": 0, "locked_until": 0.0, "last_seen": now},
            )
            locked_until = float(record.get("locked_until", 0.0))
            if locked_until and now >= locked_until:
                record["count"] = 0
                record["locked_until"] = 0.0
            record["last_seen"] = now
            count = int(record.get("count", 0)) + 1
            record["count"] = count
            if count >= self.max_attempts:
                record["locked_until"] = now + self.lockout_seconds

    def clear(self, ip: str) -> None:
        """Clear one peer's failed-login state after successful authentication."""
        with self._lock:
            self._failures.pop(ip, None)

    @property
    def entry_count(self) -> int:
        """Return the current number of retained peer records."""
        with self._lock:
            self._sweep_locked(time.monotonic())
            return len(self._failures)


@dataclass
class _AdminTestTask:
    task_id: str
    started: float
    status: str = "pending"
    task_obj: asyncio.Task[Any] | Any | None = None
    finished: float | None = None
    status_code: int | None = None
    body_bytes: bytes | None = None
    error: str | None = None
    retained_bytes: int = 0

    @property
    def active(self) -> bool:
        return self.status == "pending"


class AdminTestTaskStore:
    """Per-app model-test registry with atomic count and byte budgets."""

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TEST_TASK_TTL_SECONDS,
        max_active: int = DEFAULT_TEST_TASK_MAX_ACTIVE,
        max_count: int = DEFAULT_TEST_TASK_MAX_COUNT,
        max_payload_bytes: int = DEFAULT_TEST_TASK_MAX_PAYLOAD_BYTES,
        max_completed_bytes: int = DEFAULT_TEST_TASK_MAX_COMPLETED_BYTES,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_active = max_active
        self.max_count = max_count
        self.max_payload_bytes = max_payload_bytes
        self.max_completed_bytes = max_completed_bytes
        self._tasks: dict[str, _AdminTestTask] = {}
        self._completed_bytes = 0
        self._closed = False
        self._lock = threading.RLock()

    @staticmethod
    def _cancel_task(task_obj: Any | None) -> None:
        if task_obj is None or task_obj.done():
            return
        get_loop = getattr(task_obj, "get_loop", None)
        if callable(get_loop):
            loop = get_loop()
            if loop.is_running():
                loop.call_soon_threadsafe(task_obj.cancel)
                return
        task_obj.cancel()

    @staticmethod
    def _metadata_bytes(record: _AdminTestTask) -> int:
        metadata = {
            "task_id": record.task_id,
            "status": record.status,
            "started": record.started,
            "finished": record.finished,
            "status_code": record.status_code,
            "error": record.error,
        }
        return len(
            json.dumps(
                metadata,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )

    def _remove_locked(self, task_id: str) -> _AdminTestTask | None:
        record = self._tasks.pop(task_id, None)
        if record is not None:
            self._completed_bytes -= record.retained_bytes
            if self._completed_bytes < 0:
                raise RuntimeError("Admin test-task byte accounting became negative")
        return record

    def _oldest_completed_locked(
        self,
        *,
        exclude: str | None = None,
    ) -> _AdminTestTask | None:
        candidates = [
            record
            for record in self._tasks.values()
            if not record.active and record.task_id != exclude
        ]
        return min(
            candidates,
            key=lambda record: (
                record.finished if record.finished is not None else record.started,
                record.started,
            ),
            default=None,
        )

    def _sweep_locked(self, now: float) -> list[Any]:
        cancelled: list[Any] = []
        expired = [
            task_id
            for task_id, record in self._tasks.items()
            if now - record.started > self.ttl_seconds
        ]
        for task_id in expired:
            record = self._remove_locked(task_id)
            if record is not None and record.active and record.task_obj is not None:
                cancelled.append(record.task_obj)
        return cancelled

    def cleanup_expired(self) -> None:
        """Remove expired records and cancel only this app's expired active work."""
        with self._lock:
            cancelled = self._sweep_locked(time.monotonic())
        for task_obj in cancelled:
            self._cancel_task(task_obj)

    def reserve(self) -> tuple[str | None, str | None]:
        """Reserve one active task slot, evicting only oldest completed records."""
        with self._lock:
            if self._closed:
                return None, "Admin model-test state is closed"
            cancelled = self._sweep_locked(time.monotonic())
            if sum(record.active for record in self._tasks.values()) >= self.max_active:
                result = (None, "Too many model tests are already running")
            else:
                while len(self._tasks) >= self.max_count:
                    candidate = self._oldest_completed_locked()
                    if candidate is None:
                        result = (None, "Model test task capacity reached")
                        break
                    self._remove_locked(candidate.task_id)
                else:
                    task_id = uuid.uuid4().hex[:12]
                    self._tasks[task_id] = _AdminTestTask(
                        task_id=task_id,
                        started=time.monotonic(),
                    )
                    result = (task_id, None)
        for task_obj in cancelled:
            self._cancel_task(task_obj)
        return result

    def attach_task(self, task_id: str, task_obj: asyncio.Task[Any]) -> bool:
        """Attach the executing asyncio task to a previously reserved record."""
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None or not record.active or self._closed:
                accepted = False
            else:
                record.task_obj = task_obj
                accepted = True
        if not accepted:
            self._cancel_task(task_obj)
        return accepted

    def _set_small_error_locked(
        self,
        record: _AdminTestTask,
        *,
        message: str,
        status_code: int,
        finished: float,
    ) -> None:
        record.status = "error"
        record.finished = finished
        record.status_code = status_code
        record.body_bytes = None
        record.error = message
        record.task_obj = None
        record.retained_bytes = self._metadata_bytes(record)

    def finish(
        self,
        task_id: str,
        *,
        status: str,
        status_code: int | None = None,
        body_bytes: bytes | None = None,
        error: str | None = None,
        finished: float | None = None,
    ) -> bool:
        """Atomically retain one terminal result within task and app byte budgets."""
        terminal_time = time.monotonic() if finished is None else finished
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None or self._closed:
                return False

            self._completed_bytes -= record.retained_bytes
            record.status = status
            record.finished = terminal_time
            record.status_code = status_code
            record.body_bytes = bytes(body_bytes) if body_bytes is not None else None
            record.error = error
            record.task_obj = None
            record.retained_bytes = self._metadata_bytes(record) + len(
                record.body_bytes or b""
            )

            if record.retained_bytes > self.max_payload_bytes:
                self._set_small_error_locked(
                    record,
                    message=(
                        "Admin model-test result exceeds retained-task capacity "
                        f"({self.max_payload_bytes} bytes)"
                    ),
                    status_code=507,
                    finished=terminal_time,
                )

            while (
                self._completed_bytes + record.retained_bytes > self.max_completed_bytes
            ):
                candidate = self._oldest_completed_locked(exclude=task_id)
                if candidate is None:
                    self._set_small_error_locked(
                        record,
                        message="Admin model-test result capacity is unavailable",
                        status_code=507,
                        finished=terminal_time,
                    )
                    break
                self._remove_locked(candidate.task_id)

            if self._completed_bytes + record.retained_bytes > self.max_completed_bytes:
                # The configured aggregate is too small even for the compact
                # capacity diagnostic. Drop the record rather than violating it.
                self._tasks.pop(task_id, None)
                return False

            self._completed_bytes += record.retained_bytes
            return True

    def get_public(self, task_id: str) -> dict[str, Any] | None:
        """Build one public task result, decoding retained body bytes temporarily."""
        self.cleanup_expired()
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return None
            status = record.status
            started = record.started
            finished = record.finished
            status_code = record.status_code
            error = record.error
            body_bytes = record.body_bytes

        result: dict[str, Any] = {"status": status, "started": started}
        if finished is not None:
            result["finished"] = finished
        if status_code is not None:
            result["status_code"] = status_code
        if error is not None:
            result["error"] = error
        if body_bytes is not None:
            try:
                result["body"] = json.loads(body_bytes)
            except UnicodeDecodeError, json.JSONDecodeError:
                result["body"] = body_bytes.decode("utf-8", errors="replace")
        return result

    def cancel(self, task_id: str) -> bool:
        """Cancel one task owned by this store without exposing other stores."""
        self.cleanup_expired()
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return False
            task_obj = record.task_obj if record.active else None
        if task_obj is not None:
            self._cancel_task(task_obj)
            self.finish(task_id, status="cancelled")
        return True

    def shutdown(self) -> tuple[asyncio.Task[Any], ...]:
        """Close this app's store, clear results, and cancel its active tasks."""
        with self._lock:
            if self._closed:
                return ()
            self._closed = True
            active = tuple(
                record.task_obj
                for record in self._tasks.values()
                if record.active and isinstance(record.task_obj, asyncio.Task)
            )
            other_active = tuple(
                record.task_obj
                for record in self._tasks.values()
                if record.active
                and record.task_obj is not None
                and not isinstance(record.task_obj, asyncio.Task)
            )
            self._tasks.clear()
            self._completed_bytes = 0
        for task_obj in (*active, *other_active):
            self._cancel_task(task_obj)
        return active

    @property
    def task_count(self) -> int:
        with self._lock:
            return len(self._tasks)

    @property
    def active_count(self) -> int:
        with self._lock:
            return sum(record.active for record in self._tasks.values())

    @property
    def completed_bytes(self) -> int:
        with self._lock:
            return self._completed_bytes


class AdminRuntimeState:
    """Own all mutable Admin request state for exactly one Gateway app."""

    def __init__(
        self,
        *,
        login_limiter: AdminLoginLimiter | None = None,
        test_tasks: AdminTestTaskStore | None = None,
    ) -> None:
        self.login_limiter = login_limiter or AdminLoginLimiter()
        self.test_tasks = test_tasks or AdminTestTaskStore()

    async def aclose(self) -> None:
        """Cancel and await this app's active Admin model tests, then clear state."""
        active = self.test_tasks.shutdown()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
