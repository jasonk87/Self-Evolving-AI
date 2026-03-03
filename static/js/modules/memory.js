
// static/js/modules/memory.js
import { showModal, showAlert } from './ui.js';

export async function loadMemory(factsContainer, episodesContainer, onChatCallback) {
    if (factsContainer) factsContainer.innerHTML = '<div class="loading">Loading facts...</div>';
    if (episodesContainer) episodesContainer.innerHTML = '<div class="loading">Loading episodes...</div>';

    try {
        const resFacts = await fetch('/api/memory/facts');
        const dataFacts = await resFacts.json();

        const resEpisodes = await fetch('/api/memory/episodes');
        const dataEpisodes = await resEpisodes.json();

        if (dataFacts.success && factsContainer) {
            factsContainer.innerHTML = '';
            if (dataFacts.facts.length === 0) {
                factsContainer.innerHTML = '<div style="padding:10px; color:#666;">No facts recorded.</div>';
            } else {
                dataFacts.facts.forEach(fact => {
                    const el = document.createElement('div');
                    el.className = 'memory-item';
                    el.innerHTML = `
                        <div class="memory-text">${fact.text}</div>
                        <div class="memory-meta">
                            <span>${new Date(fact.created_at).toLocaleDateString()}</span>
                            <span class="btn-delete-memory" data-id="${fact.fact_id}">🗑️</span>
                        </div>
                    `;
                    el.querySelector('.btn-delete-memory').addEventListener('click', async (e) => {
                        e.stopPropagation();
                        showModal(
                            "Forget Fact",
                            "Are you sure you want to delete this memory?",
                            async () => {
                                await fetch(`/api/memory/facts/${fact.fact_id}`, { method: 'DELETE' });
                                loadMemory(factsContainer, episodesContainer, onChatCallback);
                            },
                            true
                        );
                    });
                    factsContainer.appendChild(el);
                });
            }
        }

        if (episodesContainer) {
            if (dataEpisodes.success && dataEpisodes.episodes && dataEpisodes.episodes.length > 0) {
                episodesContainer.innerHTML = '';
                renderEpisodes(dataEpisodes.episodes, episodesContainer, onChatCallback);
            } else {
                episodesContainer.innerHTML = '<div style="padding:10px; color:#666;">No episodes summarized yet.</div>';
            }
        }

    } catch (e) {
        if (factsContainer) factsContainer.innerHTML = 'Memory Access Error';
    }
}

function renderEpisodes(episodes, container, onChatCallback) {
    episodes.forEach(ep => {
        const el = document.createElement('div');
        el.className = 'memory-item episode-card';
        const tags = ep.key_topics ? ep.key_topics.map(t => `<span class="topic-tag">${t}</span>`).join('') : '';

        el.innerHTML = `
            <div class="data-title" style="font-weight:bold; margin-bottom:5px;">${ep.title || 'Untitled Episode'}</div>
            <div class="data-desc" style="font-size:0.9em; margin-bottom:8px;">${ep.summary}</div>
            <div class="tags-container" style="display:flex; gap:5px; flex-wrap:wrap; margin-bottom:5px;">${tags}</div>
            <div class="data-meta" style="font-size:0.8em; color:#666; display:flex; justify-content:space-between; align-items:center;">
                <span>Session: ${ep.session_id ? ep.session_id.substring(0, 8) : 'Unknown'}</span>
                <button class="btn-xs view-session-btn" data-sid="${ep.session_id}">View Chat</button>
            </div>
        `;

        const viewBtn = el.querySelector('.view-session-btn');
        if (viewBtn) {
            viewBtn.addEventListener('click', () => {
                if (onChatCallback) onChatCallback(ep.session_id);
            });
        }
        container.appendChild(el);
    });
}
