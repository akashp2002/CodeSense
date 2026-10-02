import sys
import os
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from codesense.llm_manager import get_llm
from langgraph.prebuilt import create_react_agent
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_mcp_adapters.tools import load_mcp_tools
import asyncio


class RefactorAgent:
    """
    Specialist agent that uses MCP tools to refactor code and create PRs.
    Always uses its own tool-calling model (not inherited from the Supervisor).
    """

    def __init__(self, model_name: str = "qwen/qwen3.8-27b"):
        self.llm = get_llm(purpose="coding", temperature=0, max_tokens=512)
        self.timeout_seconds = 120

    async def run(self, prompt: str, phase: str = "refactor") -> str:
        """
        Run the refactor agent in the specified phase using MCP tools.
        phase: "refactor" (applies changes and generates diff) or "push" (creates branch and PR)
        """
        server_params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "codesense.mcp.server"],
            env=os.environ.copy(),
        )

        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                all_tools = await load_mcp_tools(session)
                
                # Filter tools per phase to reduce token usage (Groq free-tier has small context)
                if phase == "refactor":
                    allowed = {"search_file", "rename_symbol", "get_git_diff", "read_file", "list_files"}
                    tools = [t for t in all_tools if t.name in allowed]
                    system_prompt = (
                        "You are an expert software engineer. You have tools to search files, "
                        "rename Python symbols with AST resolution, and get git diffs.\n\n"
                        "INSTRUCTIONS — follow these steps IN ORDER, then STOP:\n"
                        "1. Use `search_file` to locate the Python file and confirm the symbol.\n"
                        "2. Use `rename_symbol` with the relative file path, old identifier, and new identifier. "
                        "This resolves the definition and repository-wide imports/references with Tree-sitter and validates every changed file.\n"
                        "3. Use `get_git_diff` to verify your changes.\n"
                        "4. Return the diff as your final answer. DO NOT call any more tools.\n\n"
                        "CRITICAL RULES:\n"
                        "- Do NOT use `replace_in_file`, `replace_lines`, or unrestricted text replacement.\n"
                        "- Only rename valid Python identifiers through `rename_symbol`.\n"
                        "- Do NOT repeat a tool call with the same arguments. If you already searched, move on.\n"
                        "- Do NOT use branch, commit, push, or PR tools.\n"
                        "- STOP after returning the diff or an explanation."
                    )
                else:
                    allowed = {"create_branch", "commit_changes", "push_branch", "create_pull_request"}
                    tools = [t for t in all_tools if t.name in allowed]
                    system_prompt = (
                        "Create a PR. Steps: create_branch, commit_changes, push_branch, create_pull_request. "
                        "Return the PR URL. STOP."
                    )

                agent = create_react_agent(
                    self.llm,
                    tools,
                    prompt=system_prompt,
                )

                try:
                    result = await agent.ainvoke(
                        {"messages": [("user", prompt)]},
                        config={"recursion_limit": 15},
                    )
                    return result["messages"][-1].content
                except Exception as inner_e:
                    print(f"\n{'='*60}")
                    print(f"REFACTOR AGENT INNER ERROR:")
                    print(f"{'='*60}")
                    traceback.print_exc()
                    print(f"{'='*60}\n")
                    raise inner_e

    @staticmethod
    def _unwrap_exception_group(e: Exception) -> Exception:
        """Recursively unwrap ExceptionGroup to find the real root cause."""
        if isinstance(e, BaseExceptionGroup):
            for sub in e.exceptions:
                unwrapped = RefactorAgent._unwrap_exception_group(sub)
                if unwrapped is not sub or not isinstance(sub, BaseExceptionGroup):
                    return unwrapped
            return e.exceptions[0] if e.exceptions else e
        return e

    def run_sync(self, prompt: str, phase: str = "refactor") -> str:
        """Helper to run the async agent in a sync context."""
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(lambda: asyncio.run(self.run(prompt, phase)))
            return future.result(timeout=self.timeout_seconds)
        except FutureTimeoutError as e:
            future.cancel()
            raise TimeoutError(
                f"Refactor agent timed out after {self.timeout_seconds} seconds."
            ) from e
        except BaseExceptionGroup as eg:
            real_error = self._unwrap_exception_group(eg)
            print(f"\n{'='*60}")
            print(f"REFACTOR AGENT ROOT CAUSE:")
            print(f"  Type: {type(real_error).__name__}")
            print(f"  Message: {real_error}")
            print(f"{'='*60}\n")
            raise RuntimeError(f"{type(real_error).__name__}: {real_error}") from real_error
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
