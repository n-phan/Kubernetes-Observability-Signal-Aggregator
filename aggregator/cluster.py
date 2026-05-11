"""
Cluster status router.

Exposes GET /cluster/status — a small set of instant PromQL queries against
node-exporter metrics, summarised for the homepage status panel:
CPU %, memory, load, per-mount disk usage, and network throughput.

If node-exporter is not scraped (or Prometheus is unreachable), the endpoint
returns {"available": false, ...} rather than an error, so the UI can degrade
gracefully.
"""
from __future__ import annotations

import asyncio
import logging

import httpx
from fastapi import APIRouter, Query

from aggregator.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cluster", tags=["cluster"])

# Single-value queries — each must evaluate to one scalar (or empty if no data).
_SCALAR_QUERIES: dict[str, str] = {
    "cpu_pct":      '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[2m])) * 100)',
    "cpu_cores":    'count(count by (cpu) (node_cpu_seconds_total))',
    "mem_total":    'sum(node_memory_MemTotal_bytes)',
    "mem_avail":    'sum(node_memory_MemAvailable_bytes)',
    "load1":        'max(node_load1)',
    "net_recv_bps": 'sum(rate(node_network_receive_bytes_total{device!~"lo|veth.*|docker.*|br[-_].*|cni.*|flannel.*|tap.*"}[2m]))',
    "net_sent_bps": 'sum(rate(node_network_transmit_bytes_total{device!~"lo|veth.*|docker.*|br[-_].*|cni.*|flannel.*|tap.*"}[2m]))',
}

# Real filesystems only — drop pseudo / virtual mounts and WSL/Docker-Desktop
# internal bind mounts that just duplicate the same physical disk.
_FS_SELECTOR = (
    'fstype!~"tmpfs|overlay|squashfs|ramfs|devtmpfs|iso9660|autofs|nsfs|cgroup.*|'
    'sysfs|proc|debugfs|securityfs|pstore|bpf|tracefs|mqueue|hugetlbfs|fusectl|configfs",'
    'mountpoint!~"/(boot|host|parent-distro|usr/lib|snap|run|var/lib/docker)($|/.*)"'
)

_TIMEOUT = 5.0


