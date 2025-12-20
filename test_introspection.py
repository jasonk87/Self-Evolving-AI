import asyncio
import os
import sys

# Ensure project root is in path
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from ai_assistant.tools.tool_system import tool_system_instance, refresh_custom_tools
from ai_assistant.custom_tools.introspection_tools import (
    read_system_logs, 
    get_background_task_status, 
    inspect_memory_stats,
    get_ai_source_map
)

async def verify_introspection():
    print("--- Verifying Introspection Tools ---")
    
    # 1. Refresh Tools (simulating startup or reload)
    print("\n[1] Refreshing Tool Registry...")
    refresh_custom_tools()
    
    tools = tool_system_instance.list_tools()
    expected_tools = [
        'read_system_logs', 
        'get_background_task_status', 
        'inspect_memory_stats',
        'get_ai_source_map'
    ]
    
    missing = [t for t in expected_tools if t not in tools]
    if missing:
        print(f"❌ FAILED: Missing tools: {missing}")
    else:
        print("✅ SUCCESS: All introspection tools registered.")

    # 2. Test get_background_task_status
    print("\n[2] Testing get_background_task_status...")
    status = get_background_task_status()
    print(status)
    if "Background Service Status" in status:
        print("✅ SUCCESS: Status retrieved.")
    else:
        print("❌ FAILED: Invalid status output.")

    # 3. Test inspect_memory_stats
    print("\n[3] Testing inspect_memory_stats...")
    mem_stats = inspect_memory_stats()
    print(mem_stats)
    if "Memory Statistics" in mem_stats:
        print("✅ SUCCESS: Memory stats retrieved.")
    else:
        print("❌ FAILED: Invalid memory stats output.")

    # 4. Test read_system_logs
    print("\n[4] Testing read_system_logs...")
    logs = read_system_logs(lines=5)
    print(f"Logs (last 5 lines):\n{logs}")
    if "Error" not in logs: # It might be empty if no logs, but shouldn't error
         print("✅ SUCCESS: Logs read (or empty without error).")
    else:
         print(f"⚠️ NOTE: Log read returned potential error or empty: {logs}")

    # 5. Test get_ai_source_map
    print("\n[5] Testing get_ai_source_map...")
    source_map = get_ai_source_map()
    print(source_map)
    if "AI System Source Map" in source_map and "web_app.py" in source_map:
        print("✅ SUCCESS: Source map generated.")
    else:
        print("❌ FAILED: Invalid source map.")

if __name__ == "__main__":
    asyncio.run(verify_introspection())
