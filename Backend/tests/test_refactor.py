from codesense.mcp.server import rename_symbol
from codesense.agents.supervisor import SupervisorAgent


def test_impact_target_ignores_action_words():
    assert SupervisorAgent._normalize_impact_target(
        "replacing", "replacing get_db will affect what other files"
    ) == "get_db"
    assert SupervisorAgent._normalize_impact_target(
        "changing", "changing database will affect what other files"
    ) == "database"


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