async def _instant(client: httpx.AsyncClient, query: str) -> list[dict]:
    """Run a PromQL instant query. Returns the result list, or [] on any failure."""
    try:
        resp = await client.get(
            f"{settings.prometheus_url}/api/v1/query",
            params={"query": query},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") != "success":
            logger.warning("cluster query non-success: %s — %s", query, body.get("error"))
            return []
        return body.get("data", {}).get("result", []) or []
    except Exception as exc:  # noqa: BLE001 — best effort, never fail the endpoint
        logger.warning("cluster query failed: %s — %s", query, exc)
        return []


def _value(item: dict) -> float | None:
    try:
        return float(item["value"][1])
    except (KeyError, IndexError, ValueError, TypeError):
        return None


def _first(result: list[dict]) -> float | None:
    return _value(result[0]) if result else None


def _pct(used: float | None, total: float | None) -> float | None:
    if used is None or not total:
        return None
    return round(max(0.0, min(100.0, used / total * 100)), 2)


@router.get("/status")
async def cluster_status(
    target: str | None = Query(default=None, description="If given, also include pod-status info for this service (Kubernetes only)"),
    namespace: str | None = Query(default=None, description="Namespace of `target` (defaults to 'default')"),
) -> dict:
    """Summarised node-exporter metrics for the homepage status panel; plus
    pod-status info (node, phase, restarts, waiting reasons) for `target` when
    given and kube-state-metrics is available."""
    ns = (namespace or "default").strip()
    pod_info: list[dict] = []
    pod_phase: list[dict] = []
    pod_restarts: list[dict] = []
    pod_waiting: list[dict] = []

    async with httpx.AsyncClient() as client:
        scalar_results = await asyncio.gather(
            *[_instant(client, q) for q in _SCALAR_QUERIES.values()]
        )
        scalars = {k: _first(res) for k, res in zip(_SCALAR_QUERIES, scalar_results)}

        fs_size, fs_avail = await asyncio.gather(
            _instant(client, f"node_filesystem_size_bytes{{{_FS_SELECTOR}}}"),
            _instant(client, f"node_filesystem_avail_bytes{{{_FS_SELECTOR}}}"),
        )

        if target:
            sel = f'namespace="{ns}", pod=~"{target}-.+"'
            pod_info, pod_phase, pod_restarts, pod_waiting = await asyncio.gather(
                _instant(client, f"kube_pod_info{{{sel}}}"),
                _instant(client, f"kube_pod_status_phase{{{sel}}} == 1"),
                _instant(client, f"max by (pod) (kube_pod_container_status_restarts_total{{{sel}}})"),
                _instant(client, f"kube_pod_container_status_waiting_reason{{{sel}}} == 1"),
            )

    # node-exporter often reports the same physical device under many mountpoints
    # (bind mounts, WSL overlays, etc.). Collapse to one entry per `device`,
    # keeping the most "natural"-looking mountpoint (fewest path segments, then
    # shortest). Anonymous/empty device labels are kept keyed by mountpoint.
    def _mount_rank(mp: str) -> tuple[int, int]:
        return (mp.count("/"), len(mp))

    avail_by_mount: dict[str, float] = {}
    for item in fs_avail:
        mount = item.get("metric", {}).get("mountpoint")
        val = _value(item)
        if mount is not None and val is not None:
            avail_by_mount[mount] = val

    best_by_device: dict[str, dict] = {}
    for item in fs_size:
        metric = item.get("metric", {})
        mount = metric.get("mountpoint")
        size = _value(item)
        avail = avail_by_mount.get(mount) if mount is not None else None
        if mount is None or size is None or avail is None or size <= 0:
            continue
        device = metric.get("device") or f"mount:{mount}"
        used = max(0.0, size - avail)
        entry = {"mount": mount, "total": size, "used": used, "pct": _pct(used, size)}
        current = best_by_device.get(device)
        if current is None or _mount_rank(mount) < _mount_rank(current["mount"]):
            best_by_device[device] = entry

    disks = sorted(best_by_device.values(), key=lambda d: d["mount"])[:12]

    cpu_pct = scalars.get("cpu_pct")
    if cpu_pct is not None:
        cpu_pct = round(max(0.0, min(100.0, cpu_pct)), 2)
    cpu_cores = scalars.get("cpu_cores")
    used_cores = round(cpu_pct / 100 * cpu_cores, 2) if (cpu_pct is not None and cpu_cores) else None

    mem_total = scalars.get("mem_total")
    mem_avail = scalars.get("mem_avail")
    mem_used = (mem_total - mem_avail) if (mem_total is not None and mem_avail is not None) else None

    load1 = scalars.get("load1")
    load_pct = _pct(load1, cpu_cores) if (load1 is not None and cpu_cores) else None

    # ── Pod status for the target service (Kubernetes / kube-state-metrics) ──
    pods: list[dict] = []
    if target:
        def _pod(item: dict) -> str | None:
            return (item.get("metric") or {}).get("pod")

        node_by_pod = {_pod(i): (i["metric"].get("node")) for i in pod_info if _pod(i)}
        phase_by_pod = {_pod(i): (i["metric"].get("phase")) for i in pod_phase if _pod(i)}
        restarts_by_pod = {_pod(i): int(_value(i) or 0) for i in pod_restarts if _pod(i)}
        waiting_by_pod: dict[str, list[str]] = {}
        for i in pod_waiting:
            p, reason = _pod(i), (i.get("metric") or {}).get("reason")
            if p and reason:
                waiting_by_pod.setdefault(p, []).append(reason)
        for p in sorted(set(node_by_pod) | set(phase_by_pod) | set(restarts_by_pod) | set(waiting_by_pod)):
            pods.append({
                "name": p,
                "node": node_by_pod.get(p),
                "phase": phase_by_pod.get(p),
                "restarts": restarts_by_pod.get(p, 0),
                "waiting_reasons": waiting_by_pod.get(p, []),
            })

    available = cpu_pct is not None or mem_total is not None or bool(disks) or bool(pods)

    return {
        "available": available,
        "prometheus_url": settings.prometheus_url,
        "cpu": {"pct": cpu_pct, "cores": cpu_cores, "used_cores": used_cores},
        "memory": {"pct": _pct(mem_used, mem_total), "used": mem_used, "total": mem_total},
        "load": {"load1": load1, "cores": cpu_cores, "pct": load_pct},
        "disks": disks,
        "network": {
            "recv_bps": scalars.get("net_recv_bps"),
            "sent_bps": scalars.get("net_sent_bps"),
        },
        "target": target,
        "namespace": ns if target else None,
        "pods": pods,
    }
