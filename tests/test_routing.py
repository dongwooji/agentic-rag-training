from __future__ import annotations

import hashlib
from pathlib import Path
import unittest

from src.evaluation.routing_metrics import evaluate_routing
from src.routing import DeterministicRouter, RouterInput, RoutingStatus, TaskType


PROJECT_ROOT = Path(__file__).resolve().parents[1]
# Runtime router configuration hash, pinned when router_baseline_v1 was frozen.
EXPECTED_CONFIG_SHA256 = (
    "034f72ea19ca6c364ac96e66402626bfdcc1345be677245202795bbe115acb77"
)


class DeterministicRouterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.router = DeterministicRouter()

    def test_preregistered_configuration_hash_is_fixed(self) -> None:
        config = PROJECT_ROOT / "config/router_baseline_v1.json"
        self.assertEqual(hashlib.sha256(config.read_bytes()).hexdigest(), EXPECTED_CONFIG_SHA256)

    def test_literature_only_routing(self) -> None:
        plan = self.router.route(
            RouterInput(
                "저항운동 연구에서 training frequency가 최대 근력 향상에 미치는 효과는?"
            )
        )
        self.assertEqual(plan.task_type, TaskType.LITERATURE_ONLY)
        self.assertEqual(plan.selected_tools, ("search_literature",))
        self.assertEqual(plan.rule_match.rule_id, "R-LITERATURE-ONLY")

    def test_log_only_routing_prevents_unnecessary_tools(self) -> None:
        plan = self.router.route(
            RouterInput("정제된 운동 로그의 첫 timestamp와 마지막 timestamp를 조회해줘")
        )
        self.assertEqual(plan.task_type, TaskType.LOG_LOOKUP)
        self.assertEqual(plan.selected_tools, ("query_training_log",))
        self.assertNotIn("compute_metrics", plan.selected_tools)
        self.assertNotIn("search_literature", plan.selected_tools)

    def test_log_metric_routing_and_execution_order(self) -> None:
        plan = self.router.route(
            RouterInput("Bench Press (Barbell)의 주간 volume과 frequency를 계산해줘")
        )
        self.assertEqual(plan.task_type, TaskType.LOG_METRIC)
        self.assertEqual(
            plan.execution_order, ("query_training_log", "compute_metrics")
        )

    def test_hybrid_routing(self) -> None:
        plan = self.router.route(
            RouterInput(
                "Bench Press (Barbell)의 e1RM 변화와 periodization 문헌을 함께 설명해줘"
            )
        )
        self.assertEqual(plan.task_type, TaskType.HYBRID)
        self.assertEqual(
            plan.selected_tools,
            ("query_training_log", "compute_metrics", "search_literature"),
        )

    def test_hybrid_lookup_without_metric(self) -> None:
        plan = self.router.route(
            RouterInput(
                "2017-02-04 Squat (Barbell) 기록은 progressive overload의 증거인가?"
            )
        )
        self.assertEqual(plan.task_type, TaskType.HYBRID)
        self.assertEqual(
            plan.selected_tools, ("query_training_log", "search_literature")
        )

    def test_unsupported_missing_data_uses_no_tool(self) -> None:
        plan = self.router.route(
            RouterInput("최근 Squat 성능 저하는 수면 부족 때문인가?")
        )
        self.assertEqual(plan.status, RoutingStatus.UNSUPPORTED)
        self.assertEqual(plan.task_type, TaskType.UNSUPPORTED)
        self.assertEqual(plan.selected_tools, ())
        self.assertIn("수면 부족", plan.unsupported_reason)

    def test_literature_request_prevents_false_unsupported_override(self) -> None:
        plan = self.router.route(
            RouterInput(
                "Bench Press (Barbell) 기록에는 RPE가 없는데 autoregulation 문헌은 무엇을 말하는가?"
            )
        )
        self.assertEqual(plan.task_type, TaskType.HYBRID)
        self.assertIn("search_literature", plan.selected_tools)

    def test_ambiguous_input_selects_no_tool(self) -> None:
        plan = self.router.route(RouterInput("이 상태가 괜찮은가?"))
        self.assertEqual(plan.status, RoutingStatus.AMBIGUOUS)
        self.assertEqual(plan.selected_tools, ())
        self.assertEqual(plan.confidence, 0.0)

    def test_malformed_inputs_are_structured(self) -> None:
        blank = self.router.route(RouterInput("  "))
        non_text = self.router.route(RouterInput(None))  # type: ignore[arg-type]
        self.assertEqual(blank.status, RoutingStatus.INVALID)
        self.assertEqual(non_text.status, RoutingStatus.INVALID)
        self.assertEqual(blank.error.code, "invalid_input")
        self.assertEqual(non_text.selected_tools, ())

    def test_serialized_contract_contains_required_fields(self) -> None:
        value = self.router.route(
            RouterInput("운동 기록의 session 목록을 조회해줘")
        ).to_dict()
        for field in (
            "route",
            "task_type",
            "selected_tools",
            "execution_order",
            "routing_reason",
            "confidence",
            "rule_match",
            "unsupported_reason",
        ):
            self.assertIn(field, value)


class RoutingEvaluationTest(unittest.TestCase):
    def test_metrics_distinguish_exact_match_unnecessary_and_missing(self) -> None:
        cases = [
            {
                "id": "LIT-X",
                "category": "literature_only",
                "question": "literature",
                "required_tools": ["search_literature"],
            },
            {
                "id": "UNA-X",
                "category": "unanswerable",
                "question": "unsupported",
                "required_tools": [],
            },
        ]
        predictions = [
            {
                "case_id": "LIT-X",
                "selected_tools": ["search_literature"],
                "execution_order": ["search_literature"],
                "task_type": "literature_only",
                "confidence": 1.0,
                "routing_reason": "test",
                "rule_match": {"rule_id": "R-LIT"},
                "unsupported_reason": None,
            },
            {
                "case_id": "UNA-X",
                "selected_tools": ["query_training_log"],
                "execution_order": ["query_training_log"],
                "task_type": "log_lookup",
                "confidence": 1.0,
                "routing_reason": "test",
                "rule_match": {"rule_id": "R-WRONG"},
                "unsupported_reason": None,
            },
        ]
        result = evaluate_routing(cases, predictions)
        aggregate = result["aggregate"]
        self.assertAlmostEqual(aggregate["tool_selection_accuracy"], 5 / 6)
        self.assertEqual(aggregate["exact_tool_set_match_accuracy"], 0.5)
        self.assertEqual(aggregate["unnecessary_tool_call_count"], 1)
        self.assertEqual(aggregate["unnecessary_tool_call_rate"], 0.5)
        self.assertEqual(aggregate["missing_required_tool_rate"], 0.0)
        self.assertEqual(aggregate["unanswerable_no_tool_routing_accuracy"], 0.0)


if __name__ == "__main__":
    unittest.main()
