/**
 * Council.js - Real-time Agent Swarm Visualization
 * Visualizes the AI's internal "Council" of agents using Vis.js.
 */

document.addEventListener('DOMContentLoaded', () => {
    const container = document.getElementById('council-visualizer');
    const sidebarView = document.getElementById('view-sidebar-council');

    if (!container) return;

    // --- Configuration ---
    // Compact layout for the sidebar (Width ~300px)
    const AGENT_NODES = [
        { id: 'user', label: 'USER', group: 'trigger', x: 0, y: -100 },
        { id: 'orchestrator', label: 'ORCHESTRATOR', group: 'hub', x: 0, y: 0 },
        { id: 'planner', label: 'PLANNER', group: 'agent', x: -80, y: 60 },
        { id: 'executor', label: 'EXECUTOR', group: 'agent', x: 80, y: 60 },
        { id: 'action_executor', label: 'ACTION', group: 'agent', x: 80, y: 0 },
        { id: 'learning', label: 'LEARNING', group: 'agent', x: -80, y: 0 },
        { id: 'memory', label: 'MEMORY', group: 'storage', x: 0, y: 120 }
    ];

    const EDGES = [
        { from: 'user', to: 'orchestrator' },
        { from: 'orchestrator', to: 'planner' },
        { from: 'orchestrator', to: 'executor' },
        { from: 'orchestrator', to: 'action_executor' },
        { from: 'orchestrator', to: 'learning' },
        { from: 'orchestrator', to: 'memory' },
        { from: 'planner', to: 'executor', style: 'dash-line' }, // Planning -> Execution flow
        { from: 'learning', to: 'memory' }
    ];

    // --- State ---
    let network = null;
    let nodes = new vis.DataSet(AGENT_NODES);
    let edges = new vis.DataSet(EDGES);
    let isInitialized = false;

    // --- Options ---
    const options = {
        nodes: {
            shape: 'dot',
            size: 15,
            font: {
                size: 10,
                color: '#8b949e',
                face: 'Inter',
                vadjust: -25 // Push label above/below
            },
            borderWidth: 2,
            shadow: {
                enabled: true,
                color: 'rgba(0,0,0,0.5)',
                size: 10,
                x: 0,
                y: 0
            },
            color: {
                background: '#0d1117',
                border: '#30363d',
                highlight: { background: '#0d1117', border: '#58a6ff' }
            }
        },
        edges: {
            width: 1,
            color: { color: '#30363d', opacity: 0.3 },
            smooth: { type: 'continuous' },
            arrows: { to: { enabled: false } }
        },
        physics: {
            enabled: false // Fixed layout
        },
        interaction: {
            dragNodes: false,
            zoomView: false,
            dragView: false,
            selectable: false,
            hover: true
        }
    };

    // --- Initialization ---
    function initCouncil() {
        if (typeof vis === 'undefined') {
            console.error("Vis.js not loaded.");
            return;
        }

        // Only init if visible on screen to get correct dimensions
        if (container.offsetWidth === 0 || container.offsetHeight === 0) {
            console.log("Council: Container hidden, delaying init.");
            return;
        }

        console.log("Council: Initializing Network...");
        const data = { nodes: nodes, edges: edges };
        network = new vis.Network(container, data, options);

        // Initial fit
        network.fit();
        isInitialized = true;
    }

    // --- Observer for Visibility ---
    // Because the sidebar uses 'display: none', we need to watch for class changes
    const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
            if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
                if (!sidebarView.classList.contains('hidden') && sidebarView.offsetHeight > 0) {
                    // Slight delay to allow CSS transitions to finish layout
                    setTimeout(() => {
                        if (!isInitialized) {
                            initCouncil();
                        } else if (network) {
                            network.fit(); // Re-center if already init
                        }
                    }, 100);
                }
            }
        });
    });

    if (sidebarView) {
        observer.observe(sidebarView, { attributes: true });
    }

    // --- Log Analysis & Animation ---

    // Map logger names/sources to Node IDs
    const SOURCE_MAP = {
        'DynamicOrchestrator': 'orchestrator',
        'PlannerAgent': 'planner',
        'ExecutionAgent': 'executor',
        'ActionExecutor': 'action_executor',
        'LearningAgent': 'learning',
        'MemoryManager': 'memory',
        'TaskManager': 'orchestrator', // TaskManager is close to Orchestrator
        'user': 'user'
    };

    function identifyNode(logData) {
        // Try precise match via metadata if available (not standard yet in all logs)
        if (logData.logger) {
            // Check if logger name contains key
            for (const [key, nodeId] of Object.entries(SOURCE_MAP)) {
                if (logData.logger.includes(key) || logData.message.includes(key)) {
                    return nodeId;
                }
            }
        }
        return 'orchestrator'; // Default fallback
    }

    function flashNode(nodeId, type = 'info') {
        if (!nodeId || !nodes) return;

        const originalColor = { background: '#0d1117', border: '#30363d' };
        let flashColor = '#58a6ff'; // Blue (Info)

        if (type === 'ERROR') flashColor = '#ff4757'; // Red
        if (type === 'WARNING') flashColor = '#ffa502'; // Orange

        // Specific Node Colors
        if (nodeId === 'planner') flashColor = '#c54aff'; // Purple
        if (nodeId === 'executor') flashColor = '#00e5ff'; // Cyan
        if (nodeId === 'learning') flashColor = '#2ecc71'; // Green
        if (nodeId === 'action_executor') flashColor = '#e74c3c'; // Red/Orange

        // Update Node
        try {
            nodes.update({
                id: nodeId,
                color: {
                    background: flashColor,
                    border: flashColor
                },
                size: 20, // Pulse size
                font: { color: '#ffffff' }
            });

            // Revert after delay
            setTimeout(() => {
                nodes.update({
                    id: nodeId,
                    color: originalColor,
                    size: 15,
                    font: { color: '#8b949e' }
                });
            }, 400);

            // Animate Edges connected to this node (Pulse effect)
            if (nodeId !== 'orchestrator') {
                const connectedEdges = edges.get({
                    filter: function (item) {
                        return (item.from === 'orchestrator' && item.to === nodeId) ||
                            (item.from === nodeId && item.to === 'orchestrator');
                    }
                });

                connectedEdges.forEach(edge => {
                    edges.update({
                        id: edge.id,
                        color: { color: flashColor, opacity: 0.8 },
                        width: 2
                    });
                    setTimeout(() => {
                        edges.update({
                            id: edge.id,
                            color: options.edges.color, // Reset to default
                            width: 1
                        });
                    }, 400);
                });
            }
        } catch (e) {
            console.warn("Council animation error:", e);
        }
    }

    // --- Socket Listeners ---
    if (typeof socket !== 'undefined') {
        socket.on('log_event', (data) => {
            if (isInitialized) { // Only animate if visible/init
                const nodeId = identifyNode(data);
                flashNode(nodeId, data.level);
            }
        });

        // Also listen for explicit chat flow
        socket.on('chat_response', () => {
            if (isInitialized) {
                flashNode('orchestrator', 'info');
                setTimeout(() => flashNode('user', 'info'), 300);
            }
        });
    }

    // --- Init Check ---
    // If the tab is ALREADY open on load (e.g. reload), init immediately
    if (sidebarView && !sidebarView.classList.contains('hidden')) {
        setTimeout(initCouncil, 100);
    }

    // Expose for debugging
    window.CouncilVis = { network, nodes, edges, flashNode };
});
