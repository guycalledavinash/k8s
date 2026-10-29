"""HTTP extender for simulated regional scheduling signals.

The extender receives only nodes that passed kube-scheduler's normal filters.
It adds a minimum simulated-capacity filter and a configurable 0..10 score.
"""
from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

LOG = logging.getLogger("regional_scheduler_extender")


def _bounded_signal(labels: dict[str, str], key: str) -> int | None:
    raw = labels.get(key)
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= 100 else None


@dataclass(frozen=True)
class Config:
    weight_locality: float = 3.0
    weight_latency: float = 2.0
    weight_capacity: float = 2.0
    max_latency_ms: int = 200
    min_available_capacity: int = 10
    region_annotation: str = "placement.demo/region"
    region_label: str = "placement.demo/region"
    demand_label: str = "placement.demo/demand"
    latency_label: str = "placement.demo/latency-ms"
    capacity_label: str = "placement.demo/available-capacity"

    def __post_init__(self) -> None:
        weights = (self.weight_locality, self.weight_latency, self.weight_capacity)
        if any(not math.isfinite(weight) or weight < 0 for weight in weights):
            raise ValueError("score weights must be finite and non-negative")
        if sum(weights) <= 0:
            raise ValueError("at least one score weight must be positive")
        if self.max_latency_ms <= 0:
            raise ValueError("MAX_LATENCY_MS must be positive")
        if not 0 <= self.min_available_capacity <= 100:
            raise ValueError("MIN_AVAILABLE_CAPACITY must be between 0 and 100")

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            weight_locality=float(os.getenv("WEIGHT_LOCALITY", "3")),
            weight_latency=float(os.getenv("WEIGHT_LATENCY", "2")),
            weight_capacity=float(os.getenv("WEIGHT_CAPACITY", "2")),
            max_latency_ms=int(os.getenv("MAX_LATENCY_MS", "200")),
            min_available_capacity=int(os.getenv("MIN_AVAILABLE_CAPACITY", "10")),
            region_annotation=os.getenv("REGION_ANNOTATION", "placement.demo/region"),
            region_label=os.getenv("REGION_LABEL", "placement.demo/region"),
            demand_label=os.getenv("DEMAND_LABEL", "placement.demo/demand"),
            latency_label=os.getenv("LATENCY_LABEL", "placement.demo/latency-ms"),
            capacity_label=os.getenv("CAPACITY_LABEL", "placement.demo/available-capacity"),
        )


def node_score(pod: dict[str, Any], node: dict[str, Any], config: Config) -> int:
    """Return Kubernetes extender priority score (0..10) for a feasible node."""
    metadata = node.get("metadata") or {}
    labels = metadata.get("labels") or {}
    pod_metadata = pod.get("metadata") or {}
    annotations = pod_metadata.get("annotations") or {}

    desired_region = annotations.get(config.region_annotation)
    demand = _bounded_signal(labels, config.demand_label)
    locality_score = (
        demand
        if desired_region and labels.get(config.region_label) == desired_region and demand is not None
        else 0
    )

    latency_raw = labels.get(config.latency_label)
    try:
        latency = int(latency_raw)
        if latency < 0:
            raise ValueError("latency must not be negative")
    except (TypeError, ValueError):
        latency = config.max_latency_ms
    latency_score = max(0, 100 - min(max(latency, 0), config.max_latency_ms) * 100 // config.max_latency_ms)

    capacity = _bounded_signal(labels, config.capacity_label)
    capacity_score = capacity if capacity is not None else 0

    weighted_total = (
        config.weight_locality * locality_score
        + config.weight_latency * latency_score
        + config.weight_capacity * capacity_score
    )
    score = round(weighted_total / (
        (config.weight_locality + config.weight_latency + config.weight_capacity) * 10
    ))
    return max(0, min(10, score))


def filter_nodes(
    nodes: list[dict[str, Any]], config: Config
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Drop candidates without a valid, sufficient simulated capacity signal."""
    feasible = []
    failed: dict[str, str] = {}
    for node in nodes:
        metadata = node.get("metadata") or {}
        name = metadata.get("name", "")
        capacity = _bounded_signal(metadata.get("labels") or {}, config.capacity_label)
        if not name:
            raise ValueError("candidate node is missing metadata.name")
        if capacity is None:
            failed[name] = "missing or invalid simulated capacity label"
        elif capacity < config.min_available_capacity:
            failed[name] = "simulated available capacity is below configured minimum"
        else:
            feasible.append(node)
    return feasible, failed


def _pod_identity(pod: dict[str, Any]) -> str:
    metadata = pod.get("metadata") or {}
    return f"{metadata.get('namespace', 'default')}/{metadata.get('name', 'unknown')}"


class ExtenderHandler(BaseHTTPRequestHandler):
    config: Config

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.info("http " + fmt, *args)

    def _send_json(self, status: int, value: Any) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path not in {"/filter", "/prioritize"}:
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_048_576:
                raise ValueError("request body size is invalid")
            args = json.loads(self.rfile.read(length))
            if not isinstance(args, dict) or not isinstance(args.get("pod"), dict):
                raise ValueError("extender request must include a pod object")
            nodes_obj = args.get("nodes")
            nodes = nodes_obj.get("items") if isinstance(nodes_obj, dict) else None
            if not isinstance(nodes, list):
                raise ValueError("extender request must include a node list")
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": str(exc)})
            return

        if self.path == "/filter":
            try:
                selected, failed = filter_nodes(nodes, self.config)
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(200, {
                "nodes": {**nodes_obj, "items": selected},
                "failedNodes": failed,
            })
            return

        pod = args["pod"]
        priorities = [
            {"host": (node.get("metadata") or {}).get("name", ""),
             "score": node_score(pod, node, self.config)}
            for node in nodes
        ]
        priorities = [entry for entry in priorities if entry["host"]]
        priorities.sort(key=lambda entry: (-entry["score"], entry["host"]))
        LOG.info("scores pod=%s ranking=%s", _pod_identity(pod), priorities)
        self._send_json(200, priorities)


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(message)s")
    config = Config.from_env()
    ExtenderHandler.config = config
    port = int(os.getenv("PORT", "8080"))
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be in 1..65535")
    LOG.info("starting extender on port=%d config=%s", port, config)
    ThreadingHTTPServer(("0.0.0.0", port), ExtenderHandler).serve_forever()


if __name__ == "__main__":
    main()
