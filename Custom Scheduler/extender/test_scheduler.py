import unittest

from server import Config, filter_nodes, node_score


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


if __name__ == "__main__":
    unittest.main()
