# DYNAMIC SPECIALIST: DiagramGenerator
# RETIREMENT: Retire after 5 successful diagram generations or if unused for 3 days.
# ROLLBACK: If issues arise with diagram generation, revert to text-based explanations and log the error for review. Ensure the base64 encoding and SVG rendering are correctly handled.

import base64

def generate_diagram(topic):
    # Placeholder for diagram generation logic. This would involve using libraries like matplotlib, graphviz, or mermaid.js.
    # For now, it will return a dummy SVG or a message indicating what it would do.
    if topic.lower() == 'machine learning':
        # Example: Generate a simple diagram for ML
        svg_content = '''
        <svg width="300" height="150" xmlns="http://www.w3.org/2000/svg">
          <rect width="300" height="150" fill="#f0f0f0"/>
          <text x="150" y="75" font-family="Arial" font-size="16" text-anchor="middle" fill="#333">ML Diagram Placeholder</text>
          <circle cx="50" cy="100" r="20" fill="blue"/>
          <circle cx="100" cy="100" r="20" fill="green"/>
          <line x1="70" y1="100" x2="90" y2="100" stroke="black" stroke-width="2"/>
        </svg>
        '''
        return f"Successfully generated a diagram for '{topic}'.\n<img src='data:image/svg+xml;base64,{base64.b64encode(svg_content.encode('utf-8')).decode('utf-8')}' alt='ML Diagram'>"
    elif topic.lower() == 'server setup':
        # Example: Generate a simple diagram for server setup
        svg_content = '''
        <svg width="300" height="150" xmlns="http://www.w3.org/2000/svg">
          <rect width="300" height="150" fill="#e0e0ff"/>
          <text x="150" y="30" font-family="Arial" font-size="16" text-anchor="middle" fill="#333">Server Setup Diagram</text>
          <rect x="50" y="60" width="80" height="60" fill="orange"/>
          <text x="90" y="95" font-family="Arial" font-size="12" text-anchor="middle" fill="black">Web Server</text>
          <rect x="170" y="60" width="80" height="60" fill="purple"/>
          <text x="210" y="95" font-family="Arial" font-size="12" text-anchor="middle" fill="white">Database</text>
          <line x1="130" y1="90" x2="170" y2="90" stroke="gray" stroke-width="3" marker-end="url(#arrow)">
            <defs>
              <marker id="arrow" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="gray" />
              </marker>
            </defs>
          </line>
        </svg>
        '''
        return f"Successfully generated a diagram for '{topic}'.\n<img src='data:image/svg+xml;base64,{base64.b64encode(svg_content.encode('utf-8')).decode('utf-8')}' alt='Server Setup Diagram'>"
    else:
        return "I can help draw diagrams for topics like 'machine learning' or 'server setup'. What specifically would you like to visualize?"

# Example usage (for testing the logic code before it's loaded as a tool):
# print(generate_diagram('machine learning'))
# print(generate_diagram('server setup'))
# print(generate_diagram('other topic'))