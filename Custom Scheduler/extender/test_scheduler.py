import json
import re
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from server import Config, ExtenderHandler, Metrics, filter_nodes, node_score


def node(name, region, demand, latency, capacity):
    return {
        "metadata": {
            "name": name,
            "labels": {
                "placement.demo/region": region,
                "placement.demo/demand": str(demand),
                "placement.demo/latency-ms": str(latency),
                "placement.demo/available-capacity": str(capacity),
            },
        }
    }


class SchedulerScoringTests(unittest.TestCase):
    def setUp(self):
        self.config = Config(
            weight_locality=3,
            weight_latency=2,
            weight_capacity=2,
            max_latency_ms=200,
            min_available_capacity=10,
        )
        self.pod = {
            "metadata": {
                "namespace": "e-commerce",
                "name": "users-deployment-abc",
                "annotations": {"placement.demo/region": "us-east"},
            }
        }

    def test_demand_change_changes_node_ranking(self):
        east = node("east", "us-east", 30, 150, 80)
        west = node("west", "us-west", 60, 20, 80)

        baseline = {n["metadata"]["name"]: node_score(self.pod, n, self.config) for n in (east, west)}
        self.assertGreater(baseline["west"], baseline["east"])

        east["metadata"]["labels"]["placement.demo/demand"] = "80"
        during_spike = {
            n["metadata"]["name"]: node_score(self.pod, n, self.config) for n in (east, west)
        }
        self.assertGreater(during_spike["east"], during_spike["west"])

    def test_latency_change_changes_ranking(self):
        east = node("east", "us-east", 80, 150, 80)
        west = node("west", "us-west", 60, 20, 80)
        before = node_score(self.pod, east, self.config)
        east["metadata"]["labels"]["placement.demo/latency-ms"] = "10"
        after = node_score(self.pod, east, self.config)
        self.assertGreater(after, before)

    def test_capacity_threshold_filters_even_high_scoring_node(self):
        healthy = node("healthy", "us-east", 100, 1, 80)
        unavailable = node("unavailable", "us-east", 100, 1, 5)

        selected, failed = filter_nodes([healthy, unavailable], self.config)

        self.assertEqual([n["metadata"]["name"] for n in selected], ["healthy"])
        self.assertIn("unavailable", failed)

    def test_invalid_capacity_is_filtered_fail_closed(self):
        missing = {"metadata": {"name": "missing", "labels": {}}}
        selected, failed = filter_nodes([missing], self.config)
        self.assertEqual(selected, [])
        self.assertIn("missing", failed)

    def test_zero_weights_are_invalid(self):
        with self.assertRaises(ValueError):
            Config(weight_locality=0, weight_latency=0, weight_capacity=0)


class ExtenderMetricsHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class TestHandler(ExtenderHandler):
            pass

        TestHandler.config = Config()
        TestHandler.metrics = Metrics()
        cls.handler = TestHandler
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.handler.metrics = Metrics()

    def get(self, path):
        with urllib.request.urlopen(self.base_url + path, timeout=3) as response:
            return response.status, response.headers, response.read().decode()

    def post(self, path, value):
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(value).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read().decode()

    @staticmethod
    def candidate(name, capacity):
        return {"metadata": {"name": name, "labels": {
            "placement.demo/available-capacity": str(capacity),
            "placement.demo/region": "us-east",
            "placement.demo/demand": "50",
            "placement.demo/latency-ms": "20",
        }}}

    def test_metrics_endpoint_has_prometheus_content_type_and_declarations(self):
        self.get("/healthz")
        status, headers, body = self.get("/metrics")
        self.assertEqual(status, 200)
        self.assertIn("text/plain", headers.get("Content-Type", ""))
        for declaration in (
            "# HELP regional_scheduler_extender_http_requests_total",
            "# TYPE regional_scheduler_extender_http_requests_total counter",
            "# TYPE regional_scheduler_extender_request_duration_seconds histogram",
            "# TYPE regional_scheduler_extender_nodes_evaluated_total counter",
            "# TYPE regional_scheduler_extender_nodes_rejected_total counter",
        ):
            self.assertIn(declaration, body)
        sample = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*(?:\{[^{}]+\})?\s+-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$")
        for line in body.splitlines():
            if not line.startswith("#"):
                self.assertRegex(line, sample)

    def test_counts_health_success_filter_rejections_and_invalid_requests(self):
        self.get("/healthz")
        request = {"pod": {"metadata": {}}, "nodes": {"items": [
            self.candidate("healthy", 80), self.candidate("low-capacity", 1),
        ]}}
        status, _, _ = self.post("/filter", request)
        self.assertEqual(status, 200)
        invalid_status, _, _ = self.post("/prioritize", {})
        self.assertEqual(invalid_status, 400)
        body = self.get("/metrics")[2]
        self.assertIn('http_requests_total{endpoint="healthz",status="200"} 1', body)
        self.assertIn('http_requests_total{endpoint="filter",status="200"} 1', body)
        self.assertIn('http_requests_total{endpoint="prioritize",status="400"} 1', body)
        self.assertIn('nodes_evaluated_total{operation="filter"} 2', body)
        self.assertIn("regional_scheduler_extender_nodes_rejected_total 1", body)
        self.assertIn('request_duration_seconds_count{endpoint="filter"} 1', body)

    def test_prioritize_counts_evaluated_nodes(self):
        request = {"pod": {"metadata": {}}, "nodes": {"items": [
            self.candidate("a", 80), self.candidate("b", 70),
        ]}}
        self.assertEqual(self.post("/prioritize", request)[0], 200)
        body = self.get("/metrics")[2]
        self.assertIn('nodes_evaluated_total{operation="prioritize"} 2', body)
        self.assertIn('http_requests_total{endpoint="prioritize",status="200"} 1', body)

    def test_scrapes_are_not_counted_and_concurrent_requests_are_thread_safe(self):
        before = self.get("/metrics")[2]
        thread_count = 5
        failures = []

        def request_health():
            try:
                self.get("/healthz")
            except Exception as exc:
                failures.append(exc)

        threads = [threading.Thread(target=request_health) for _ in range(thread_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
        self.assertEqual(failures, [])
        after = self.get("/metrics")[2]
        self.assertIn('http_requests_total{endpoint="healthz",status="200"} 5', after)
        self.assertNotIn('endpoint="metrics"', after)
        # Rendering itself is read-only; repeated scrapes leave counters unchanged.
        self.assertEqual(after, self.get("/metrics")[2])



if __name__ == "__main__":
    unittest.main()
