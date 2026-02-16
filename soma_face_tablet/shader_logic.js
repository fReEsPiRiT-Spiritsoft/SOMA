/**
 * SOMA-AI Visual Face – Shader Logic
 * ====================================
 * WebGL Sinuswellen-Visualisierung mit Three.js.
 * Reagiert auf Audio-Amplitude und System-Status.
 *
 * Die Welle ist das "Gesicht" von SOMA:
 *   - Ruhig (idle): Sanfte, langsame Welle
 *   - Hörend: Leichte Reaktion
 *   - Sprechend: Starke, schnelle Welle
 *   - Denkend: Pulsierende Farbe
 *   - Kritisch: Rotes Warnsignal
 */

(function () {
    'use strict';

    // ── State ───────────────────────────────────────────────────────────
    const state = {
        amplitude: 0.0,        // 0-1, Audio-Reaktion
        targetAmplitude: 0.0,
        frequency: 2.0,        // Wellenfrequenz
        speed: 0.5,            // Animation-Speed
        colorR: 0.0,
        colorG: 1.0,
        colorB: 0.53,          // SOMA-Grün #00ff88
        mode: 'idle',          // idle | listening | speaking | thinking | critical
        time: 0,
    };

    // ── Three.js Setup ──────────────────────────────────────────────────
    const canvas = document.getElementById('soma-canvas');
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.setClearColor(0x000000, 1);

    const scene = new THREE.Scene();
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 10);
    camera.position.z = 1;

    // ── Shader Material ─────────────────────────────────────────────────
    const vertexShader = `
        varying vec2 vUv;
        void main() {
            vUv = uv;
            gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
    `;

    const fragmentShader = `
        precision highp float;

        uniform float u_time;
        uniform float u_amplitude;
        uniform float u_frequency;
        uniform vec3  u_color;
        uniform vec2  u_resolution;

        varying vec2 vUv;

        // Smooth noise function
        float hash(float n) { return fract(sin(n) * 43758.5453123); }

        void main() {
            vec2 uv = vUv;
            float aspect = u_resolution.x / u_resolution.y;
            uv.x *= aspect;

            // Base wave
            float wave = 0.0;
            float baseY = 0.5;

            // Multi-layer sine waves
            for (float i = 1.0; i <= 5.0; i += 1.0) {
                float amp = u_amplitude * (0.15 / i);
                float freq = u_frequency * i * 1.5;
                float speed = u_time * (0.3 + i * 0.1);
                wave += sin(uv.x * freq + speed) * amp;
            }

            // Distance from wave center
            float dist = abs(uv.y - baseY - wave);

            // Glow effect
            float glow = 0.003 / (dist + 0.003);
            glow = pow(glow, 1.5);

            // Thin bright line
            float line = smoothstep(0.003, 0.0, dist) * 0.8;

            // Color
            vec3 color = u_color * (glow * 0.4 + line);

            // Subtle background pulse
            float bgPulse = sin(u_time * 0.5) * 0.02 + 0.02;
            color += u_color * bgPulse * (1.0 - dist * 3.0);

            // Vignette
            vec2 vigUv = vUv * 2.0 - 1.0;
            float vig = 1.0 - dot(vigUv * 0.5, vigUv * 0.5);
            color *= vig;

            gl_FragColor = vec4(color, 1.0);
        }
    `;

    const material = new THREE.ShaderMaterial({
        vertexShader,
        fragmentShader,
        uniforms: {
            u_time:       { value: 0.0 },
            u_amplitude:  { value: 0.3 },
            u_frequency:  { value: 2.0 },
            u_color:      { value: new THREE.Vector3(0.0, 1.0, 0.53) },
            u_resolution: { value: new THREE.Vector2(window.innerWidth, window.innerHeight) },
        },
    });

    const geometry = new THREE.PlaneGeometry(2, 2);
    const mesh = new THREE.Mesh(geometry, material);
    scene.add(mesh);

    // ── Resize Handler ──────────────────────────────────────────────────
    window.addEventListener('resize', () => {
        renderer.setSize(window.innerWidth, window.innerHeight);
        material.uniforms.u_resolution.value.set(window.innerWidth, window.innerHeight);
    });

    // ── Mode Presets ────────────────────────────────────────────────────
    const MODES = {
        idle:      { amplitude: 0.15, frequency: 2.0, speed: 0.5, r: 0.0, g: 1.0, b: 0.53 },
        listening: { amplitude: 0.25, frequency: 3.0, speed: 0.7, r: 0.0, g: 0.9, b: 0.6 },
        speaking:  { amplitude: 0.6,  frequency: 4.0, speed: 1.2, r: 0.0, g: 1.0, b: 0.53 },
        thinking:  { amplitude: 0.35, frequency: 2.5, speed: 0.4, r: 0.2, g: 0.6, b: 1.0 },
        critical:  { amplitude: 0.5,  frequency: 5.0, speed: 1.5, r: 1.0, g: 0.2, b: 0.2 },
    };

    // ── Public API ──────────────────────────────────────────────────────
    window.SomaFace = {
        setMode(mode) {
            const preset = MODES[mode] || MODES.idle;
            state.mode = mode;
            state.targetAmplitude = preset.amplitude;
            state.frequency = preset.frequency;
            state.speed = preset.speed;
            state.colorR = preset.r;
            state.colorG = preset.g;
            state.colorB = preset.b;
        },

        setAmplitude(value) {
            state.targetAmplitude = Math.max(0, Math.min(1, value));
        },

        pushAudioLevel(level) {
            // Direct audio reactivity (0-1)
            const base = MODES[state.mode]?.amplitude || 0.15;
            state.targetAmplitude = base + level * 0.5;
        },
    };

    // ── Animation Loop ──────────────────────────────────────────────────
    const clock = new THREE.Clock();

    function animate() {
        requestAnimationFrame(animate);

        const delta = clock.getDelta();
        state.time += delta * state.speed;

        // Smooth amplitude interpolation
        state.amplitude += (state.targetAmplitude - state.amplitude) * delta * 3.0;

        // Update uniforms
        material.uniforms.u_time.value = state.time;
        material.uniforms.u_amplitude.value = state.amplitude;
        material.uniforms.u_frequency.value = state.frequency;
        material.uniforms.u_color.value.set(state.colorR, state.colorG, state.colorB);

        renderer.render(scene, camera);
    }

    // ── Start ───────────────────────────────────────────────────────────
    window.SomaFace.setMode('idle');
    animate();

    console.log('[SOMA Face] Visual interface initialized. 🧠');
})();
