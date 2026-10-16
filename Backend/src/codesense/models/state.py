from typing import TypedDict, Optional, List, Dict, Any

class CodeSenseState(TypedDict):
    """
    The state for the LangGraph orchestrator.
    """
    question: str
    intent: Optional[str]  # 'search', 'impact', 'explain', or 'refactor'
    _target_symbol: Optional[str]  # Symbol extracted by intent classifier for impact queries
    _dependency_direction: Optional[str]
    _exact_symbol_lookup: Optional[bool]
    _source_dependency_analysis: Optional[bool]
    _audit_user_id: Optional[str]
    _audit_session_id: Optional[str]
    _repository_path: Optional[str]
    search_results: Optional[List[Dict[str, Any]]]
    impact_results: Optional[Dict[str, Any]]
    final_answer: Optional[str]
    plan: Optional[str]
    error: Optional[str]
    requires_approval: Optional[bool]
    diff_data: Optional[str]
