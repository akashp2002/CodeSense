import asyncio
from dotenv import load_dotenv
load_dotenv()
from codesense.agents.refactor import RefactorAgent
import sys
import os
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage
from langgraph.prebuilt import create_react_agent
from langchain_mcp_adapters.tools import load_mcp_tools

async def main():
    agent_wrapper = RefactorAgent()
    prompt = 'Rename clean_text function to preprocess_text'
    
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "codesense.mcp.server"],
        env=os.environ.copy(),
    )
    
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = await load_mcp_tools(session)
            print("AVAILABLE TOOLS:", [t.name for t in mcp_tools])
            
            # Using the exact same agent setup
            agent = create_react_agent(agent_wrapper.llm, tools=mcp_tools)
            
            system_prompt = (
                "You are an expert software engineer. You have tools to read files, "
                "apply patches, and get git diffs.\n\n"
                "INSTRUCTIONS — follow these steps IN ORDER, then STOP:\n"
                "1. Use `search_file` to find the exact file path containing the code, OR use `list_files` to find the file.\n"
                "2. Use `read_file` to read the file. The file_path argument should be a RELATIVE path from the repo root.\n"
                "3. Use `replace_lines` with the exact start and end line numbers and the replacement content.\n"
                "4. Use `get_git_diff` to get the diff of your changes.\n"
                "5. Return the diff as your final answer. DO NOT call any more tools after step 4.\n\n"
                "DO NOT use branch, commit, push, or PR tools. STOP after returning the diff."
            )
            
            print("--- STARTING AGENT EXECUTION ---")
            try:
                async for chunk in agent.astream(
                    {"messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ]},
                    stream_mode="values"
                ):
                    msg = chunk["messages"][-1]
                    print(f"[{msg.type.upper()}] {msg.content}")
                    if hasattr(msg, 'tool_calls') and msg.tool_calls:
                        for tc in msg.tool_calls:
                            print(f"  TOOL CALL: {tc['name']} -> {tc['args']}")
            except Exception as e:
                print("Execution Error:", e)

if __name__ == "__main__":
    asyncio.run(main())
