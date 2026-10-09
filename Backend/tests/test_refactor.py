from codesense.mcp.server import rename_symbol
from codesense.agents.supervisor import SupervisorAgent
from codesense.agents.dependency_graph import DependencyGraphAgent


def test_impact_target_ignores_action_words():
    assert SupervisorAgent._normalize_impact_target(
        "replacing", "replacing get_db will affect what other files"
    ) == "get_db"
    assert SupervisorAgent._normalize_impact_target(
        "changing", "changing database will affect what other files"
    ) == "database"


def test_dependency_question_direction():
    assert SupervisorAgent._dependency_direction("What depends on HybridRetriever?") == "dependents"
    assert SupervisorAgent._dependency_direction("What does HybridRetriever depend on?") == "dependencies"
    assert SupervisorAgent._dependency_direction("HybridRetriever's dependencies") == "dependencies"
    assert SupervisorAgent._dependency_direction("HybridRetriever explain its usage?") is None


def test_dependency_question_routes_to_impact_even_if_llm_says_explain():
    class Classifier:
        def invoke(self, _prompt):
            return type("Result", (), {"intent": "explain", "extracted_symbol": None})()

    agent = SupervisorAgent.__new__(SupervisorAgent)
    agent.llm = Classifier()
    state = {"question": "What does HybridRetriever depend on?"}

    result = agent._classify_intent(state)

    assert result["intent"] == "impact"
    assert result["_dependency_direction"] == "dependencies"


def test_dependency_graph_agent_uses_outbound_direction():
    class GraphStore:
        def get_dependencies(self, symbol_name, max_hops):
            assert symbol_name == "HybridRetriever"
            assert max_hops == 2
            return [{"symbol_name": "EmbeddingModel", "file_path": "embeddings.py"}]

        def get_dependents(self, _symbol_name, _max_hops):
            raise AssertionError("outbound dependency query used incoming traversal")

    result = DependencyGraphAgent(GraphStore()).get_impact(
        "HybridRetriever", max_hops=2, direction="dependencies"
    )

    assert result["direction"] == "dependencies"
    assert result["dependencies"] == [{"symbol_name": "EmbeddingModel", "file_path": "embeddings.py"}]
    assert "EmbeddingModel" in result["summary"]


def test_rename_symbol_changes_resolved_identifiers_only(tmp_path, monkeypatch):
    repository = tmp_path / "repo"
    repository.mkdir()
    source_path = repository / "sample.py"
    source_path.write_text(
        """def clean_text(value):
    return value


def wrapper(clean_text):
    return clean_text


result = clean_text('clean_text')
# clean_text must remain in this comment
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODESENSE_REPO_PATH", str(repository))

    result = rename_symbol("sample.py", "clean_text", "preprocess_text")

    assert "Renamed" in result
    updated = source_path.read_text(encoding="utf-8")
    assert "def preprocess_text(value):" in updated
    assert "result = preprocess_text('clean_text')" in updated
    assert "def wrapper(clean_text):" in updated
    assert "clean_text must remain in this comment" in updated


def test_rename_symbol_updates_cross_file_imports(tmp_path, monkeypatch):
    repository = tmp_path / "repo"
    package = repository / "app" / "core"
    package.mkdir(parents=True)
    database_path = package / "database.py"
    security_path = package / "security.py"
    database_path.write_text(
        "async def get_db():\n    yield None\n",
        encoding="utf-8",
    )
    security_path.write_text(
        "from app.core.database import get_db\n\nasync def current_user(db=Depends(get_db)):\n    return db\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODESENSE_REPO_PATH", str(repository))

    result = rename_symbol("app/core/database.py", "get_db", "get_db_session")

    assert "resolved identifier(s) across 2 file(s)" in result
    assert "async def get_db_session" in database_path.read_text(encoding="utf-8")
    assert "from app.core.database import get_db_session" in security_path.read_text(encoding="utf-8")
    assert "Depends(get_db_session)" in security_path.read_text(encoding="utf-8")