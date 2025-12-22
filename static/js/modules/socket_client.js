
// static/js/modules/socket_client.js
// We assume 'io' is global from the script tag in index.html, or we could import it if using a bundler.
// For native ES modules without bundler, we might need to rely on the global 'io' or import from Cdn.
// Since 'io' comes from /socket.io/socket.io.js served by Flask-SocketIO, it exposes a global `io`.

export const socket = io();

// Event listeners will be registered by other modules or a central init.
// Or we can provide a helper to register them.
