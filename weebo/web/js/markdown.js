// Markdown → sanitized HTML, with syntax highlighting, copyable code blocks and
// live "html-dynamic" widgets rendered in sandboxed iframes.
import { marked } from "../vendor/marked.esm.js";
import DOMPurify from "../vendor/purify.es.mjs";
import hljs from "../vendor/highlight.min.js";
import { api } from "./api.js";
import { escapeHtml } from "./ui.js";
import { proposalHref, proposalFromHash } from "./evolution-route.js";

let widgetCounter = 0;

marked.use({
  gfm: true,
  breaks: false,
  renderer: {
    code(token) {
      const lang = (token.lang || "").trim().split(/\s+/)[0].toLowerCase();
      if (lang === "html-dynamic") {
        return `<div class="widget-slot" data-widget-index="${widgetCounter++}"><div class="widget-loading">Building widget…</div></div>`;
      }
      const text = token.text || "";
      let html;
      try {
        if (lang && hljs.getLanguage(lang)) html = hljs.highlight(text, { language: lang, ignoreIllegals: true }).value;
        else if (text.length < 4000) html = hljs.highlightAuto(text).value;
        else html = escapeHtml(text);
      } catch {
        html = escapeHtml(text);
      }
      const label = escapeHtml(lang || "code");
      return `<div class="code"><div class="code-head"><span>${label}</span><button class="code-copy" type="button">Copy</button></div><pre><code class="hljs">${html}</code></pre></div>`;
    },
  },
});

const WEB_LINK = /^(https?:|mailto:|#)/i;

DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName !== "A" || !node.getAttribute("href")) return;
  const href = node.getAttribute("href");
  try {
    const url = new URL(href, location.href);
    const proposal = proposalFromHash(url.hash);
    if (url.origin === location.origin && url.pathname === "/" && proposal) {
      node.setAttribute("href", proposalHref(proposal));
      node.removeAttribute("target");
      return;
    }
  } catch { /* Other links follow the existing web/file rules. */ }
  if (WEB_LINK.test(href)) {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
    return;
  }
  // Codex links local files such as C:/projects/app.py. Browsers can't open those, so make them copyable path chips.
  let path = href.replace(/^file:\/+/i, "").replace(/^\/([A-Za-z]:)/, "$1");
  try { path = decodeURIComponent(path); } catch { /* keep as-is */ }
  path = path.split("#")[0];
  node.setAttribute("data-path", path);
  const rel = workspaceRelative(path);
  if (rel !== null) {
    // Inside Weebo's workspace: open it (served sandboxed by /files/).
    node.setAttribute("href", "/files/" + rel.split("/").map(encodeURIComponent).join("/"));
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
    node.setAttribute("class", "file-link openable");
    node.setAttribute("title", `Open ${rel}`);
    return;
  }
  node.removeAttribute("href");
  node.setAttribute("class", "file-link");
  node.setAttribute("title", "Click to copy path");
});

let workspaceRoot = "";

export function setWorkspaceRoot(path) {
  workspaceRoot = String(path || "").replace(/\\/g, "/").replace(/\/+$/, "");
}

function workspaceRelative(path) {
  if (!workspaceRoot) return null;
  const norm = String(path).replace(/\\/g, "/");
  const root = workspaceRoot.toLowerCase();
  if (!norm.toLowerCase().startsWith(root + "/")) return null;
  const rel = norm.slice(root.length + 1);
  return rel && !rel.split("/").includes("..") ? rel : null;
}

export function renderMarkdown(text) {
  widgetCounter = 0;
  const raw = marked.parse(text || "", { async: false });
  return DOMPurify.sanitize(raw, {
    ADD_ATTR: ["target", "data-widget-index", "data-path"],
    FORBID_TAGS: ["style", "iframe", "form", "input", "script"],
  });
}

// ---------------------------------------------------------------- widgets
const frames = new Map();

window.addEventListener("message", (event) => {
  const key = event.data && event.data.weeboWidget;
  if (!key) return;
  const frame = frames.get(key);
  if (frame && event.source === frame.contentWindow) {
    frame.style.height = `${Math.min(900, Math.max(40, Number(event.data.height) + 4))}px`;
  }
});

export async function hydrateWidgets(container, messageId) {
  const slots = container.querySelectorAll(".widget-slot:not([data-ready])");
  for (const slot of slots) {
    slot.dataset.ready = "1";
    const index = Number(slot.dataset.widgetIndex || 0);
    try {
      const { url } = await api.get(`/api/widget-url?id=${encodeURIComponent(messageId)}&index=${index}`);
      const frame = document.createElement("iframe");
      frame.className = "widget-frame";
      frame.setAttribute("sandbox", "allow-scripts");
      frame.setAttribute("referrerpolicy", "no-referrer");
      frame.setAttribute("loading", "lazy");
      frame.title = "Interactive widget";
      frame.src = url;
      frames.set(`${messageId}:${index}`, frame);
      slot.replaceChildren(frame);
    } catch (err) {
      slot.innerHTML = `<div class="widget-loading">Couldn't load widget: ${escapeHtml(err.message)}</div>`;
    }
  }
}

export function plainText(markdown) {
  return (markdown || "")
    .replace(/```[\s\S]*?```/g, " (code) ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/[#>*_~|-]{1,3}/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}
