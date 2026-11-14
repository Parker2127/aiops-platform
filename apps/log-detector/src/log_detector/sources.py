"""Log source for MODE=k8s: follow the logs of every pod matching a label selector."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Iterator
from typing import Any

log = logging.getLogger(__name__)


def split_lines(chunks: Iterable[bytes]) -> Iterator[str]:
    """Re-assemble a byte stream into complete lines."""
    buf = b""
    for chunk in chunks:
        buf += chunk
        *lines, buf = buf.split(b"\n")
        for line in lines:
            yield line.decode("utf-8", errors="replace")
    if buf:
        yield buf.decode("utf-8", errors="replace")


class PodLogTailer:
    def __init__(
        self,
        core_v1: Any,
        namespace: str,
        label_selector: str,
        sink: Callable[[str, str], None],
        rediscover_seconds: float = 15.0,
    ) -> None:
        self._core = core_v1
        self._ns = namespace
        self._selector = label_selector
        self._sink = sink
        self._rediscover = rediscover_seconds
        self._active: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                pods = self._core.list_namespaced_pod(self._ns, label_selector=self._selector).items
                for pod in pods:
                    name = pod.metadata.name
                    if pod.status.phase != "Running":
                        continue
                    with self._lock:
                        if name in self._active:
                            continue
                        self._active.add(name)
                    threading.Thread(
                        target=self._follow, args=(name,), daemon=True, name=f"tail-{name}"
                    ).start()
            except Exception:
                log.exception("pod discovery failed")
            self._stop.wait(self._rediscover)

    def _follow(self, pod: str) -> None:
        log.info("tailing pod", extra={"pod": pod, "namespace": self._ns})
        try:
            resp = self._core.read_namespaced_pod_log(
                pod, self._ns, follow=True, since_seconds=10, _preload_content=False
            )
            for line in split_lines(resp.stream(4096)):
                if self._stop.is_set():
                    break
                self._sink(pod, line)
        except Exception:
            log.exception("log stream failed", extra={"pod": pod})
        finally:
            with self._lock:
                self._active.discard(pod)
            log.info("stopped tailing pod", extra={"pod": pod})
