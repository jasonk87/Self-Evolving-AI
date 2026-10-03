// Same-app links work on reload without a server-side catch-all route.
export function proposalHref(id) {
  return `/#evolution/${encodeURIComponent(id)}`;
}

export function proposalFromHash(hash) {
  const match = /^#evolution\/(p_[a-zA-Z0-9_-]+)$/.exec(hash);
  return match ? match[1] : null;
}
