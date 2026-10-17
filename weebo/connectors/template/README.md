# Connector template

A thin [MCP](https://modelcontextprotocol.io) server that wraps one of your own programs (its command line or its
HTTP API) so Weebo's bots can use it, and so you can add the same server to ChatGPT as a custom connector.

1. Copy this folder and rename it. Point it at your program with environment variables, not code:
   `PROGRAM_CMD` (a command line) or `PROGRAM_URL` (an HTTP address). Nothing about your program is hardcoded.
2. Replace the example tools in `server.py` with your program's real actions. Keep each tool small and say in its
   docstring what it does.
3. Edit `manifest.json`: give every tool a risk label.
   - `read`: only looks things up.
   - `act`: changes something in your own program or files.
   - `spend`: can cost money.
   - `contact_people`: calls, texts or emails someone.
   Add a `test_call` to read-only tools so onboarding can try them.
4. In Weebo: Capabilities → Add a connector, paste the manifest. Weebo starts the server in isolation, lists its
   tools, checks they match the manifest and makes the read-only test calls.
5. Approve it on its card. Tools labelled `spend` or `contact_people` need a rule in your words first (for example
   "may call businesses to book appointments; never personal contacts").
6. Give it to a bot (Bots → the bot → ask Weebo to add the connector). That is a gated change: it waits for your yes.

Weebo watches every connector afterwards: failures are counted, one that keeps failing is flagged to the chief, and
one nobody uses is questioned. Run it over HTTP with `python server.py --http --port 8765`.
