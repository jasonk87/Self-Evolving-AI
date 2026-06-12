import sys
print(f"Python: {sys.version}")

print("Importing chromadb...")
try:
    print("chromadb imported successfully")
except Exception as e:
    print(f"chromadb failed: {e}")

print("Importing tokenizers...")
try:
    print("tokenizers imported successfully")
except Exception as e:
    print(f"tokenizers failed: {e}")

print("Importing tiktoken...")
try:
    print("tiktoken imported successfully")
except Exception as e:
    print(f"tiktoken failed: {e}")

print("Importing playwright...")
try:
    print("playwright imported successfully")
except Exception as e:
    print(f"playwright failed: {e}")
