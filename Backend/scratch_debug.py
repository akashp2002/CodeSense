import asyncio
from dotenv import load_dotenv
from codesense.agents.refactor import RefactorAgent

async def main():
    load_dotenv()
    try:
        agent = RefactorAgent()
        print("Agent created, running refactor...")
        result = await agent.run(
            "Rename the function 'analyze_job_batch' to 'process_job_batch_analysis' in the codebase"
        )
        print(f"SUCCESS:\n{result}")
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
