
// AI Ghost Mode HUD Injector
(function() {
    // 1. Inject CSS Styles
    const style = document.createElement('style');
    style.innerHTML = `
        body.ai-mode {
            border: 4px solid #a855f7 !important;
            box-sizing: border-box !important;
        }

        #ai-cursor {
            position: fixed;
            top: 0;
            left: 0;
            width: 20px;
            height: 20px;
            border: 2px solid #a855f7;
            border-radius: 50%;
            background-color: rgba(168, 85, 247, 0.2);
            box-shadow: 0 0 10px #a855f7, inset 0 0 5px #a855f7;
            z-index: 2147483647; /* Max z-index */
            pointer-events: none;
            transition: all 0.1s ease;
            transform: translate(-50%, -50%); /* Center cursor on coordinates */
        }

        #ai-cursor::after {
            content: '';
            position: absolute;
            top: 50%;
            left: 50%;
            width: 4px;
            height: 4px;
            background-color: #a855f7;
            border-radius: 50%;
            transform: translate(-50%, -50%);
        }

        .ai-target-locked {
            animation: ai-pulse 0.5s ease-in-out;
            outline: 2px solid #a855f7 !important;
            box-shadow: 0 0 15px rgba(168, 85, 247, 0.6) !important;
        }

        @keyframes ai-pulse {
            0% { transform: scale(1); opacity: 1; }
            50% { transform: scale(1.02); opacity: 0.8; }
            100% { transform: scale(1); opacity: 1; }
        }
    `;
    document.head.appendChild(style);

    // 2. Add AI Mode class to body
    document.body.classList.add('ai-mode');

    // 3. Create Cursor Element
    const cursor = document.createElement('div');
    cursor.id = 'ai-cursor';
    document.body.appendChild(cursor);

    // 4. Update Cursor Position based on "virtual" mouse movements
    // Playwright mouse movements trigger standard mouse events.
    // We listen to them to update our visual cursor.
    document.addEventListener('mousemove', (e) => {
        cursor.style.left = e.clientX + 'px';
        cursor.style.top = e.clientY + 'px';
    });

    // 5. Public Functions to Highlight Targets

    // Highlight a specific DOM element directly
    window.highlightTargetElement = function(element) {
        if (!element) return;
        try {
            // Cleanup previous locks
            document.querySelectorAll('.ai-target-locked').forEach(el => el.classList.remove('ai-target-locked'));

            element.classList.add('ai-target-locked');
            element.scrollIntoView({ behavior: 'smooth', block: 'center' });

            // Auto-remove after animation
            setTimeout(() => {
                element.classList.remove('ai-target-locked');
            }, 1000);
        } catch (e) {
            console.error("AI HUD Error:", e);
        }
    };

    // Highlight by selector string (Legacy/Simple support)
    window.highlightTarget = function(selector) {
        try {
            const element = document.querySelector(selector);
            if (element) {
                window.highlightTargetElement(element);
            }
        } catch (e) {
            console.error("AI HUD Error:", e);
        }
    };
})();
