import pytest

from codesense.mcp.server import rename_symbol
from codesense.agents.refactor import RefactorAgent
from codesense.agents.supervisor import SupervisorAgent
from codesense.agents.dependency_graph import DependencyGraphAgent


def test_refactor_agent_extracts_explicit_symbol_rename():
    prompt = (
        "Relevant code from the repository:\n"
        "- File: `src/database.py` (Lines 10-12)\n"
        "```python\n"
        "def get_db_sessions():\n"
        "    yield None\n"
        "```\n\n"
        "User Request: rename get_db_sessions to get_db_session"
    )

    assert RefactorAgent._requested_symbol_rename(prompt) == (
        "get_db_sessions",
        "get_db_session",
    )
    assert RefactorAgent._definition_file(prompt, "get_db_sessions") == "src/database.py"


def test_refactor_agent_falls_back_to_repository_scan_when_context_lacks_definition():
    prompt = (
        "Relevant code from the repository:\n"
        "- File: `src/database.py` (Lines 10-12)\n"
        "```python\n"
        "def get_db_session():\n"
        "    yield None\n"
        "```\n\n"
        "User Request: rename get_db_sessions to get_db_session"
    )

    assert RefactorAgent._definition_file(prompt, "get_db_sessions") is None
    assert RefactorAgent._rename_file_path(prompt, "get_db_sessions") == "."


def test_exact_definition_query_reads_source_instead_of_trusting_semantic_index(
    tmp_path, monkeypatch
):
    repository = tmp_path / "repo"
    other_repository = tmp_path / "other-repo"
    database_path = repository / "backend" / "app" / "core" / "database.py"
    database_path.parent.mkdir(parents=True)
    (other_repository / "backend" / "app" / "core").mkdir(parents=True)
    database_path.write_text(
        "async def get_db_session():\n"
        "    yield None\n",
        encoding="utf-8",
    )
    (other_repository / "backend" / "app" / "core" / "database.py").write_text(
        "async def get_db_sessions():\n"
        "    yield None\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODESENSE_REPO_PATH", str(other_repository))

    agent = SupervisorAgent.__new__(SupervisorAgent)
    agent.search_agent = type(
        "SearchAgent",
        (),
        {
            "search_codebase": lambda *_args, **_kwargs: [
                {
                    "file_path": str(database_path),
                    "symbol_name": "get_db_sessions",
                    "line_range": "1-2",
                    "snippet": "async def get_db_sessions():\\n    yield None",
                }
            ]
        },
    )()
    state = {
        "question": "Where is get_db_sessions defined?",
        "_repository_path": str(repository),
    }

    class Classifier:
        def invoke(self, _prompt):
            raise AssertionError("Exact definition queries should not use the LLM classifier")

    agent.llm = Classifier()
    result = agent._classify_intent(state)
    assert result["intent"] == "search"
    assert result["_target_symbol"] == "get_db_sessions"

    result = agent._run_semantic_search(result)

    assert result["_exact_symbol_lookup"] is True
    assert result["search_results"] == []
    assert "No Python function or class named `get_db_sessions`" in result["final_answer"]
    assert agent._route_after_search(result) == "output_guardrail"


def test_exact_definition_query_returns_actual_source_location(tmp_path, monkeypatch):
    repository = tmp_path / "repo"
    database_path = repository / "backend" / "app" / "core" / "database.py"
    database_path.parent.mkdir(parents=True)
    database_path.write_text(
        "async def get_db_sessions():\n"
        "    yield None\n",
        encoding="utf-8",
    )
    agent = SupervisorAgent.__new__(SupervisorAgent)
    agent.search_agent = type(
        "SearchAgent",
        (),
        {"search_codebase": lambda *_args, **_kwargs: pytest.fail("vector search should not run")},
    )()
    state = {
        "question": "Where is get_db_sessions defined?",
        "_repository_path": str(repository),
    }

    result = agent._run_semantic_search(state)

    assert len(result["search_results"]) == 1
    assert result["search_results"][0]["file_path"] == "backend/app/core/database.py"
    assert result["search_results"][0]["line_range"] == "1-2"
    assert "`backend/app/core/database.py` (lines 1-2)" in result["final_answer"]
    assert "async def get_db_sessions():" in result["final_answer"]


