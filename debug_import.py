
import sys
import os
sys.path.append(os.path.abspath("c:/Users/Jason/Desktop/Self Evolving AI"))

try:
    import ai_assistant.core.self_modification as sm
    print("Module loaded:", sm)
    print("Attributes:", dir(sm))
    print("Has get_function_source_code:", hasattr(sm, 'get_function_source_code'))
except Exception as e:
    print("Import failed:", e)
