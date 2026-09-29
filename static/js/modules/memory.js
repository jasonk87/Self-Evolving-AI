// static/js/modules/memory.js
import { escapeHtml, showModal } from './ui.js';

const MEMORY_PAGE_SIZE = 50;
let currentFacts = [];
let currentEpisodes = [];
let currentChatCallback = null;

function sortMemory(items, sortOrder) {
    return [...items].sort((a, b) => {
        const aDate = new Date(a.created_at || a.timestamp || 0).getTime();
        const bDate = new Date(b.created_at || b.timestamp || 0).getTime();
        return sortOrder === 'oldest' ? aDate - bDate : bDate - aDate;
    });
}

function matchesQuery(item, query) {
    if (!query) return true;
    const haystack = [item.text, item.title, item.summary, item.description, ...(item.key_topics || [])]
        .filter(Boolean).join(' ').toLowerCase();
    return haystack.includes(query.toLowerCase());
}

export async function loadMemory(factsContainer, episodesContainer, onChatCallback) {
    if (factsContainer) factsContainer.innerHTML = '<div class="loading">Loading facts...</div>';
    if (episodesContainer) episodesContainer.innerHTML = '<div class="loading">Loading episodes...</div>';

    try {
        const resFacts = await fetch('/api/memory/facts');
        const dataFacts = await resFacts.json();

        const resEpisodes = await fetch('/api/memory/episodes');
        const dataEpisodes = await resEpisodes.json();

        currentFacts = dataFacts.success && Array.isArray(dataFacts.facts) ? dataFacts.facts : [];
        currentEpisodes = dataEpisodes.success && Array.isArray(dataEpisodes.episodes) ? dataEpisodes.episodes : [];
        currentChatCallback = onChatCallback;

        if (dataFacts.success && factsContainer) {
            if (dataFacts.facts.length === 0) {
                factsContainer.innerHTML = '<div class="memory-empty-state"><strong>No facts recorded.</strong><span>Use the plus button to add a durable fact.</span></div>';
            } else {
                renderFacts(dataFacts.facts, factsContainer, onChatCallback, MEMORY_PAGE_SIZE);
            }
        }

        if (episodesContainer) {
            if (dataEpisodes.success && dataEpisodes.episodes && dataEpisodes.episodes.length > 0) {
                renderEpisodes(dataEpisodes.episodes, episodesContainer, onChatCallback, MEMORY_PAGE_SIZE);
            } else {
                episodesContainer.innerHTML = '<div class="memory-empty-state"><strong>No episodes summarized yet.</strong><span>Completed conversations will appear here.</span></div>';
            }
        }
    } catch (e) {
        if (factsContainer) factsContainer.innerHTML = 'Memory Access Error';
        if (episodesContainer) episodesContainer.innerHTML = 'Memory Access Error';
    }
}

export function filterMemory(query = '', sortOrder = 'newest') {
    const normalizedQuery = query.trim();
    const factsContainer = document.getElementById('memory-list');
    const episodesContainer = document.getElementById('episodes-list');
    const filteredFacts = sortMemory(currentFacts.filter(item => matchesQuery(item, normalizedQuery)), sortOrder);
    const filteredEpisodes = sortMemory(currentEpisodes.filter(item => matchesQuery(item, normalizedQuery)), sortOrder);

    if (factsContainer) {
        if (!filteredFacts.length) {
            factsContainer.innerHTML = `<div class="memory-empty-state"><strong>No matching facts.</strong><span>Try a different search.</span></div>`;
        } else {
            renderFacts(filteredFacts, factsContainer, currentChatCallback, MEMORY_PAGE_SIZE);
        }
    }
    if (episodesContainer) {
        if (!filteredEpisodes.length) {
            episodesContainer.innerHTML = `<div class="memory-empty-state"><strong>No matching episodes.</strong><span>Try a different search.</span></div>`;
        } else {
            renderEpisodes(filteredEpisodes, episodesContainer, currentChatCallback, MEMORY_PAGE_SIZE);
        }
    }
}

function appendLoadMore(container, items, visibleCount, renderPage) {
    if (visibleCount >= items.length) return;

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn-xs memory-load-more';
    button.textContent = `Load more (${items.length - visibleCount} remaining)`;
    button.addEventListener('click', () => {
        renderPage(Math.min(items.length, visibleCount + MEMORY_PAGE_SIZE));
    });
    container.appendChild(button);
}

function renderFacts(facts, container, onChatCallback, visibleCount) {
    const visibleFacts = facts.slice(0, visibleCount);
    container.innerHTML = '';

    visibleFacts.forEach(fact => {
        const el = document.createElement('div');
        el.className = 'memory-item';
        el.innerHTML = `
            <div class="memory-text">${escapeHtml(fact.text)}</div>
            <div class="memory-meta">
                <span>${escapeHtml(new Date(fact.created_at).toLocaleDateString())}</span>
                <button type="button" class="btn-delete-memory" data-id="${escapeHtml(fact.fact_id)}" aria-label="Delete memory">🗑️</button>
            </div>
        `;
        el.querySelector('.btn-delete-memory').addEventListener('click', async (e) => {
            e.stopPropagation();
            showModal(
                'Forget Fact',
                'Are you sure you want to delete this memory?',
                async () => {
                    await fetch(`/api/memory/facts/${encodeURIComponent(fact.fact_id)}`, { method: 'DELETE' });
                    loadMemory(container, document.getElementById('episodes-list'), onChatCallback);
                },
                true
            );
        });
        container.appendChild(el);
    });

    appendLoadMore(container, facts, visibleFacts.length, nextCount => {
        renderFacts(facts, container, onChatCallback, nextCount);
    });
}

function renderEpisodes(episodes, container, onChatCallback, visibleCount) {
    const visibleEpisodes = episodes.slice(0, visibleCount);
    container.innerHTML = '';

    visibleEpisodes.forEach(ep => {
        const el = document.createElement('div');
        el.className = 'memory-item episode-card';
        const tags = ep.key_topics
            ? ep.key_topics.map(t => `<span class="topic-tag">${escapeHtml(t)}</span>`).join('')
            : '';

        el.innerHTML = `
            <div class="data-title" style="font-weight:bold; margin-bottom:5px;">${escapeHtml(ep.title || 'Untitled Episode')}</div>
            <div class="data-desc" style="font-size:0.9em; margin-bottom:8px;">${escapeHtml(ep.summary)}</div>
            <div class="tags-container" style="display:flex; gap:5px; flex-wrap:wrap; margin-bottom:5px;">${tags}</div>
            <div class="data-meta" style="font-size:0.8em; color:#666; display:flex; justify-content:space-between; align-items:center;">
                <span>Session: ${escapeHtml(ep.session_id ? ep.session_id.substring(0, 8) : 'Unknown')}</span>
                <button class="btn-xs view-session-btn" data-sid="${escapeHtml(ep.session_id)}">View Chat</button>
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

    appendLoadMore(container, episodes, visibleEpisodes.length, nextCount => {
        renderEpisodes(episodes, container, onChatCallback, nextCount);
    });
}
