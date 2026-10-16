import os
import difflib
import re
import subprocess
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field
from codesense.llm_manager import get_llm
from langgraph.graph import StateGraph, START, END
from codesense.models.state import CodeSenseState
from codesense.guardrails import (
    MAX_ANSWER_CHARS,
    MAX_DIFF_CHARS,
    GuardrailViolation,
    contains_sensitive_data,
    log_guardrail_denial,
    sanitize_output,
    validate_user_input,
)
from codesense.agents.semantic_search import SemanticSearchAgent
from codesense.ingestion.parser import CodeParser, SUPPORTED_EXTENSIONS
from codesense.ingestion.chunker import SemanticChunker
from codesense.ingestion.symbol_table import SymbolExtractor
from codesense.agents.dependency_graph import DependencyGraphAgent
from codesense.agents.explainer import ExplainerAgent
from codesense.vector_store import VectorStore
from codesense.graph_store import GraphStore

class IntentClassification(BaseModel):
    intent: Literal["search", "impact", "explain", "refactor"] = Field(
        description="The classified intent of the user's question. "
                    "'search' for finding where something is or how it's implemented. "
                    "'impact' for questions about blast radius, dependencies, or what breaks if something changes. "
                    "'explain' for general architectural or 'how does it work' questions."
                    "'refactor' for requests to change, rename, rewrite, or refactor code."
    )
    extracted_symbol: str | None = Field(
        description="If the intent is 'impact', the specific symbol (function/class name) the user is asking about. Otherwise null.",
        default=None
    )

