from typing import TypedDict, Optional, List, Dict, Any

class CodeSenseState(TypedDict):
    """
    The state for the LangGraph orchestrator.
    """
    question: str
    intent: Optional[str]  # 'search', 'impact', 'explain', or 'refactor'
    search_results: Optional[List[Dict[str, Any]]]
    impact_results: Optional[Dict[str, Any]]
    final_answer: Optional[str]
    error: Optional[str]
    requires_approval: Optional[bool]
    diff_data: Optional[str]
