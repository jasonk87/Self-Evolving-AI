
import sys
import os
import asyncio
import logging
from unittest.mock import MagicMock

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Adjust path
sys.path.append(os.path.abspath("."))

from ai_assistant.core import self_modification
from ai_assistant.custom_tools import knowledge_tools
from ai_assistant.learning.learning import LearningAgent

def test_get_function_source_code_fallback_real():
    print("\n--- Testing get_function_source_code Fallback (Real Broken Module) ---")
    print(f"CWD: {os.getcwd()}")
    
    module_path = "ai_assistant.custom_tools.broken_tool"
    function_name = "works_fine"
    
    # Create a broken tool file
    tool_path = os.path.join("ai_assistant", "custom_tools", "broken_tool.py")
    abs_tool_path = os.path.abspath(tool_path)
    print(f"Creating tool at: {abs_tool_path}")
    
    os.makedirs(os.path.dirname(tool_path), exist_ok=True)
    with open(tool_path, "w") as f:
        f.write("try:\n    import this_module_does_not_exist_xyz\nexcept ImportError:\n    raise ImportError('Simulated broken import')\n\n")
        f.write("def works_fine():\n    return 'I am resilient'\n")

    # Verify it exists
    if os.path.exists(tool_path):
            print("File confirmed to exist.")
    else:
            print("File creation FAILED.")

    try:
        # 2. Test get_function_source_code
        print("Calling get_function_source_code...")
        code = self_modification.get_function_source_code(module_path, function_name)
        
        print(f"Retrieved code: {code}")
        
        # Manually debug _resolve_file_path_robust if it failed
        if code is None:
            print("Code was None. Debugging internal function _resolve_file_path_robust...")
            resolved = self_modification._resolve_file_path_robust(module_path, function_name)
            print(f"_resolve_file_path_robust returned: {resolved}")
            
            # Check calculation manually
            relative = os.path.join(*module_path.split('.'))
            naive = os.path.join(os.getcwd(), relative)
            print(f"Naive path calc: {naive}")
            print(f"Naive exists as dir? {os.path.isdir(naive)}")
            print(f"Naive + .py exists? {os.path.exists(naive + '.py')}")
        
        if code and "def works_fine" in code:
            print("Fallback mechanism worked successfully on broken module!")
        else:
            print("FAILED: Code loop up failed.")
            sys.exit(1)

    finally:
        if os.path.exists(tool_path):
            os.remove(tool_path)

def test_fact_curation_prompt_update():
    print("\n--- Testing FACT_CURATION_PROMPT_TEMPLATE Update ---")
    prompt = knowledge_tools.FACT_CURATION_PROMPT_TEMPLATE
    if "Err on the side of KEEPING" in prompt:
        print("Prompt contains expected key phrases.")
    else:
        print("FAILED: Prompt missing key phrases.")
        sys.exit(1)

async def async_test_tool_deduction_blacklist():
    print("\n--- Testing Tool Deduction Blacklist ---")
    mock_tm = MagicMock()
    mock_nm = MagicMock()
    agent = LearningAgent(task_manager=mock_tm, notification_manager=mock_nm)
    
    mock_llm = MagicMock()
    async def mock_invoke(*args, **kwargs):
        return '{"tool_name": "PROPOSE_TOOL_MODIFICATION"}'
    
    agent.action_executor = MagicMock()
    agent.action_executor.code_service.llm_provider.invoke_ollama_model_async = mock_invoke
    
    description = "The action PROPOSE_TOOL_MODIFICATION failed."
    result = await agent._deduce_tool_from_description(description)
    
    print(f"Deduction result: {result}")
    if result is None:
        print("System Action Type was correctly rejected.")
    else:
        print(f"FAILED: Expected None, got {result}")
        sys.exit(1)

def main():
    test_get_function_source_code_fallback_real()
    test_fact_curation_prompt_update()
    asyncio.run(async_test_tool_deduction_blacklist())
    print("\nALL TESTS PASSED")

if __name__ == '__main__':
    main()
