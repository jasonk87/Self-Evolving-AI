
import inspect
import asyncio
import sys
import os

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_assistant.custom_tools.knowledge_tools import learn_fact
from ai_assistant.tools.tool_system import tool_system_instance

async def verify_fixes():
    print("--- Verifying learn_fact Signature ---")
    sig = inspect.signature(learn_fact)
    params = list(sig.parameters.keys())
    print(f"learn_fact parameters: {params}")
    if 'fact' in params and 'fact_text' not in params:
        print("SUCCESS: learn_fact argument renamed to 'fact'.")
    else:
        print("FAILURE: learn_fact argument NOT renamed correctly.")

    print("\n--- Verifying ActionExecutor Injection Logic ---")
    
    # Define a dummy tool that requires action_executor
    def dummy_tool(action_executor):
        return f"Received: {action_executor}"

    # Register it manually for testing
    tool_system_instance.register_tool(
        tool_name="dummy_tool_for_injection",
        description="Test tool",
        module_path=__name__,
        function_name_in_module="dummy_tool",
        tool_type="dynamic",
        func_callable=dummy_tool
    )

    # Mock ActionExecutor
    mock_action_executor = "I_AM_ACTION_EXECUTOR"

    try:
        # Execute it
        result = await tool_system_instance.execute_tool(
            "dummy_tool_for_injection",
            action_executor=mock_action_executor
        )
        print(f"Result from dummy_tool: {result}")
        if result == "Received: I_AM_ACTION_EXECUTOR":
            print("SUCCESS: ActionExecutor injected correctly.")
        else:
            print("FAILURE: Injection failed or returned unexpected result.")
    except Exception as e:
        print(f"FAILURE: Execution raised exception: {e}")

if __name__ == "__main__":
    asyncio.run(verify_fixes())
