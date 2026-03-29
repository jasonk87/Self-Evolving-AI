import re

with open("ai_assistant/core/orchestrator.py", "r") as f:
    content = f.read()

# I want to find the section right after tool execution to catch any 'base64_image' and emit it.
search = """                    # Append to history
                    step_record = f"Cycle {step_i+1}:\\nStrategist: {strategist_response}\\nOperator Action: {tool_name}\\nResult: {result_str[:1000]}\\n"
                    execution_history += step_record"""

replace = """                    # Process PiP Visuals
                    if isinstance(result, dict) and "base64_image" in result:
                        state.final_images.append(result.get("filename", "unknown.png"))
                        EventEmitter.emit("pip_update", {
                            "image_data": result["base64_image"],
                            "tool_name": tool_name
                        })

                    # Append to history
                    step_record = f"Cycle {step_i+1}:\\nStrategist: {strategist_response}\\nOperator Action: {tool_name}\\nResult: {result_str[:1000]}\\n"
                    execution_history += step_record"""

content = content.replace(search, replace)

with open("ai_assistant/core/orchestrator.py", "w") as f:
    f.write(content)
print("Added PiP intercept to orchestrator")
