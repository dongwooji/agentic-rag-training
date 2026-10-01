"""API routing extensions, separate from the frozen router_baseline_v1."""

from copy import deepcopy
from dataclasses import replace

from .contracts import RouterInput, RoutingPlan
from .deterministic import DeterministicRouter, load_router_config
from .runtime_language import RuntimeQuestionNormalizer, phrase_pattern


RUNTIME_ROUTER_VERSION = "runtime_router_korean_v1"


class RuntimeRouter:
    def __init__(self, normalizer: RuntimeQuestionNormalizer | None = None) -> None:
        self.normalizer = normalizer or RuntimeQuestionNormalizer()
        config = deepcopy(load_router_config())
        config["structured_log_signals"]["regex"].extend(
            self.normalizer.config["personal_record_patterns"]
        )
        exercises = [
            alias
            for canonical, aliases in self.normalizer.exercise_aliases.items()
            for alias in [canonical, *aliases]
        ]
        exercise_pattern = "|".join(phrase_pattern(alias) for alias in exercises)
        config["structured_log_signals"]["regex"].append(
            r"(?<![가-힣A-Za-z0-9])(?:내|나의|제|저의)\s*(?:" + exercise_pattern + r")"
        )
        config["metric_signals"]["literals"].extend(
            alias
            for canonical, aliases in self.normalizer.config["metric_aliases"].items()
            if canonical != "median"
            for alias in aliases
        )
        config["metric_signals"]["regex"].extend(
            r"(?<![가-힣A-Za-z0-9])" + phrase_pattern(alias)
            for canonical, aliases in self.normalizer.config["metric_aliases"].items()
            if canonical != "median"
            for alias in aliases
        )
        config["metric_signals"]["literals"].extend(["훈련량 변화", "운동량 변화"])
        self._router = DeterministicRouter(config)

    def route(self, request: RouterInput) -> RoutingPlan:
        # Route on the original wording: an exercise in a literature question alone
        # does not establish that personal records are requested.
        plan = self._router.route(request)
        return replace(plan, router_version=RUNTIME_ROUTER_VERSION)
