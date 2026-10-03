import os

# Weebo 1.x tests mock the DeepSeek/Gemini clients. The app now defaults the legacy LLM layer to Codex
# (the user's ChatGPT plan), so pin the old provider here: tests must stay offline, free and deterministic.
os.environ.setdefault("LLM_PROVIDER", "deepseek")
