"""Evaluation-only server observer: receives questions, never reads labels."""
import os
from pathlib import Path

from src.api.app import create_app
from src.api.dependencies import get_workflow
from src.evaluation.phase_a_semantics import observe_state


def create_observed_app():
    from src.evaluation.acceptance_v2_runner import save_exclusive
    destination = Path(os.environ["AGENTIC_RAG_EVAL_OUTPUT"])
    if not destination.is_dir():
        raise ValueError("observer_directory_missing")

    class Observer:
        sequence = 0

        def invoke(self, question):
            self.sequence += 1
            state = get_workflow().invoke(question)
            # Only allowlisted operations/semantics/usage; no raw workout rows.
            observation = observe_state(f"request-{self.sequence}", state)
            save_exclusive(destination / f"{self.sequence:03}.json", observation)
            return state

    observer = Observer()
    app = create_app()
    app.dependency_overrides[get_workflow] = lambda: observer
    return app
