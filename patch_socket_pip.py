import re

with open("socket_events.py", "r") as f:
    content = f.read()

# Add pip_update to EventEmitter forwards
search = """    EventEmitter.on('quarantine_update', lambda data: socketio.emit('quarantine_update', data))"""
replace = """    EventEmitter.on('quarantine_update', lambda data: socketio.emit('quarantine_update', data))
    EventEmitter.on('pip_update', lambda data: socketio.emit('pip_update', data))"""

content = content.replace(search, replace)

with open("socket_events.py", "w") as f:
    f.write(content)
print("Forwarded pip_update in socket_events")
