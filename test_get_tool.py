import asyncio
from ai_assistant.tools.tool_system import tool_system_instance

async def run():
    res = await tool_system_instance.execute_tool("get_tool_schema", kwargs={"tool_name": "refresh_available_tools"})
    print("RES:")
    print(res)

asyncio.run(run())
