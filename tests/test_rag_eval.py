"""AI evaluation — RAG retrieval quality assessment with Ragas.

Note: Requires OpenAI-compatible API. Skipped in CI; runs locally with configured backend.
"""
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest


@pytest.mark.skipif(
    not os.environ.get("TAVILY_API_KEY") and not os.environ.get("DEEPSEEK_API_KEY"),
    reason="No LLM API for AI evaluation",
)
class TestRAGEvaluation:
    """RAG retrieval quality — Ragas metrics for relevancy and faithfulness."""

    @pytest.fixture
    def eval_data(self):
        return [
            {
                "question": "什么是灵枢？",
                "ground_truth": "灵枢是一个多Agent编排系统，基于FastAPI和LangGraph构建。",
                "contexts": [
                    "灵枢是一个多Agent编排框架，支持Supervisor-Worker模式。",
                    "后端使用FastAPI，前端使用pywebview。",
                ],
            },
        ]

    def test_context_relevancy(self, eval_data):
        try:
            from ragas import evaluate
            from ragas.metrics import context_relevancy
        except ImportError:
            pytest.skip("ragas not installed: pip install ragas")
        for item in eval_data:
            result = evaluate(
                datasets=[{
                    "question": item["question"],
                    "contexts": item["contexts"],
                    "ground_truth": item["ground_truth"],
                }],
                metrics=[context_relevancy],
            )
            print("Context relevancy:", result[context_relevancy])

    def test_faithfulness(self, eval_data):
        try:
            from ragas import evaluate
            from ragas.metrics import faithfulness
        except ImportError:
            pytest.skip("ragas not installed")
        for item in eval_data:
            result = evaluate(
                datasets=[{
                    "question": item["question"],
                    "contexts": item["contexts"],
                    "ground_truth": item["ground_truth"],
                }],
                metrics=[faithfulness],
            )
            print("Faithfulness:", result[faithfulness])