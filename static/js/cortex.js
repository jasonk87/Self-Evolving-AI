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
                    contentHtml += `<div class="detail-item">
                        <div class="detail-label">Created</div>
                        <div class="detail-value">${new Date(info.timestamp).toLocaleString()}</div>
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
    }

    async function fetchData() {
        try {
            console.log("Visual Cortex: Fetching data...");
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

                newNodes.push({
                    id: insight.insight_id,
                    label: labelText,
                    title: insight.description ? insight.description.substring(0, 100) + "..." : "No description",
                    color: '#ffb347', // Orange/Gold
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
