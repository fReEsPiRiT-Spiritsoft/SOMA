(function() {
    // 1. CSS Styles dynamisch hinzufügen
    const style = document.createElement('style');
    style.innerHTML = `
        * { cursor: none !important; }
        #reptileCanvas {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            z-index: 999999; 
            pointer-events: none;
            display: block;
        }
    `;
    document.head.appendChild(style);

    // 2. Canvas Element erstellen
    const canvas = document.createElement('canvas');
    canvas.id = 'reptileCanvas';
    document.body.appendChild(canvas);
    const ctx = canvas.getContext('2d');

    let mouse = { x: -100, y: -100 };
    let isOverInteractive = false;
    let animTime = 0;
    let lastPos = { x: 0, y: 0 };

    // Resize Logik
    const resize = () => {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
    };
    window.addEventListener('resize', resize);
    resize();

    // Mouse Tracking & Hover Detection
    window.addEventListener('mousemove', (e) => {
        mouse.x = e.clientX;
        mouse.y = e.clientY;
        const el = document.elementFromPoint(e.clientX, e.clientY);
        isOverInteractive = (el && (el.tagName === 'A' || el.tagName === 'BUTTON' || window.getComputedStyle(el).cursor === 'pointer'));
    });

    class Segment {
        constructor(size) {
            this.x = 0; this.y = 0; this.angle = 0; this.size = size;
        }
        update(tX, tY) {
            let dx = tX - this.x;
            let dy = tY - this.y;
            this.angle = Math.atan2(dy, dx);
            this.x = tX - Math.cos(this.angle) * this.size;
            this.y = tY - Math.sin(this.angle) * this.size;
        }
        draw(index, total, moving) {
            ctx.strokeStyle = isOverInteractive ? '#00ff88' : 'white';
            ctx.fillStyle = isOverInteractive ? 'rgba(0, 255, 136, 0.3)' : 'rgba(255, 255, 255, 0.1)';
            ctx.lineWidth = 1.5;

            const thickness = 12 * (1 - index / total);

            ctx.beginPath();
            ctx.ellipse(this.x, this.y, thickness/2, 6, this.angle + Math.PI/2, 0, Math.PI*2);
            ctx.fill();
            ctx.stroke();

            // Beinchen
            if (index > 2 && index < total - 8 && index % 6 === 0) {
                const swing = moving ? Math.sin(animTime + index) * 0.8 : 0;
                for (let side of [-1, 1]) {
                    const sideAngle = this.angle + (Math.PI / 2.3 * side) + (swing * side);
                    ctx.beginPath();
                    ctx.moveTo(this.x, this.y);
                    ctx.lineTo(this.x + Math.cos(sideAngle) * 18, this.y + Math.sin(sideAngle) * 18);
                    ctx.stroke();
                }
            }
        }
    }

    const segments = Array.from({ length: 38 }, () => new Segment(8));

    function loop() {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        const dist = Math.hypot(mouse.x - lastPos.x, mouse.y - lastPos.y);
        if (dist > 0.1) animTime += dist * 0.12;

        let tX = mouse.x, tY = mouse.y;
        segments.forEach((seg, i) => {
            seg.update(tX, tY);
            seg.draw(i, segments.length, dist > 0.1);
            tX = seg.x; tY = seg.y;
        });
        lastPos = { ...mouse };
        requestAnimationFrame(loop);
    }
    loop();
})();