import asyncio
import logging
import sys
import os

# Ensure project root is in path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from ai_assistant.planning.planning import PlannerAgent
from ai_assistant.tools.tool_system import tool_system_instance

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def run_test():
    planner = PlannerAgent()
    available_tools = tool_system_instance.list_tools_with_sources()
    
    goal = "how are you doing?"
    print(f"DEBUG: Goal is '{goal}'")
    
    try:
        print("DEBUG: Calling create_plan_with_llm...")
        plan = await planner.create_plan_with_llm(
            goal_description=goal,
            available_tools=available_tools
        )
        print(f"DEBUG: Plan created successfully: {plan}")
        with open("status.txt", "w") as f:
             f.write(f"SUCCESS\nPlan: {plan}")
    except Exception as e:
        print(f"DEBUG: Planning failed with exception: {e}")
        with open("status.txt", "w") as f:
             f.write(f"FAILURE: {e}")
        logger.error("Planning failed", exc_info=True)



if __name__ == "__main__":
    asyncio.run(run_test())
