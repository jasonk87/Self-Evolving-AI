# ai_assistant/planning/hierarchical_planner.py
import re
from typing import List, Any, Optional # Added Optional
# Assuming a generic LLM service interface or a specific one like OllamaProvider
from ai_assistant.llm_interface.ollama_client import OllamaProvider
# For __main__ example, we'll mock this.

LLM_HP_OUTLINE_GENERATION_PROMPT_TEMPLATE = """
Given the user's goal: '{user_goal}'

Break this down into a list of 3-7 high-level functional components or development phases.
Each item in the list should be a concise title for that component or phase.
Return ONLY the list, with each item on a new line, preferably starting with a hyphen or asterisk.

Example for 'develop a command-line snake game':
- Game Core Logic
- Display and Rendering
- User Input Handling
- Game State Management (including scoring)
- Startup and End Game Flow
"""

LLM_HP_DETAILED_TASK_BREAKDOWN_PROMPT_TEMPLATE = """
User's overall goal: '{user_goal}'
Current high-level component to break down: '{outline_item}'
{project_context_section}
Break down the high-level component '{outline_item}' into a list of 3-7 specific, actionable sub-tasks required to implement it, considering the overall user goal.
These sub-tasks should be concrete steps.
Return ONLY the list of sub-task descriptions, each on a new line, preferably starting with a hyphen or asterisk.

Example for component 'Game Core Logic & Mechanics' (goal: 'develop a command-line snake game'):
- Define data structure for the snake (e.g., list of coordinates).
- Implement snake movement logic (up, down, left, right).
- Implement food generation and placement.
- Implement collision detection (with walls and self).
- Handle snake growth when it eats food.
"""

# Conceptual structure for ProjectPlanStep (not a strict TypedDict here, for clarity)
# ProjectPlanStep = {
# "step_id": str, # e.g., "1.1", "2.a.i"
# "description": str, # The detailed task description
# "type": str, # "python_script", "human_review_gate", "informational"
# "details": dict # Type-specific details
# }

LLM_HP_STEP_ELABORATION_PROMPT_TEMPLATE = """
User's overall goal: '{user_goal}'
Context: This is for the detailed task: '{detailed_task}'
{project_context_section}

Your task is to convert the above detailed task into a single, specific step for a project plan.
Determine the most appropriate step 'type' from the allowed types: "python_script", "human_review_gate", "informational".
Then, construct a JSON object for the 'details' field, specific to that type.

- If 'type' is "python_script":
  The 'details' JSON object MUST include:
    - "script_content_prompt": A detailed natural language prompt for another AI to write the Python code for '{detailed_task}'. This prompt should be comprehensive enough for a coding AI to understand the requirements, inputs, outputs, and any constraints.
    - "input_files": An optional list of anticipated input filenames (as strings) that the script might need. Default to an empty list if none are obvious.
    - "output_files_to_capture": An optional list of anticipated output filenames (as strings) that the script might produce and whose content should be captured. Default to an empty list if none are obvious.
    - "timeout_seconds": An optional integer for script execution timeout (e.g., 30 or 60). Default to 30 if not specified.

- If 'type' is "human_review_gate":
  The 'details' JSON object MUST include:
    - "prompt_to_user": A concise question or statement to present to a human for review or approval, relevant to '{detailed_task}'.

- If 'type' is "informational":
  The 'details' JSON object MUST include:
    - "message": A brief informational message related to '{detailed_task}' for logging or user display.

Respond ONLY with a single JSON object representing the step, structured as follows:
{{
  "type": "<chosen_type>",
  "details": {{ ... type-specific_fields ... }}
}}

Do NOT include any other text, explanations, or markdown formatting.
Ensure the output is a valid JSON object.

Example for detailed_task 'Define data structure for snake (list of coordinates)':
{{
  "type": "python_script",
  "details": {{
    "script_content_prompt": "Write Python code to define a class or data structure representing the snake. The snake should be represented as a list of (x, y) coordinate tuples. Include initialization for a starting position and length.",
    "input_files": [],
    "output_files_to_capture": ["snake_data_structure.py"],
    "timeout_seconds": 30
  }}
}}
"""

