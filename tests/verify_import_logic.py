
import ast
import logging
import os
import asyncio
import shutil
import sys
from ai_assistant.core.self_modification import upsert_import

# Configure logging to print to stdout
logging.basicConfig(level=logging.INFO, stream=sys.stdout)

async def test_upsert_logic():
    print("--- Starting Test ---")
    module_name = "test_upsert_dummy"
    test_file_name = f"{module_name}.py"
    cwd = os.getcwd() # project root
    
    # Create dummy file
    with open(test_file_name, "w") as f:
        f.write("import os, sys\n\ndef foo():\n    pass\n")
    
    try:
        # Test 1: Upsert 'import os' (Should exist)
        print("Test 1: Upserting 'import os'...")
        # Note: We pass module_name ("test_upsert_dummy") not file name
        res = await upsert_import(module_name, "import os", cwd, "test")
        print(f"Result 1: {res}")
        
        if "already exists" not in res:
             print("FAILURE: 'import os' was not detected as existing.")
        else:
             print("SUCCESS: 'import os' detected.")

        # Test 2: Upsert 'import time' (Should add)
        print("\nTest 2: Upserting 'import time'...")
        res = await upsert_import(module_name, "import time", cwd, "test")
        print(f"Result 2: {res}")
        
        if "upserted successfully" not in res:
             print("FAILURE: 'import time' failed to upsert.")
        else:
             print("SUCCESS: 'import time' upserted.")

        # Check content
        with open(test_file_name, "r") as f:
            content = f.read()
        print(f"File Content Now:\n{content}")

    finally:
        # Clean up
        if os.path.exists(test_file_name): os.remove(test_file_name)
        if os.path.exists(test_file_name + ".bak"): os.remove(test_file_name + ".bak")
        print("--- Test Complete ---")

async def main():
    await test_upsert_logic()

if __name__ == "__main__":
    asyncio.run(main())