def test_refactor_context_paths_are_relative_to_selected_repository(tmp_path):
    repository = tmp_path / "repo"
    target = repository / "src" / "database.py"
    target.parent.mkdir(parents=True)
    target.write_text("def get_db_session():\n    yield None\n", encoding="utf-8")

    assert SupervisorAgent._repository_relative_path(target, repository) == "src/database.py"
    assert SupervisorAgent._repository_relative_path(tmp_path / "outside.py", repository) is None


def test_impact_target_ignores_action_words():
    assert SupervisorAgent._normalize_impact_target(
        "replacing", "replacing get_db will affect what other files"
    ) == "get_db"
    assert SupervisorAgent._normalize_impact_target(
        "changing", "changing database will affect what other files"
    ) == "database"
    assert SupervisorAgent._normalize_impact_target(
        "Where", "Where is get_db_sessions dependant to?"
    ) == "get_db_sessions"


def test_dependency_analysis_corrects_unique_near_match_without_references(tmp_path):
    repository = tmp_path / "selected"
    database_file = repository / "backend" / "app" / "core" / "database.py"
    database_file.parent.mkdir(parents=True)
    database_file.write_text(
        "async def get_db_session():\n"
        "    yield None\n",
        encoding="utf-8",
    )
    agent = SupervisorAgent.__new__(SupervisorAgent)
    agent.graph_agent = type(
        "GraphAgent",
        (),
        {"get_impact": lambda *_args, **_kwargs: pytest.fail("stale graph must not be trusted")},
    )()
    state = {
        "question": "Where is get_db_sessions dependant to?",
        "_target_symbol": "Where",
        "_dependency_direction": "dependents",
        "_repository_path": str(repository),
    }

    result = agent._run_dependency_graph(state)

    assert result["impact_results"]["exists"] is True
    assert result["impact_results"]["target_symbol"] == "get_db_session"
    assert result["impact_results"]["related_count"] == 0
    assert "`get_db_sessions` was not found" in result["final_answer"]
    assert "`get_db_session`" in result["final_answer"]
    assert "safe to change" not in result["final_answer"]
    assert SupervisorAgent._route_after_dependency_graph(result) == "output_guardrail"


def test_dependency_analysis_reports_source_references_in_selected_repository(tmp_path):
    repository = tmp_path / "selected"
    repository.mkdir()
    (repository / "database.py").write_text(
        "def get_db_session():\n"
        "    return None\n",
        encoding="utf-8",
    )
    (repository / "routes.py").write_text(
        "def get_user():\n"
        "    return get_db_session()\n",
        encoding="utf-8",
    )
    agent = SupervisorAgent.__new__(SupervisorAgent)
    state = {
        "question": "Where is get_db_session dependant to?",
        "_target_symbol": "get_db_session",
        "_dependency_direction": "dependents",
        "_repository_path": str(repository),
    }

    result = agent._run_dependency_graph(state)

    assert result["impact_results"]["exists"] is True
    assert result["impact_results"]["related_count"] == 1
    assert result["impact_results"]["related_symbols"][0]["caller_symbol"] == "get_user"
    assert "`routes.py`" in result["final_answer"]
    assert "get_user" in result["final_answer"]


def test_dependency_analysis_corrects_unique_near_match_and_reports_dependents(tmp_path):
    repository = tmp_path / "selected"
    repository.mkdir()
    (repository / "database.py").write_text(
        "async def get_db_sessions():\n"
        "    yield None\n",
        encoding="utf-8",
    )
    (repository / "auth.py").write_text(
        "from database import get_db_sessions\n"
        "def login():\n"
        "    return get_db_sessions()\n",
        encoding="utf-8",
    )
    agent = SupervisorAgent.__new__(SupervisorAgent)
    state = {
        "question": "but when get_db_sesion dependant to?",
        "_target_symbol": "get_db_sesion",
        "_dependency_direction": "dependents",
        "_repository_path": str(repository),
    }

    result = agent._run_dependency_graph(state)

    assert result["impact_results"]["target_symbol"] == "get_db_sessions"
    assert result["impact_results"]["related_count"] == 2
    assert "`get_db_sesion` was not found" in result["final_answer"]
    assert "`get_db_sessions`" in result["final_answer"]
    assert "`auth.py`" in result["final_answer"]


