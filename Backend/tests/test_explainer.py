from codesense.agents import explainer


def test_explainer_uses_larger_output_token_budget(monkeypatch):
    captured = {}

    def fake_get_llm(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(explainer, "get_llm", fake_get_llm)

    explainer.ExplainerAgent()

    assert captured == {
        "purpose": "fast",
        "temperature": 0,
        "max_tokens": 1024,
    }