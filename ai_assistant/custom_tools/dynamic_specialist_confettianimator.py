# DYNAMIC SPECIALIST: ConfettiAnimator
# RETIREMENT: OnCompletion
# ROLLBACK: If the ConfettiAnimator fails to generate or display the animation, log an error and inform the user that the confetti effect could not be created. Do not attempt to retry automatically.

def generate_confetti_html():
    """Generates HTML with JavaScript for a confetti animation."""
    html_content = """
<div id="confetti-container" style="position: fixed; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; z-index: 9999;"></div>
<script>
    function createConfettiParticle(x, y) {
        const particle = document.createElement('div');
        particle.style.position = 'absolute';
        particle.style.width = '10px';
        particle.style.height = '10px';
        particle.style.backgroundColor = '#' + Math.floor(Math.random()*16777215).toString(16); // Random color
        particle.style.left = x + 'px';
        particle.style.top = y + 'px';
        particle.style.opacity = '0.8';
        particle.style.animation = 'fall 3s forwards';
        document.getElementById('confetti-container').appendChild(particle);

        const angle = Math.random() * 2 * Math.PI;
        const speed = Math.random() * 10 + 5;
        const vx = Math.cos(angle) * speed;
        const vy = Math.sin(angle) * speed;

        let currentX = x;
        let currentY = y;
        const animationStep = () => {
            currentX += vx;
            currentY += vy;
            particle.style.left = currentX + 'px';
            particle.style.top = currentY + 'px';
            if (currentY > window.innerHeight || currentX < -50 || currentX > window.innerWidth + 50) {
                particle.remove();
            } else {
                requestAnimationFrame(animationStep);
            }
        };
        requestAnimationFrame(animationStep);
    }

    function triggerConfetti(count = 100) {
        const container = document.getElementById('confetti-container');
        if (!container) return;

        container.innerHTML = '';

        for (let i = 0; i < count; i++) {
            const x = Math.random() * window.innerWidth;
            const y = Math.random() * window.innerHeight * 0.2; 
            createConfettiParticle(x, y);
        }
    }

    const styleSheet = document.createElement("style");
    styleSheet.type = "text/css";
    styleSheet.innerText = "\n        @keyframes fall {\n            from { transform: translateY(0) rotate(0deg); opacity: 0.8; }\n            to { transform: translateY(100vh) rotate(360deg); opacity: 0; }\n        }\n    ";
    document.head.appendChild(styleSheet);

    triggerConfetti(150);
</script>
    """
    return html_content

# This agent will execute generate_confetti_html() and display the result.
# For demonstration, it will simply return the HTML string.
# In a real scenario, this might interact with a browser tool.

# This is a placeholder logic. A more advanced agent would use a browser tool to render this.
# For now, we'll simulate by returning the generated HTML.

def run_confetti_animation():
    return generate_confetti_html()