def test_dependency_question_direction():
    assert SupervisorAgent._dependency_direction("What depends on HybridRetriever?") == "dependents"
    assert SupervisorAgent._dependency_direction("What does HybridRetriever depend on?") == "dependencies"
    assert SupervisorAgent._dependency_direction("HybridRetriever's dependencies") == "dependencies"
    assert SupervisorAgent._dependency_direction(
        "Where is get_db_sessions dependant to?"
    ) == "dependents"
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


def test_dependency_graph_does_not_claim_empty_graph_means_safe_to_change():
    class GraphStore:
        def get_dependents(self, _symbol_name, max_hops):
            assert max_hops == 3
            return []

    result = DependencyGraphAgent(GraphStore()).get_impact("get_db_sessions")

    assert "No dependents were found" in result["summary"]
    assert "inconclusive" in result["summary"]
    assert "safe to change" not in result["summary"]


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


def test_rename_symbol_finds_definition_when_search_result_file_is_stale(tmp_path, monkeypatch):
    repository = tmp_path / "repo"
    database_path = repository / "backend" / "app" / "core" / "database.py"
    other_path = repository / "backend" / "app" / "core" / "other.py"
    caller_path = repository / "backend" / "app" / "api.py"
    database_path.parent.mkdir(parents=True)
    caller_path.parent.mkdir(parents=True, exist_ok=True)
    database_path.write_text(
        "from sqlalchemy.ext.asyncio import AsyncSession\n"
        "async def get_db_sessions():\n"
        "    async with AsyncSessionLocal() as session:\n"
        "        yield session\n",
        encoding="utf-8",
    )
    other_path.write_text(
        "async def get_db_session():\n"
        "    yield None\n",
        encoding="utf-8",
    )
    caller_path.write_text(
        "from app.core.database import get_db_sessions\n"
        "dependency = Depends(get_db_sessions)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODESENSE_REPO_PATH", str(repository))

    result = rename_symbol(".", "get_db_sessions", "get_db_session")

    assert "Renamed" in result
    updated_database = database_path.read_text(encoding="utf-8")
    assert "async def get_db_session():" in updated_database
    assert "get_db_sessions" not in updated_database
    assert "from app.core.database import get_db_session" in caller_path.read_text(
        encoding="utf-8"
    )
    assert "async def get_db_session():" in other_path.read_text(encoding="utf-8")


def test_rename_symbol_keeps_existing_destination_and_redirects_old_references(
    tmp_path, monkeypatch
):
    repository = tmp_path / "repo"
    repository.mkdir()
    source_path = repository / "database.py"
    caller_path = repository / "consumer.py"
    source_path.write_text(
        "async def get_db_sessions():\n"
        "    yield 'obsolete implementation'\n\n"
        "async def get_db_session():\n"
        "    yield 'existing implementation'\n\n"
        "dependency = Depends(get_db_sessions)\n",
        encoding="utf-8",
    )
    caller_path.write_text(
        "from database import get_db_sessions\n"
        "dependency = Depends(get_db_sessions)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODESENSE_REPO_PATH", str(repository))

    result = rename_symbol("database.py", "get_db_sessions", "get_db_session")

    assert "Renamed" in result
    updated = source_path.read_text(encoding="utf-8")
    assert "get_db_sessions" not in updated
    assert updated.count("async def get_db_session(") == 1
    assert "yield 'existing implementation'" in updated
    assert "Depends(get_db_session)" in updated
    assert "from database import get_db_session" in caller_path.read_text(
        encoding="utf-8"
    )
    assert "Depends(get_db_session)" in caller_path.read_text(encoding="utf-8")


def test_rename_symbol_reports_missing_source_without_changing_existing_target(
    tmp_path, monkeypatch
):
    repository = tmp_path / "repo"
    source_path = repository / "backend" / "app" / "core" / "database.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        "async def get_db_session():\n"
        "    yield None\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODESENSE_REPO_PATH", str(repository))

    result = rename_symbol(".", "get_db_sessions", "get_db_session")

    assert "no unique function or class definition named 'get_db_sessions'" in result
    assert source_path.read_text(encoding="utf-8") == (
        "async def get_db_session():\n"
        "    yield None\n"
    )