class HierarchicalPlanner:
    def __init__(self, llm_provider: OllamaProvider): # Assuming OllamaProvider for now
        """
        Initializes the HierarchicalPlanner.

        Args:
            llm_provider: An instance of an LLM provider (e.g., OllamaProvider).
        """
        self.llm_provider = llm_provider

    async def generate_high_level_outline(self, user_goal: str, project_context: Optional[str] = None) -> List[str]:
        """
        Generates a high-level outline (list of main functional blocks or phases)
        for a given user goal.

        Args:
            user_goal: The user's complex goal description.
            project_context: Optional. Existing project context to provide to the LLM.

        Returns:
            A list of strings, where each string is a high-level component/phase.
            Returns an empty list if generation fails or parsing yields no items.
        """
        if not user_goal:
            return []

        prompt = LLM_HP_OUTLINE_GENERATION_PROMPT_TEMPLATE.format(user_goal=user_goal)
        if project_context: # pragma: no cover
            prompt += f"\n\nExisting project context to consider:\n{project_context}"

        try:
            # Assuming invoke_ollama_model_async is the method to call for text completion
            # and it's available on the llm_provider instance.
            # Adjust model, temperature, max_tokens as needed for this task.
            # For outline generation, a slightly creative but focused model might be good.
            # Using a generic model from get_model_for_task or a specific one.
            from ai_assistant.config import get_model_for_task # Local import for this specific call
            model_name = get_model_for_task("hierarchical_planning_outline")


            response_text = await self.llm_provider.invoke_ollama_model_async(
                prompt,
                model_name=model_name,
                temperature=0.6, # Slightly higher for some creativity in breakdown
                max_tokens=500 # Should be enough for an outline
            )

            if not response_text or not response_text.strip():
                return []

            # Parse the LLM's response into a list of strings.
            # Handles common list formats (newline-separated, markdown lists).
            outline_items = []
            for line in response_text.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                # Remove common list markers like '-', '*', or '1.', '2.', etc.
                line = re.sub(r"^\s*[-*]\s*", "", line)
                line = re.sub(r"^\s*\d+\.\s*", "", line)
                if line: # Ensure line is not empty after stripping markers
                    outline_items.append(line)

            return outline_items

        except Exception as e: # pragma: no cover
            # Log the error in a real application
            print(f"Error during LLM call in HierarchicalPlanner (generate_high_level_outline): {e}")
            return []

    async def generate_detailed_tasks_for_outline_item(
        self,
        outline_item: str,
        user_goal: str,
        project_context: Optional[str] = None
    ) -> List[str]:
        """
        Generates a list of detailed, actionable sub-tasks for a given high-level outline item.

        Args:
            outline_item: The high-level component/phase to break down.
            user_goal: The original user goal for overall context.
            project_context: Optional. Existing project context.

        Returns:
            A list of strings, where each string is a detailed sub-task.
            Returns an empty list if generation fails or parsing yields no items.
        """
        if not outline_item or not user_goal:
            return []

        project_context_section = ""
        if project_context: # pragma: no cover
            project_context_section = f"\nExisting project context to consider:\n{project_context}"

        prompt = LLM_HP_DETAILED_TASK_BREAKDOWN_PROMPT_TEMPLATE.format(
            user_goal=user_goal,
            outline_item=outline_item,
            project_context_section=project_context_section
        )

        try:
            from ai_assistant.config import get_model_for_task # Local import
            model_name = get_model_for_task("hierarchical_planning_tasks")

            response_text = await self.llm_provider.invoke_ollama_model_async(
                prompt,
                model_name=model_name,
                temperature=0.5, # Slightly less creative for more direct task breakdown
                max_tokens=700 # Enough for a list of tasks
            )

            if not response_text or not response_text.strip():
                return []

            detailed_tasks = []
            for line in response_text.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                line = re.sub(r"^\s*[-*]\s*", "", line)
                line = re.sub(r"^\s*\d+\.\s*", "", line)
                if line:
                    detailed_tasks.append(line)

            return detailed_tasks

        except Exception as e: # pragma: no cover
            print(f"Error during LLM call in HierarchicalPlanner (generate_detailed_tasks): {e}")
            return []

    async def generate_project_plan_step_for_task(
        self,
        detailed_task: str,
        user_goal: str,
        project_context: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Elaborates a detailed task description into a structured project plan step
        (type and details dictionary) using an LLM.

        Args:
            detailed_task: The specific task description to elaborate.
            user_goal: The overall user goal for context.
            project_context: Optional existing project context.

        Returns:
            A dictionary with "type" and "details" keys if successful,
            otherwise None.
        """
        if not detailed_task or not user_goal:
            return None

        project_context_section = ""
        if project_context: # pragma: no cover
            project_context_section = f"\nExisting project context to consider:\n{project_context}"

        prompt = LLM_HP_STEP_ELABORATION_PROMPT_TEMPLATE.format(
            user_goal=user_goal,
            detailed_task=detailed_task,
            project_context_section=project_context_section
        )

        try:
            from ai_assistant.config import get_model_for_task # Local import
            model_name = get_model_for_task("hierarchical_planning_step_elaboration")

            response_text = await self.llm_provider.invoke_ollama_model_async(
                prompt,
                model_name=model_name,
                temperature=0.3, # More deterministic for JSON output
                max_tokens=1000 # Allow for detailed prompts within JSON
            )

            if not response_text or not response_text.strip():
                print(f"HierarchicalPlanner (generate_project_plan_step_for_task): LLM returned empty response for task '{detailed_task}'")
                return None

            # Attempt to parse the LLM response as JSON
            try:
                # The LLM might sometimes include markdown ```json ... ``` around the output
                cleaned_json_response = re.sub(r"^\s*```json\s*\n?", "", response_text.strip(), flags=re.IGNORECASE)
                cleaned_json_response = re.sub(r"\n?\s*```\s*$", "", cleaned_json_response, flags=re.IGNORECASE).strip()

                parsed_step_data = json.loads(cleaned_json_response)

                # Basic validation of the parsed structure
                if isinstance(parsed_step_data, dict) and \
                   "type" in parsed_step_data and \
                   "details" in parsed_step_data and \
                   isinstance(parsed_step_data["details"], dict):
                    return parsed_step_data
                else:
                    print(f"HierarchicalPlanner (generate_project_plan_step_for_task): Parsed JSON for task '{detailed_task}' has incorrect structure: {parsed_step_data}")
                    return None
            except json.JSONDecodeError as je:
                print(f"HierarchicalPlanner (generate_project_plan_step_for_task): Failed to parse LLM JSON response for task '{detailed_task}'. Error: {je}")
                print(f"LLM Response was: {response_text}")
                return None

        except Exception as e: # pragma: no cover
            print(f"Error during LLM call in HierarchicalPlanner (generate_project_plan_step_for_task) for task '{detailed_task}': {e}")
            return None


if __name__ == '__main__': # pragma: no cover
    import asyncio
    import json # For printing dicts nicely in main

    # Mock LLMProvider for the __main__ example
    class MockLLMProvider(OllamaProvider): # Inherit to satisfy type hint
        async def invoke_ollama_model_async(self, prompt: str, model_name: str, temperature: float = 0.7, max_tokens: int = 1500) -> str:
            print(f"\n--- MockLLMProvider received prompt for model {model_name} (Temp: {temperature}, MaxTokens: {max_tokens}) ---")
            print(prompt[:600] + "..." if len(prompt) > 600 else prompt) # Print preview if too long
            print("--- End of MockLLMProvider prompt ---")

            # Mock responses for outline generation
            if "snake game" in user_goal_from_prompt(prompt) and "component to break down" not in prompt and "convert the above detailed task" not in prompt:
                return """
                - Game Core Logic & Mechanics
                - Graphics & Display System (Command-line)
                - User Input Handling
                - Game State Management (Score, Levels, Game Over)
                """
            elif "blog platform" in user_goal_from_prompt(prompt) and "component to break down" not in prompt and "convert the above detailed task" not in prompt:
                return """
                * User Authentication
                * Post Creation and Management
                * Commenting System
                """
            # Mock responses for detailed task breakdown
            elif "Game Core Logic & Mechanics" in component_from_prompt(prompt) and "convert the above detailed task" not in prompt:
                return """
                - Define data structure for snake (list of coordinates)
                - Implement snake movement logic (up, down, left, right)
                - Implement food generation and placement logic
                - Implement collision detection (walls and self)
                """
            elif "User Authentication" in component_from_prompt(prompt) and "blog" in user_goal_from_prompt(prompt) and "convert the above detailed task" not in prompt:
                 return """
                 - Design user model (username, hashed_password, email)
                 - Implement user registration endpoint
                 - Implement user login endpoint (session/token based)
                 """
            # Mock responses for step elaboration
            elif "Define data structure for snake" in detailed_task_from_prompt(prompt):
                return json.dumps({
                    "type": "python_script",
                    "details": {
                        "script_content_prompt": "Create a Python file defining the Snake class. It should have attributes for body (list of tuples), direction, and methods to grow and change direction.",
                        "input_files": [],
                        "output_files_to_capture": ["snake_model.py"],
                        "timeout_seconds": 30
                    }
                })
            elif "Design user model" in detailed_task_from_prompt(prompt):
                 return json.dumps({
                    "type": "informational",
                    "details": {
                        "message": "User model design: username (string), email (string), password_hash (string). Consider using an ORM."
                    }
                 })
            elif "Implement user registration endpoint" in detailed_task_from_prompt(prompt):
                return json.dumps({
                    "type": "human_review_gate",
                    "details": {
                        "prompt_to_user": "Review the proposed API for user registration: POST /users with username, email, password. Does this meet requirements before proceeding with code generation?"
                    }
                })

            return "" # Default empty response

    # Helper to extract user_goal from prompt for mock (very basic)
    def user_goal_from_prompt(prompt_text: str) -> str:
        match = re.search(r"User's overall goal: '(.*?)'", prompt_text, re.IGNORECASE)
        if not match: # Fallback for outline prompt if overall goal not found in that specific format
            match_outline = re.search(r"goal: '(.*?)'", prompt_text, re.IGNORECASE)
            return match_outline.group(1) if match_outline else "Unknown Goal"
        return match.group(1)

    def component_from_prompt(prompt_text: str) -> str:
        match = re.search(r"Current high-level component to break down: '(.*?)'", prompt_text, re.IGNORECASE)
        return match.group(1) if match else "Unknown Component"

    def detailed_task_from_prompt(prompt_text: str) -> str:
        match = re.search(r"Context: This is for the detailed task: '(.*?)'", prompt_text, re.IGNORECASE)
        return match.group(1) if match else "Unknown Detailed Task"


    async def run_main_example():
        mock_provider = MockLLMProvider(base_url="http://localhost:11434")

        original_get_model = None
        try:
            import ai_assistant.config
            original_get_model = ai_assistant.config.get_model_for_task

            def mock_model_selector(task_type: str) -> str:
                if task_type == "hierarchical_planning_outline": return "mock_outline_model"
                elif task_type == "hierarchical_planning_tasks": return "mock_detailed_tasks_model"
                elif task_type == "hierarchical_planning_step_elaboration": return "mock_step_elaboration_model"
                return "mock_default_model" # pragma: no cover
            ai_assistant.config.get_model_for_task = mock_model_selector

            planner = HierarchicalPlanner(llm_provider=mock_provider)

            print("\n--- Testing HierarchicalPlanner: 'snake game' goal ---")
            snake_user_goal = "Develop a command-line snake game in Python, including scoring."

            print("\n1. Generating High-Level Outline...")
            snake_outline = await planner.generate_high_level_outline(user_goal=snake_user_goal)
            # ... (rest of outline printing) ...
            if not snake_outline: print("  No outline generated."); return
            for item in snake_outline: print(f"  - {item}")


            first_component = snake_outline[0]
            print(f"\n2. Generating Detailed Tasks for Component: '{first_component}'...")
            detailed_tasks = await planner.generate_detailed_tasks_for_outline_item(
                outline_item=first_component, user_goal=snake_user_goal
            )
            # ... (rest of detailed tasks printing) ...
            if not detailed_tasks: print("  No detailed tasks generated."); return
            for task_desc in detailed_tasks: print(f"  - {task_desc}")


            first_detailed_task = detailed_tasks[0]
            print(f"\n3. Elaborating Project Plan Step for Task: '{first_detailed_task}'...")
            plan_step_dict = await planner.generate_project_plan_step_for_task(
                detailed_task=first_detailed_task, user_goal=snake_user_goal
            )
            print(f"Generated Plan Step for '{first_detailed_task}':")
            if plan_step_dict: print(json.dumps(plan_step_dict, indent=2))
            else: print("  No plan step generated.")
            assert plan_step_dict is not None and plan_step_dict["type"] == "python_script"


            print("\n--- Testing HierarchicalPlanner: 'blog platform' goal ---")
            blog_user_goal = "Create a simple blog platform with user accounts, posts, and comments."
            # ... (similar flow for blog: outline -> detailed tasks -> step elaboration for one task)
            blog_outline = await planner.generate_high_level_outline(user_goal=blog_user_goal)
            if not blog_outline: print("  No blog outline generated."); return
            for item in blog_outline: print(f"  - {item}")

            first_blog_component = blog_outline[0] # e.g., "User Authentication"
            detailed_blog_tasks = await planner.generate_detailed_tasks_for_outline_item(
                outline_item=first_blog_component, user_goal=blog_user_goal
            )
            if not detailed_blog_tasks: print(f"  No detailed tasks for {first_blog_component}."); return
            for task_desc in detailed_blog_tasks: print(f"  - {task_desc}")

            # Test elaboration for both an informational and a human_review_gate type
            if len(detailed_blog_tasks) > 1:
                detailed_task_for_info = detailed_blog_tasks[0] # "Design user model"
                print(f"\n3a. Elaborating (Info) Step for Task: '{detailed_task_for_info}'...")
                info_step = await planner.generate_project_plan_step_for_task(detailed_task_for_info, blog_user_goal)
                if info_step: print(json.dumps(info_step, indent=2))
                else: print("  No info step generated.")
                assert info_step and info_step["type"] == "informational"

                detailed_task_for_review = detailed_blog_tasks[1] # "Implement user registration endpoint"
                print(f"\n3b. Elaborating (Review Gate) Step for Task: '{detailed_task_for_review}'...")
                review_step = await planner.generate_project_plan_step_for_task(detailed_task_for_review, blog_user_goal)
                if review_step: print(json.dumps(review_step, indent=2))
                else: print("  No review step generated.")
                assert review_step and review_step["type"] == "human_review_gate"


        finally:
            if original_get_model:
                 ai_assistant.config.get_model_for_task = original_get_model

    asyncio.run(run_main_example())
