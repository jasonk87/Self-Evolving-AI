// Cortex.js - Neural Network Visualization
document.addEventListener('DOMContentLoaded', () => {
    const container = document.getElementById('cortex-graph');
    if (!container) return;

    let network = null;
    let nodes = new vis.DataSet([]);
    let edges = new vis.DataSet([]);

    const options = {
        nodes: {
            shape: 'dot',
            size: 10,
            font: {
                size: 12,
                color: '#ffffff'
            },
            borderWidth: 2,
            shadow: true
        },
        edges: {
            width: 1,
            color: { inherit: 'from', opacity: 0.5 },
            smooth: { type: 'continuous' }
        },
        physics: {
            stabilization: false,
            barnesHut: {
                gravitationalConstant: -2000,
                springConstant: 0.04,
                springLength: 95
            },
            minVelocity: 0.75
        },
        interaction: {
            tooltipDelay: 200,
            hideEdgesOnDrag: true
        }
    };

    function initNetwork() {
        if (typeof vis === 'undefined') {
            container.innerHTML = `<div style="color: #ff4757; text-align: center; padding-top: 20px;">
                <h3>Library Error</h3>
                <p>Vis.js library failed to load.</p>
                <p>Check internet connection.</p>
            </div>`;
            return;
        }

        const data = { nodes: nodes, edges: edges };
        network = new vis.Network(container, data, options);

        network.on("click", function (params) {
            const panel = document.getElementById('cortex-details-panel');
            const panelTitle = document.getElementById('panel-title');
            const panelBody = document.getElementById('panel-body');
            const closeBtn = document.getElementById('close-panel-btn');

            if (!panel) return;

            // Wire close button once (or check if already wired)
            closeBtn.onclick = () => {
                panel.classList.remove('active');
            };

            if (params.nodes.length > 0) {
                const nodeId = params.nodes[0];
                const node = nodes.get(nodeId);
                const info = node._data; // Custom data property

                if (!info) return;

                // Format Title
                panelTitle.textContent = node.group === 'fact' ? "Learned Fact" : "Actionable Insight";

                // Format Body
                let contentHtml = '';

                // Common fields
                contentHtml += `<div class="detail-item">
                    <div class="detail-label">Type</div>
                    <div class="detail-value">${node.group.toUpperCase()}</div>
                </div>`;

                if (node.group === 'fact') {
                    contentHtml += `<div class="detail-item">
                        <div class="detail-label">Fact Content</div>
                        <div class="detail-value">${info.text}</div>
                    </div>`;
                    const dateStr = info.timestamp ? new Date(info.timestamp).toLocaleString() : 'Unknown';
                    contentHtml += `<div class="detail-item">
                        <div class="detail-label">Created</div>
                        <div class="detail-value">${dateStr}</div>
                    </div>`;
                } else if (node.group === 'insight') {
                    contentHtml += `<div class="detail-item">
                        <div class="detail-label">Category</div>
                        <div class="detail-value">${info.type}</div>
                    </div>`;
                    contentHtml += `<div class="detail-item">
                        <div class="detail-label">Description</div>
                        <div class="detail-value">${info.description}</div>
                    </div>`;

                    if (info.suggestion) {
                        contentHtml += `<div class="detail-item">
                            <div class="detail-label">Suggestion</div>
                            <div class="detail-value"><code>${info.suggestion}</code></div>
                        </div>`;
                    }
                }

                // Raw JSON for deep debug
                // contentHtml += `<details><summary style="color:#666;cursor:pointer;">Raw Data</summary><pre style="font-size:10px;color:#888;">${JSON.stringify(info, null, 2)}</pre></details>`;

                panelBody.innerHTML = contentHtml;
                panel.classList.add('active');

            } else {
                // Clicked on background: Close panel
                panel.classList.remove('active');
            }
        });

        // Live Thought Stream via Socket.IO
        if (typeof io !== 'undefined') {
            const cortexSocket = io();
            let rootNodeId = null;

            cortexSocket.on('thought_update', (data) => {
                const nodeId = data.node_id || `thought_${Date.now()}`;
                const parentId = data.parent_id;

                // Track root to connect new cycles if parent is missing
                if (!parentId && !rootNodeId) rootNodeId = nodeId;

                let nodeColor = '#3498db'; // Operator Blue
                if (data.role === 'Strategist') nodeColor = '#e74c3c'; // Strategist Red
                else if (data.role === 'Tool') nodeColor = '#9b59b6'; // Tool Purple
                else if (data.role === 'Critic') nodeColor = '#f1c40f'; // Critic Yellow

                const newNode = {
                    id: nodeId,
                    label: data.role || 'Thought',
                    title: data.thought ? data.thought.substring(0, 150) + "..." : "Processing...",
                    color: nodeColor,
                    group: 'live_thought',
                    _data: data
                };

                try {
                    // Update if exists, otherwise add
                    if (nodes.get(nodeId)) {
                        nodes.update(newNode);
                    } else {
                        nodes.add(newNode);
                        if (parentId && nodes.get(parentId)) {
                            edges.add({
                                id: `edge_${parentId}_${nodeId}`,
                                from: parentId,
                                to: nodeId,
                                arrows: 'to',
                                color: { color: '#ffffff', opacity: 0.4 }
                            });
                        }
                        // Center dynamically
                        network.fit({ animation: true });
                    }
                } catch (err) {
                    console.warn("Cortex Graph Update Error:", err);
                }
            });
        }
    }

    function renderActivitySummary(activity) {
        const metricsEl = document.getElementById('cortex-activity-metrics');
        const anomaliesEl = document.getElementById('cortex-activity-anomalies');
        const latestEl = document.getElementById('cortex-activity-latest');
        if (!metricsEl || !latestEl || !anomaliesEl || !activity) return;

        const totals = activity.totals || {};
        const totalRecent = activity.total_recent_changes || 0;

        metricsEl.innerHTML = `
            <div class="summary-metric">Facts <strong>${totals.facts || 0}</strong></div>
            <div class="summary-metric">Insights <strong>${totals.insights || 0}</strong></div>
            <div class="summary-metric">Episodes <strong>${totals.episodes || 0}</strong></div>
            <div class="summary-metric">Changes <strong>${totalRecent}</strong></div>
        `;

        const anomalies = Array.isArray(activity.anomalies) ? activity.anomalies : [];
        if (anomalies.length > 0) {
            anomaliesEl.innerHTML = anomalies
                .map(item => `<span class="summary-anomaly">${item.message || 'Memory anomaly detected'}</span>`)
                .join(' ');
        } else {
            anomaliesEl.innerHTML = '';
        }

        const latest = Array.isArray(activity.latest_changes) ? activity.latest_changes[0] : null;
        if (!latest) {
            latestEl.textContent = 'No recent memory changes in this window.';
            return;
        }

        const label = latest.label || latest.id || 'Unknown item';
        latestEl.textContent = `Latest ${latest.kind || 'memory'} ${latest.change_type || 'change'}: ${label}`;
    }

    async function fetchActivitySummary() {
        try {
            const hoursFilter = document.getElementById('cortex-hours-filter');
            const kindsFilter = document.getElementById('cortex-kind-filter');

            const params = new URLSearchParams({
                hours: hoursFilter ? hoursFilter.value : '24',
                kinds: kindsFilter ? kindsFilter.value : 'facts,insights,episodes',
            });

            const res = await fetch(`/api/memory/cortex-activity?${params.toString()}`);
            if (!res.ok) throw new Error(`Activity API Error: ${res.status}`);
            const payload = await res.json();
            if (payload.success && payload.activity) {
                renderActivitySummary(payload.activity);
            }
        } catch (e) {
            console.warn('Failed to load cortex activity summary', e);
        }
    }

    async function fetchData() {
        try {
            console.log("Visual Cortex: Fetching data...");
            fetchActivitySummary();
            const res = await fetch('/api/memory/all');
            if (!res.ok) throw new Error(`API Error: ${res.status}`);

            const data = await res.json();

            // Check if data is empty
            if (data.data && (!data.data.facts || data.data.facts.length === 0) && (!data.data.insights || data.data.insights.length === 0)) {
                container.innerHTML = `<div style="color: #aaa; text-align: center; padding-top: 20px;">
                    <h3>Empty Mind</h3>
                    <p>No memories or insights found.</p>
                    <p>Chat with the AI to generate some!</p>
                </div>`;
                return;
            }

            if (data.success && data.data) {
                updateGraph(data.data);
            }
        } catch (e) {
            console.error("Failed to load cortex data", e);
            container.innerHTML = `<div style="color: #ff4757; text-align: center; padding-top: 20px;">
                <h3>Visual Cortex Error</h3>
                <p>${e.message}</p>
                <p>Check console for details.</p>
            </div>`;
        }
    }

    function updateGraph(data) {
        const newNodes = [];
        const newEdges = [];

        // Map Facts to Nodes
        if (data.facts && Array.isArray(data.facts)) {
            data.facts.forEach(fact => {
                newNodes.push({
                    id: fact.fact_id,
                    label: fact.text ? (fact.text.length > 25 ? fact.text.substring(0, 25) + '...' : fact.text) : "Fact",
                    title: "Click for details",
                    color: '#97c2fc', // Blue
                    group: 'fact',
                    _data: fact
                });
            });
        }

        // Map Insights to Nodes
        if (data.insights && Array.isArray(data.insights)) {
            data.insights.forEach(insight => {
                // Better Label Logic
                let labelText = "Insight";
                if (insight.type) {
                    // Convert TOOL_BUG_SUSPECTED -> Tool Bug Suspected
                    labelText = insight.type.replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, l => l.toUpperCase());
                }

                // Status-based coloring
                let nodeColor = '#ffb347'; // Default Orange (Pending)
                if (insight.status === 'APPROVED_BY_USER' || insight.status === 'COMPLETED') {
                    nodeColor = '#2ecc71'; // Green (Done)
                } else if (insight.status === 'REJECTED_BY_USER' || insight.status === 'DISMISSED') {
                    nodeColor = '#95a5a6'; // Grey (Inactive)
                }

                newNodes.push({
                    id: insight.insight_id,
                    label: labelText,
                    title: `[${insight.status}] ${insight.description ? insight.description.substring(0, 100) + "..." : "No description"}`,
                    color: nodeColor,
                    group: 'insight',
                    _data: insight
                });
            });
        }

        nodes.clear();
        edges.clear();
        nodes.add(newNodes);
        edges.add(newEdges);

        if (!network) initNetwork();
        else network.fit();
    }

    const hoursFilter = document.getElementById('cortex-hours-filter');
    const kindFilter = document.getElementById('cortex-kind-filter');
    if (hoursFilter) {
        hoursFilter.addEventListener('change', fetchActivitySummary);
    }
    if (kindFilter) {
        kindFilter.addEventListener('change', fetchActivitySummary);
    }

    // Refresh button
    const refreshBtn = document.getElementById('refresh-cortex-btn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', fetchData);
    }

    // Auto load
    // Auto load when tab opens
    const cortexTab = document.querySelector('[data-target="view-cortex"]');
    if (cortexTab) {
        cortexTab.addEventListener('click', () => {
            // Wait for layout to update (display: none -> flex)
            setTimeout(() => {
                console.log("Visual Cortex: Tab active check.");
                const container = document.getElementById('cortex-graph');
                if (container) {
                    console.log(`Visual Cortex: Container size: ${container.offsetWidth}x${container.offsetHeight}`);
                }

                if (!network) {
                    fetchData();
                } else {
                    network.fit();
                }
            }, 300);
        });
    }

    // Initial load check?
    // No, wait for user interaction to save resources, or check visibility.
    const observer = new MutationObserver((mutations) => {
        const target = document.getElementById('view-cortex');
        if (target && target.classList.contains('active')) {
            if (!network) fetchData();
            else network.fit();
        }
    });

    const cortexView = document.getElementById('view-cortex');
    if (cortexView) {
        observer.observe(cortexView, { attributes: true, attributeFilter: ['class', 'style'] });
    }
});