class SupervisorAgent:
    def __init__(self, user_id: str = "default", model_name: str = "qwen/qwen3.8-27b"):
        self.user_id = user_id
        self.llm = get_llm(
            purpose="fast",
            temperature=0,
            max_tokens=128,
        ).with_structured_output(IntentClassification)
        
        # Initialize specialized isolated stores
        self.vector_store = VectorStore(user_id=self.user_id)
        self.graph_store = GraphStore(user_id=self.user_id)

        # Initialize specialist tools with isolated stores
        self.search_agent = SemanticSearchAgent(vector_store=self.vector_store)
        self.graph_agent = DependencyGraphAgent(graph_store=self.graph_store)
        self.explainer_agent = ExplainerAgent(model_name=model_name)
        
        from codesense.agents.refactor import RefactorAgent
        self.refactor_agent = RefactorAgent(vector_store=self.vector_store)  # Uses its own tool-calling model
        
        # Build the graph
        self.graph = self._build_graph()

    @staticmethod
    def _validate_input(state: CodeSenseState) -> CodeSenseState:
        try:
            state["question"] = validate_user_input(state.get("question"))
        except GuardrailViolation as error:
            log_guardrail_denial(
                "query",
                state.get("question"),
                str(error),
                user_id=state.get("_audit_user_id"),
                session_id=state.get("_audit_session_id"),
            )
            state["error"] = str(error)
        return state

    @staticmethod
    def _route_after_input(state: CodeSenseState) -> str:
        return END if state.get("error") else "supervisor"

    @staticmethod
    def _apply_output_guardrails(state: CodeSenseState) -> CodeSenseState:
        answer = state.get("final_answer")
        if answer:
            state["final_answer"] = sanitize_output(answer, MAX_ANSWER_CHARS)

        diff = state.get("diff_data")
        if diff:
            sanitized_diff = sanitize_output(diff, MAX_DIFF_CHARS, format_hint="diff")
            if len(diff) > MAX_DIFF_CHARS or contains_sensitive_data(diff) or sanitized_diff != diff:
                log_guardrail_denial(
                    "refactor_diff",
                    diff,
                    "The proposed changes exceed the safe output limit or contain sensitive data.",
                    user_id=state.get("_audit_user_id"),
                    session_id=state.get("_audit_session_id"),
                )
                state["diff_data"] = None
                state["requires_approval"] = False
                state["error"] = (
                    "The proposed changes exceed the safe output limit or contain sensitive data. "
                    "No changes are available for approval."
                )
            else:
                state["diff_data"] = sanitized_diff
        return state

    def _classify_intent(self, state: CodeSenseState) -> CodeSenseState:
        """Node 1: Classify the user's intent."""
        exact_symbol = self._defined_symbol_query(state["question"])
        if exact_symbol:
            state["intent"] = "search"
            state["_target_symbol"] = exact_symbol
            state["_dependency_direction"] = None
            state["_exact_symbol_lookup"] = True
            return state

        print(f"Supervisor: Analyzing intent for '{state['question']}'")
        try:
            result = self.llm.invoke(f"Classify the following codebase question: {state['question']}")
            direction = self._dependency_direction(state["question"])
            state["intent"] = "impact" if direction else result.intent
            state["_dependency_direction"] = direction
            
            if result.intent == "impact" and result.extracted_symbol:
                state["_target_symbol"] = result.extracted_symbol
            
            print(f"Supervisor: Classified intent as '{state['intent']}'")
        except Exception as e:
            # Always try keyword fallback first — handles rate limits AND tool_use_failed
            intent, symbol = self._fallback_intent(state["question"])
            direction = self._dependency_direction(state["question"])
            state["intent"] = "impact" if direction else intent
            state["_dependency_direction"] = direction
            if symbol:
                state["_target_symbol"] = symbol
            print(
                f"Supervisor: Structured classification failed ({type(e).__name__}); "
                f"using keyword fallback intent '{intent}'"
            )
        return state

    @staticmethod
    def _fallback_intent(question: str) -> tuple[str, str | None]:
        """Rule-based intent classifier used when the LLM structured output fails for any reason."""
        q = question.lower()
        words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", question)
        
        # Symbols: snake_case or CamelCase words that aren't common words
        stopwords = {"what", "where", "how", "does", "is", "it", "the", "a", "an",
                     "to", "if", "i", "do", "will", "be", "in", "of", "and", "or"}
        symbol_candidates = [w for w in words if ("_" in w or w[:1].isupper()) and w.lower() not in stopwords]
        top_symbol = symbol_candidates[0] if symbol_candidates else None
        
        refactor_terms = {"rename", "refactor", "replace", "rewrite", "move"}
        impact_terms  = {"dependent", "dependant", "depend", "dependencies", "impact", "affect",
                         "effect", "break", "uses", "callers", "calls", "blast"}
        search_terms  = {"where", "find", "locate", "which", "file", "defined", "implemented", "show"}
        
        if any(t in q for t in refactor_terms):
            return "refactor", top_symbol
        if any(t in q for t in impact_terms):
            return "impact", top_symbol
        if any(t in q for t in search_terms):
            return "search", top_symbol
        return "explain", top_symbol

    @staticmethod
    def _dependency_direction(question: str) -> str | None:
        """Return the requested edge direction for a dependency question."""
        q = question.lower()
        if not re.search(r"\bdepend(?:s|ed|ent|ents|ant|ants|enc(?:y|ies))?\b", q):
            return None

        if (
            re.search(r"\b(?:what|which|who)\s+depend(?:s|ed)?\s+on\b", q)
        ):
            return "dependents"

        if (
            re.search(r"\bdepend(?:s|ed|ent)?\s+on\b", q)
            or re.search(r"\bdependenc(?:y|ies)\s+(?:of|for)\b", q)
            or re.search(r"\b[A-Za-z_][A-Za-z0-9_]*['’]s\s+dependenc(?:y|ies)\b", question, re.IGNORECASE)
        ):
            return "dependencies"
        return "dependents"

    def _route(self, state: CodeSenseState) -> str:
        """Conditional router based on intent."""
        if state.get("error"):
            return END
            
        intent = state.get("intent")
        if intent == "impact":
            return "dependency_graph"
        elif intent == "search" or intent == "refactor":
            return "semantic_search"
        else:
            # Explain usually requires searching first to get context, so we route to search then explain
            return "semantic_search"

    def _run_semantic_search(self, state: CodeSenseState) -> CodeSenseState:
        """Node: Semantic Search Specialist"""
        exact_symbol = self._defined_symbol_query(state["question"])
        if exact_symbol:
            state["_exact_symbol_lookup"] = True
            repo_path = Path(
                state.get("_repository_path")
                or os.getenv("CODESENSE_REPO_PATH")
                or Path.cwd() / "repos" / "demo"
            ).resolve()
            if not repo_path.is_dir():
                state["final_answer"] = (
                    f"I couldn't verify `{exact_symbol}` because no active repository "
                    "is configured."
                )
                state["search_results"] = []
                return state

            definitions = self._find_python_definitions(repo_path, exact_symbol)
            state["search_results"] = definitions
            if not definitions:
                state["final_answer"] = (
                    f"No Python function or class named `{exact_symbol}` was found "
                    "in the selected repository."
                )
            elif len(definitions) == 1:
                definition = definitions[0]
                state["final_answer"] = (
                    f"`{exact_symbol}` is defined in "
                    f"`{definition['file_path']}` (lines {definition['line_range']}).\n\n"
                    f"```python\n{definition['snippet']}\n```"
                )
            else:
                locations = "\n".join(
                    f"- `{definition['file_path']}` (lines {definition['line_range']})"
                    for definition in definitions
                )
                state["final_answer"] = (
                    f"Found {len(definitions)} Python definitions named "
                    f"`{exact_symbol}` in the selected repository:\n{locations}"
                )
            return state

        print("Semantic Search: Retrieving code chunks...")
        results = self.search_agent.search_codebase(state["question"], limit=5)
        state["search_results"] = results
        return state

    @staticmethod
    def _defined_symbol_query(question: str) -> str | None:
        match = re.search(
            r"\bwhere\s+is\s+(?:the\s+)?(?:function|class|symbol)?\s*"
            r"`?([A-Za-z_]\w*)`?\s+defined\b",
            question,
            re.IGNORECASE,
        )
        return match.group(1) if match else None

    @staticmethod
    def _find_python_definitions(repo_path: Path, symbol: str) -> list[dict[str, str]]:
        parser = CodeParser()
        skipped_directories = {
            ".git", ".venv", "__pycache__", "node_modules", "dist", "build", "target"
        }
        definitions = []

        for file_path in repo_path.rglob("*.py"):
            if any(part in skipped_directories for part in file_path.relative_to(repo_path).parts):
                continue
            tree, source = parser.parse_file(str(file_path))
            pending = [tree.root_node]
            while pending:
                node = pending.pop()
                if node.type in {"function_definition", "class_definition"}:
                    name_node = node.child_by_field_name("name")
                    if name_node and name_node.text.decode("utf-8") == symbol:
                        definition_node = (
                            node.parent
                            if node.parent and node.parent.type == "decorated_definition"
                            else node
                        )
                        definitions.append(
                            {
                                "file_path": file_path.relative_to(repo_path).as_posix(),
                                "line_range": (
                                    f"{definition_node.start_point[0] + 1}-"
                                    f"{definition_node.end_point[0] + 1}"
                                ),
                                "snippet": source[
                                    definition_node.start_byte:definition_node.end_byte
                                ].decode("utf-8"),
                            }
                        )
                pending.extend(reversed(node.children))
        return definitions

    def _run_dependency_graph(self, state: CodeSenseState) -> CodeSenseState:
        """Node: Dependency Graph Specialist"""
        symbol = self._normalize_impact_target(
            state.get("_target_symbol"), state["question"]
        )
        
        if not symbol:
            # Fallback heuristic: find CamelCase or snake_case words if LLM failed to extract
            import re
            words = re.findall(r'[A-Za-z_][A-Za-z0-9_]*', state["question"])
            # Filter out common capitalized sentence starters
            ignore = {"What", "If", "How", "Do", "I", "Need", "The"}
            potential = [w for w in words if w not in ignore and (w[0].isupper() or '_' in w)]
            symbol = potential[0] if potential else state["question"].split()[0]

        repo_path = state.get("_repository_path")
        if repo_path:
            repository = Path(repo_path).resolve()
            if not repository.is_dir():
                state["error"] = "The selected repository is unavailable for dependency analysis."
                return state

            direction = state.get("_dependency_direction") or "dependents"
            definitions, related, resolved_symbol = self._scan_repository_dependencies(
                repository, symbol, direction
            )
            state["_source_dependency_analysis"] = True
            relation = "dependents" if direction == "dependents" else "dependencies"
            if not definitions:
                state["impact_results"] = {
                    "target_symbol": symbol,
                    "direction": relation,
                    "exists": False,
                    "related_count": 0,
                    "related_symbols": [],
                }
                state["final_answer"] = (
                    f"No definition of `{symbol}` was found in the selected repository, "
                    f"so I can't determine its {relation}."
                )
                return state

            state["impact_results"] = {
                "target_symbol": resolved_symbol,
                "direction": relation,
                "exists": True,
                "definitions": definitions,
                "related_count": len(related),
                "related_symbols": related,
            }
            correction = (
                f"`{symbol}` was not found; using the matching definition "
                f"`{resolved_symbol}` instead.\n\n"
                if resolved_symbol != symbol
                else ""
            )
            if related:
                details = []
                for item in related:
                    origin = (
                        f" from `{item['caller_symbol']}`"
                        if item["caller_symbol"]
                        else ""
                    )
                    details.append(
                        f"- `{item['file_path']}` (line {item['line_number']}){origin}"
                    )
                relation_phrase = (
                    "direct dependent reference"
                    if relation == "dependents"
                    else "direct dependency reference"
                )
                state["final_answer"] = (
                    correction
                    + f"Found {len(related)} {relation_phrase}"
                    f"{'s' if len(related) != 1 else ''} "
                    f"{'to' if relation == 'dependents' else 'from'} `{resolved_symbol}` "
                    f"in the selected repository:\n"
                    + "\n".join(details)
                )
            else:
                state["final_answer"] = (
                    correction
                    + f"Found `{resolved_symbol}` in the selected repository, but detected no direct "
                    f"{relation} in supported source files."
                )
            return state

        print(f"Dependency Graph: Traversing graph for '{symbol}'...")
        
        results = self.graph_agent.get_impact(
            symbol, direction=state.get("_dependency_direction") or "dependents"
        )
        state["impact_results"] = results
        return state

    @staticmethod
    def _scan_repository_dependencies(
        repo_path: Path, symbol: str, direction: str
    ) -> tuple[list[dict[str, str]], list[dict[str, str | int | None]], str]:
        parser = CodeParser()
        chunker = SemanticChunker()
        symbol_extractor = SymbolExtractor()
        skipped_directories = {
            ".git", ".venv", "__pycache__", "tests", ".qdrant_db", "node_modules",
            ".tox", "dist", "build", "target",
        }
        definitions_by_symbol: dict[str, list[dict[str, str]]] = {}
        references: list[dict[str, str | int | None]] = []

        for file_path in sorted(repo_path.rglob("*")):
            relative_path = file_path.relative_to(repo_path)
            if (
                not file_path.is_file()
                or file_path.suffix.lower() not in SUPPORTED_EXTENSIONS
                or any(part in skipped_directories for part in relative_path.parts)
            ):
                continue

            tree, source = parser.parse_file(str(file_path))
            for chunk in chunker.chunk_node(tree.root_node, source, str(file_path)):
                if chunk.symbol_name == symbol:
                    definitions_by_symbol.setdefault(symbol, []).append({
                        "file_path": relative_path.as_posix(),
                        "line_range": (
                            f"{chunk.line_range.start_line}-{chunk.line_range.end_line}"
                        ),
                    })
                else:
                    definitions_by_symbol.setdefault(chunk.symbol_name, []).append({
                        "file_path": relative_path.as_posix(),
                        "line_range": (
                            f"{chunk.line_range.start_line}-{chunk.line_range.end_line}"
                        ),
                    })

            for reference in symbol_extractor.extract_references(
                tree.root_node, source, str(file_path)
            ):
                references.append({
                    "file_path": relative_path.as_posix(),
                    "caller_symbol": reference.caller_symbol,
                    "symbol_name": reference.symbol_name.rsplit(".", 1)[-1],
                    "reference_type": reference.reference_type,
                    "line_number": reference.line_number,
                })

        resolved_symbol = symbol
        if resolved_symbol not in definitions_by_symbol:
            close_matches = difflib.get_close_matches(
                symbol,
                sorted(definitions_by_symbol),
                n=2,
                cutoff=0.85,
            )
            if close_matches:
                best_score = difflib.SequenceMatcher(
                    None, symbol.casefold(), close_matches[0].casefold()
                ).ratio()
                next_score = (
                    difflib.SequenceMatcher(
                        None, symbol.casefold(), close_matches[1].casefold()
                    ).ratio()
                    if len(close_matches) > 1
                    else 0
                )
                if best_score >= 0.85 and best_score - next_score >= 0.08:
                    resolved_symbol = close_matches[0]

        definitions = definitions_by_symbol.get(resolved_symbol, [])
        if not definitions:
            return definitions, [], symbol

        related = [
            reference
            for reference in references
            if (
                reference["caller_symbol"] == resolved_symbol
                if direction == "dependencies"
                else reference["symbol_name"] == resolved_symbol
            )
        ]
        unique_related = {
            (
                item["file_path"],
                item["line_number"],
                item["caller_symbol"],
                item["symbol_name"],
                item["reference_type"],
            ): item
            for item in related
        }
        return definitions, list(unique_related.values()), resolved_symbol

    @staticmethod
    def _normalize_impact_target(extracted_symbol: str | None, question: str) -> str:
        """Avoid generic classifier words becoming the dependency target."""
        generic = {
            "a", "affect", "change", "changing", "effect", "effected", "files",
            "depend", "dependent", "dependents", "dependant", "dependants",
            "dependencies", "dependency", "impact", "need", "other", "replace",
            "replacing", "what", "will", "where", "who", "which", "when", "why",
            "how", "does", "is", "are", "the", "to", "on", "of", "for", "it",
        }
        words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", question)
        ignored = generic | {"i"}
        candidates = [word for word in words if word.lower() not in ignored]
        identifier_candidates = [
            word for word in candidates if "_" in word or word[:1].isupper()
        ]
        if identifier_candidates:
            return identifier_candidates[0]
        if extracted_symbol and extracted_symbol.lower() not in generic:
            return extracted_symbol
        if candidates:
            return candidates[0]
        return extracted_symbol or question.split()[0]

    @staticmethod
    def _route_after_dependency_graph(state: CodeSenseState) -> str:
        if state.get("error") or state.get("_source_dependency_analysis"):
            return "output_guardrail"
        return "explainer"

    @staticmethod
    def _repository_relative_path(file_path: str | Path, repo_path: Path) -> str | None:
        try:
            return Path(file_path).resolve().relative_to(repo_path.resolve()).as_posix()
        except ValueError:
            return None

    def _refactor_node(self, state: CodeSenseState) -> CodeSenseState:
        """Node: Run the refactor agent in a sandbox.
        
        Creates a lightweight staging area (skipping .git to avoid slow
        Windows permission copies) so the agent cannot corrupt the live
        working tree.  The diff is generated from the sandbox.
        """
        import shutil
        import stat
        print(f"Refactor Agent: Processing request '{state['question']}'")
        
        repo_path = Path(
            state.get("_repository_path")
            or os.getenv("CODESENSE_REPO_PATH")
            or str(Path.cwd() / "repos" / "demo")
        ).resolve()
        staging_path = Path(f"{repo_path}_staging")
        
        def remove_readonly(func, path, excinfo):
            os.chmod(path, stat.S_IWRITE)
            try:
                func(path)
            except Exception:
                pass

        # 1. Create a lightweight staging area (skip .git for speed)
        if os.path.exists(staging_path):
            shutil.rmtree(staging_path, onerror=remove_readonly)
        shutil.copytree(repo_path, staging_path, ignore=shutil.ignore_patterns('.git'))
        
        # Init a fresh git repo so get_git_diff still works
        subprocess.run(["git", "init"], cwd=staging_path, capture_output=True, check=False)
        subprocess.run(["git", "add", "."], cwd=staging_path, capture_output=True, check=False)
        subprocess.run(["git", "commit", "-m", "baseline", "--allow-empty"], cwd=staging_path,
                        capture_output=True, check=False, env={**os.environ, "GIT_AUTHOR_NAME": "CodeSense",
                        "GIT_AUTHOR_EMAIL": "bot@codesense", "GIT_COMMITTER_NAME": "CodeSense",
                        "GIT_COMMITTER_EMAIL": "bot@codesense"})
        
        # Build a context-enriched prompt with RELATIVE paths so the agent edits the sandbox, not live repo.
        # res['file_path'] from the vector store is an absolute path to the original repo.
        # We convert it to be relative to repo_path. The MCP server resolves it against
        # CODESENSE_REPO_PATH (staging_path), so edits go into the sandbox — not the live working tree.
        context = ""
        if state.get("search_results"):
            context = "Relevant code from the repository:\n"
            for res in state["search_results"][:5]:
                rel_path = self._repository_relative_path(res["file_path"], repo_path)
                if rel_path is None:
                    continue
                context += f"- File: `{rel_path}` (Lines {res['line_range']})\n"
                context += f"```\n{res['snippet']}\n```\n\n"

        prompt = f"{context}\nUser Request: {state['question']}"
        
        try:
            agent_reply = self.refactor_agent.run_sync(
                prompt,
                phase="refactor",
                repository_path=str(staging_path),
                audit_user_id=state.get("_audit_user_id"),
                audit_session_id=state.get("_audit_session_id"),
            )
            
            # Run git diff ourselves — the agent's text reply is NOT the diff
            diff_result = subprocess.run(
                ["git", "--no-pager", "diff"],
                cwd=staging_path, capture_output=True, text=True, check=False,
            ).stdout.strip()
            
            if diff_result:
                state["diff_data"] = diff_result
                state["requires_approval"] = True
                state["final_answer"] = (
                    "I have drafted the refactor in a secure sandbox. "
                    "Please review the diff below and approve to create a PR."
                )
            else:
                # Agent ran but made no file changes
                state["final_answer"] = agent_reply or "The agent completed but no file changes were detected."
        except TimeoutError as error:
            # Fallback if the agent timed out but made edits
            diff_result = subprocess.run(
                ["git", "--no-pager", "diff"],
                cwd=staging_path,
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()

            if diff_result:
                state["diff_data"] = diff_result
                state["requires_approval"] = True
                state["final_answer"] = (
                    "The refactor was applied to the sandbox, but the agent timed out while preparing its response. "
                    "Please review the recovered diff below."
                )
            else:
                state["error"] = str(error)
        except Exception as e:
            state["error"] = f"Refactor failed: {e}"
        return state

    def _refresh_indexes(self) -> str:
        """Rebuild search and dependency indexes after a working-tree refactor."""
        repo_path = os.getenv("CODESENSE_REPO_PATH") or str(Path.cwd() / "repos" / "demo")
        try:
            # DO NOT close vector_store.client
            self.graph_agent.graph_store.close()

            from codesense.cli import refresh_indexes
            from codesense.agents.semantic_search import SemanticSearchAgent
            from codesense.agents.dependency_graph import DependencyGraphAgent

            refresh_indexes(repo_path)
            self.search_agent = SemanticSearchAgent()
            self.graph_agent = DependencyGraphAgent()
            return "Semantic and dependency indexes refreshed from the edited working tree."
        except Exception as error:
            return f"Index refresh failed; search results may be stale: {error}"

    def _route_after_search(self, state: CodeSenseState) -> str:
        if state.get("_exact_symbol_lookup"):
            return "output_guardrail"
        if state.get("intent") == "refactor":
            return "refactor_node"
        return "explainer"

    def _build_graph(self):
        workflow = StateGraph(CodeSenseState)

        # Add nodes
        workflow.add_node("input_guardrail", self._validate_input)
        workflow.add_node("supervisor", self._classify_intent)
        workflow.add_node("semantic_search", self._run_semantic_search)
        workflow.add_node("dependency_graph", self._run_dependency_graph)
        workflow.add_node("explainer", self.explainer_agent.generate_explanation)
        workflow.add_node("refactor_node", self._refactor_node)
        workflow.add_node("output_guardrail", self._apply_output_guardrails)

        # Edges
        workflow.add_edge(START, "input_guardrail")
        workflow.add_conditional_edges(
            "input_guardrail",
            self._route_after_input,
            {"supervisor": "supervisor", END: END},
        )
        
        # Router from supervisor
        workflow.add_conditional_edges(
            "supervisor",
            self._route,
            {
                "semantic_search": "semantic_search",
                "dependency_graph": "dependency_graph",
                END: END
            }
        )
        
        # After search, route to explainer or refactor based on intent
        workflow.add_conditional_edges(
            "semantic_search",
            self._route_after_search,
            {
                "explainer": "explainer",
                "refactor_node": "refactor_node",
                "output_guardrail": "output_guardrail",
                END: END
            }
        )
        
        workflow.add_conditional_edges(
            "dependency_graph",
            self._route_after_dependency_graph,
            {"explainer": "explainer", "output_guardrail": "output_guardrail"},
        )
        
        # After refactor, we're done (UI shows approval)
        workflow.add_edge("refactor_node", "output_guardrail")
        
        # After explainer, we're done
        workflow.add_edge("explainer", "output_guardrail")
        workflow.add_edge("output_guardrail", END)

        return workflow.compile()

    def run(self, question: str, audit_session_id: str | None = None) -> dict:
        """Execute the LangGraph workflow for a given question and return the state dict."""
        try:
            question = validate_user_input(question)
        except GuardrailViolation as error:
            log_guardrail_denial(
                "query",
                question,
                str(error),
                user_id=self.user_id,
                session_id=audit_session_id,
            )
            return {"error": str(error)}

        if not os.getenv("GROQ_API_KEY"):
            return {"error": "Error: GROQ_API_KEY environment variable is missing. Please set it in your .env file."}
            
        initial_state = CodeSenseState(
            question=question,
            intent=None,
            _target_symbol=None,
            _dependency_direction=None,
            _exact_symbol_lookup=False,
            _source_dependency_analysis=False,
            _audit_user_id=str(self.user_id),
            _audit_session_id=audit_session_id,
            _repository_path=None,
            search_results=None,
            impact_results=None,
            final_answer=None,
            plan=None,
            error=None,
            requires_approval=False,
            diff_data=None
        )
        
        result_state = self.graph.invoke(initial_state)
        
        return result_state
