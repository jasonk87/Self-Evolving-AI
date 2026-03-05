# System Evolution Roadmap

This document outlines planned enhancements, optimizations, and future capabilities for the AI Assistant Cockpit, prioritized based on system resilience, autonomy, and efficiency.

## 1. Enhancing the Quarantine System (Near-Term)
- **Persistent Quarantine State:** Persist `blocked_tools` to a JSON file or database to ensure tools remain quarantined across server restarts until genuinely fixed.
- **Auto-Recovery / Cooldowns:** Introduce a timer (e.g., 15 minutes) or exponential backoff to tentatively lift a tool from quarantine and try it again.
- **Deep Failure Context:** Store the exact arguments, goal, and task context that caused the failure. Display this in the UI modal for manual debugging.
- **Configurable Thresholds:** Allow the user to configure the failure threshold globally or per-tool via the UI settings.

## 2. Deepening Autonomous Integration (Mid-Term)
- **Proactive Self-Healing Triggers:** When a tool trips the circuit breaker, automatically generate an `EPHEMERAL_AGENT_TASK` for the `LearningAgent` to analyze the failure, fix the source code, and submit a "Ready to Merge" approval.
- **Strategist Fallback Routing:** Explicitly inject quarantined tools into the Phase 1 (Strategist) prompt: *"Tool X is QUARANTINED. DO NOT plan to use it. Find an alternative."* to prevent the AI from getting stuck.

## 3. General System Optimization & Architecture (Long-Term)
- **Token-Aware Context Management:** Implement a token counter to dynamically summarize or intelligently prune context (RAG facts, file contents, chat history) to optimize API costs and prevent context window overflows.
- **Sandboxed Execution:** Move code execution (especially for ephemeral agents) into isolated Docker containers to increase host security and allow safe AI experimentation.
- **Concurrent Agent Swarms:** Upgrade the `HierarchicalPlanner` to dispatch independent subtasks to multiple agents concurrently, speeding up complex project generation.
