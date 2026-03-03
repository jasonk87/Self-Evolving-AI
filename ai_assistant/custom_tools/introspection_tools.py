import os
import time
import logging
from ai_assistant.config import get_data_dir
from ai_assistant.core import background_service
from ai_assistant.core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)

def read_system_logs(lines: int = 50, log_file: str = "server.log") -> str:
    """
    Reads the last N lines of a system log file.
    
    Args:
        lines: Number of lines to read from the end. Default is 50.
        log_file: The name of the log file to read. Options: 'server.log', 'omnis.log', 'error_log.txt'. 
                  Defaults to 'server.log'.
                  
    Returns:
        A string containing the last N lines of the log file.
    """
    allowed_logs = ['server.log', 'omnis.log', 'error_log.txt', 'app_log.txt']
    if log_file not in allowed_logs:
        return f"Error: Invalid log file. Allowed options: {', '.join(allowed_logs)}"

    # Logs are typically in the root 'logs' directory, relative to project root
    # or sometimes in data dir depending on config. 
    # Based on file listing earlier: c:\Users\Jason\Desktop\Self Evolving AI\logs
    # We should dynamically find project root.
    try:
        # Assuming project root is 3 levels up from this file (ai_assistant/custom_tools/introspection_tools.py)
        # But safer to use config or relative path from known location.
        # let's try standard logs dir from project root first.
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        log_path = os.path.join(project_root, 'logs', log_file)
        
        if not os.path.exists(log_path):
            # Fallback to data dir if logs are there?
            # Creating a fallback just in case.
            log_path = os.path.join(get_data_dir(), log_file)
            
        if not os.path.exists(log_path):
             return f"Error: Log file '{log_file}' not found at {log_path}."

        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            # Efficient implementation for tailing large files is tricky in pure python without seeks.
            # But logs shouldn't be massive for this context. Reading all might be slow if 100MB.
            # Let's use a seek approach for safety.
            f.seek(0, os.SEEK_END)
            file_size = f.tell()
            
            block_size = 1024
            data = []
            lines_found = 0
            
            # Read backwards
            offset = file_size
            while offset > 0 and lines_found <= lines: # Read a bit more to ensure we get partial lines
                read_size = min(block_size, offset)
                offset -= read_size
                f.seek(offset)
                chunk = f.read(read_size)
                data.insert(0, chunk)
                lines_found += chunk.count('\n')
                
            content = "".join(data)
            split_lines = content.splitlines()
            return "\n".join(split_lines[-lines:])
            
    except Exception as e:
        return f"Error reading log file: {e}"

def get_background_task_status() -> str:
    """
    Returns the status of background AI services (Dreamer, Architect, Curation, etc.).
    Useful for knowing what the AI is doing "subconsciously".
    """
    try:
        status = background_service.get_service_status()
        
        is_active = status.get('is_active', False)
        active_str = "ACTIVE" if is_active else "INACTIVE"
        
        human_readable = f"Background Service Status: [{active_str}]\n"
        human_readable += "-" * 30 + "\n"
        
        # Helper to format timestamp
        def fmt_time(ts):
            if not ts: return "Never"
            return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))
            
        human_readable += f"• Autonomous Learning: {'ENABLED' if status.get('autonomous_learning_enabled') else 'DISABLED'}\n"
        human_readable += f"• Last Self-Reflection: {fmt_time(status.get('last_reflection_timestamp'))}\n"
        human_readable += f"• Last Fact Curation:   {fmt_time(status.get('last_fact_curation_timestamp'))}\n"
        human_readable += f"• Last Dream Cycle:     {fmt_time(status.get('last_dream_timestamp'))}\n"
        human_readable += f"• Last Architect Audit: {fmt_time(status.get('last_architect_audit_timestamp'))}\n"
        human_readable += f"• Last Visual Audit:    {fmt_time(status.get('last_visual_audit_timestamp'))}\n"
        
        return human_readable
    except Exception as e:
        return f"Error fetching background status: {e}"

def inspect_memory_stats() -> str:
    """
    Returns statistics about the AI's internal memory (facts, insights, episodes).
    Use this to understand what the AI 'knows'.
    """
    try:
        mm = MemoryManager()
        facts = mm.get_all_facts()
        insights = mm.get_all_insights()
        episodes = mm.get_all_episodes()
        
        stats = f"Memory Statistics:\n"
        stats += f"- Total Learned Facts: {len(facts)}\n"
        stats += f"- Total Insights: {len(insights)}\n"
        stats += f"- Total Episodes: {len(episodes)}\n\n"
        
        stats += "Recent Activity:\n"
        if facts:
            latest_fact = facts[-1]
            stats += f"• Latest Fact: {latest_fact.get('text', '')[:100]}...\n"
            
        if insights:
            # Filter for new insights
            new_insights = [i for i in insights if i.get('status') == 'NEW']
            stats += f"• Pending Insights (NEW): {len(new_insights)}\n"
            if new_insights:
                stats += f"  - Top Pending: {new_insights[0].get('description')[:100]}...\n"
                
        if episodes:
            latest_episode = episodes[-1]
            stats += f"• Latest Episode: '{latest_episode.get('title')}' (ID: {latest_episode.get('episode_id')})\n"
            
        return stats
        
    except Exception as e:
        logger.error(f"Error inspecting memory: {e}", exc_info=True)
        return f"Error inspecting memory: {e}"

def get_ai_source_map() -> str:
    """
    Returns a map of the AI's own source code structure.
    Use this to find relevant files when you want to read your own source code.
    """
    try:
        # We can dynamically walk the ai_assistant directory
        # But for now, let's provide a high-level guided map
        
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        core_dirs = {
            "ai_assistant/core": "Core logic (Orchestrator, BackgroundService, MemoryManager)",
            "ai_assistant/tools": "Tool definitions and registry",
            "ai_assistant/custom_tools": "Specific tool implementations (including this one)",
            "ai_assistant/llm_interface": "Connectors to LLMs (Gemini, Ollama)",
            "ai_assistant/memory": "Memory persistence and RAG",
            "ai_assistant/planning": "Planning agents",
            "web_app.py": "Main Flask application and API entry points"
        }
        
        output = "AI System Source Map:\n"
        output += f"Project Root: {project_root}\n\n"
        
        for rel_path, desc in core_dirs.items():
            full_path = os.path.join(project_root, rel_path)
            output += f"- {rel_path}: {desc}\n"
            if os.path.isdir(full_path):
                # List top level files in key dirs
                try:
                    files = [f for f in os.listdir(full_path) if f.endswith('.py') and not f.startswith('__')]
                    if files:
                        output += f"  Files: {', '.join(files[:10])}{'...' if len(files)>10 else ''}\n"
                except Exception:
                    pass
        
        output += "\nTo read the content of these files, use the 'read_file' tool with the full path (Project Root + relative path)."
        return output
    except Exception as e:
        return f"Error generating source map: {e}"
