import unittest

from src.health_check import load_health_data


class HealthDataTests(unittest.TestCase):
    def test_fixture_matches_contract(self):
        data = load_health_data()
        self.assertEqual(data["project"]["id"], "demo-project")
        self.assertGreaterEqual(data["project"]["health_score"], 0)
        self.assertLessEqual(data["project"]["health_score"], 100)
        self.assertEqual(len(data["checks"]), 3)

    def test_fixture_has_only_supported_statuses(self):
        data = load_health_data()
        statuses = {check["status"] for check in data["checks"]}
        self.assertTrue(statuses.issubset({"passing", "warning", "failing"}))


if __name__ == "__main__":
    unittest.main()
