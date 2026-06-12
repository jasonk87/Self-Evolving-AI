
import asyncio
import logging
import sys
import os

# Put project root in path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from web_app import app, init_orchestrator
from ai_assistant.core.task_manager import ActiveTaskType, ActiveTaskStatus

# Configure logging to see what's happening
logging.basicConfig(level=logging.ERROR)

async def run_verification():
    print("--- Starting Mission Control API Verification ---")
    
    # 1. Initialize Orchestrator (needs to be done to set the global variable)
    await init_orchestrator()
    
    # Access the global orchestrator from web_app (it was imported, but init_orchestrator writes to the module-level variable)
    # We need to make sure we are accessing the SAME orchestrator that the route uses.
    # Since we imported 'orchestrator' from web_app, it might be the initial None.
    # We should access it via web_app.orchestrator after init.
    import web_app
    if not web_app.orchestrator:
        print("Error: Orchestrator failed to initialize in web_app module.")
        return

    tm = web_app.orchestrator.task_manager
    
    # 2. Clear existing tasks for clean test
    tm.clear_all_tasks(clear_archive=True)
    
    # 3. Create a Dummy Task
    print("Creating dummy task...")
    dummy_task = tm.add_task(
        description="Verify Mission Control API",
        task_type=ActiveTaskType.MISC_CODE_GENERATION,
        details={"test": "data"}
    )
    
    # 4. Test the API
    print("Testing /api/tasks endpoint...")
    with app.test_client() as client:
        response = client.get('/api/tasks')
        
        if response.status_code != 200:
            print(f"FAILED: API returned status {response.status_code}")
            print(response.data)
            return

        data = response.get_json()
        if not data.get('success'):
            print(f"FAILED: API returned success=False. Error: {data.get('error')}")
            return
            
        tasks = data.get('tasks', [])
        print(f"API returned {len(tasks)} tasks.")
        
        # 5. Assertions
        assert len(tasks) == 1, f"Expected 1 task, got {len(tasks)}"
        task_data = tasks[0]
        assert task_data['task_id'] == dummy_task.task_id, "Task ID mismatch"
        assert task_data['description'] == "Verify Mission Control API", "Description mismatch"
        assert task_data['status'] == ActiveTaskStatus.INITIALIZING.name, "Status mismatch"
        
        print("SUCCESS: Task retrieved correctly via API.")

        print("Updating task status...")
        # Use a valid status from the Enum
        tm.update_task_status(dummy_task.task_id, ActiveTaskStatus.PLANNING) 
        
        # Now update to GENERATING_CODE
        tm.update_task_status(dummy_task.task_id, ActiveTaskStatus.GENERATING_CODE, progress=50)
        
        response = client.get('/api/tasks')
        data = response.get_json()
        task_data = data.get('tasks')[0]
        
        assert task_data['status'] == ActiveTaskStatus.GENERATING_CODE.name, "Updated status mismatch"
        assert task_data['progress_percentage'] == 50, "Progress mismatch"
        
        print("SUCCESS: Task status update reflected in API.")

    print("\n--- Verification Complete: PASSED ---")

if __name__ == "__main__":
    asyncio.run(run_verification())
