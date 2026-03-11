/**
 * SOMA-AI Visual Face – Shader Logic v2
 * ========================================
 * Zwei schaltbare Visualisierungen:
 *   A) "Neural Wave" – Organische, lebendige Wellenlandschaft
 *   B) "Orb"         – Pulsierende Energie-Sphäre mit Tentakeln
 *
 * Reagiert auf Audio-Amplitude, Sprache, Denken, Emotion.
 * Kein Delta-Time → kein "Resetten" mehr.
 */

(function () {
    'use strict';

    // ══════════════════════════════════════════════════════════════════
    //  STATE
    // ══════════════════════════════════════════════════════════════════

    const state = {
        // Zielwerte (werden sanft interpoliert)
        amplitude:       0.0,
        targetAmplitude: 0.15,
        energy:          0.0,
        targetEnergy:    0.0,

        // Farbe (wird geblended)
        colorR: 0.0, colorG: 1.0, colorB: 0.53,
        targetR: 0.0, targetG: 1.0, targetB: 0.53,

        mode: 'idle',
        vizMode: 'wave',      // 'wave' | 'orb'
        speaking: false,
        audioLevel: 0.0,      // raw audio 0-1
    };

    // ══════════════════════════════════════════════════════════════════
    //  THREE.JS SETUP
    // ══════════════════════════════════════════════════════════════════

    const canvas   = document.getElementById('soma-canvas');
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.setClearColor(0x000000, 1);

    const scene  = new THREE.Scene();
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 10);
    camera.position.z = 1;

    // ══════════════════════════════════════════════════════════════════
    //  SHADER — NEURAL WAVE
    // ══════════════════════════════════════════════════════════════════

    const VERT = `
        varying vec2 vUv;
        void main() {
            vUv = uv;
            gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
    `;

    const WAVE_FRAG = `
        precision highp float;

        uniform float u_time;
        uniform float u_amp;
        uniform float u_energy;
        uniform vec3  u_color;
        uniform vec2  u_res;

        varying vec2 vUv;

        // ── Simplex-ish noise (GPU) ─────────────────────────────
        vec3 mod289(vec3 x) { return x - floor(x / 289.0) * 289.0; }
        vec2 mod289(vec2 x) { return x - floor(x / 289.0) * 289.0; }
        vec3 permute(vec3 x) { return mod289((x * 34.0 + 1.0) * x); }

        float snoise(vec2 v) {
            const vec4 C = vec4(0.211324865, 0.366025403, -0.577350269, 0.024390243);
            vec2 i = floor(v + dot(v, C.yy));
            vec2 x0 = v - i + dot(i, C.xx);
            vec2 i1 = (x0.x > x0.y) ? vec2(1.0, 0.0) : vec2(0.0, 1.0);
            vec4 x12 = x0.xyxy + C.xxzz;
            x12.xy -= i1;
            i = mod289(i);
            vec3 p = permute(permute(i.y + vec3(0.0, i1.y, 1.0)) + i.x + vec3(0.0, i1.x, 1.0));
            vec3 m = max(0.5 - vec3(dot(x0, x0), dot(x12.xy, x12.xy), dot(x12.zw, x12.zw)), 0.0);
            m = m * m; m = m * m;
            vec3 x = 2.0 * fract(p * C.www) - 1.0;
            vec3 h = abs(x) - 0.5;
            vec3 ox = floor(x + 0.5);
            vec3 a0 = x - ox;
            m *= 1.79284291400159 - 0.85373472095314 * (a0 * a0 + h * h);
            vec3 g;
            g.x = a0.x * x0.x + h.x * x0.y;
            g.yz = a0.yz * x12.xz + h.yz * x12.yw;
            return 130.0 * dot(m, g);
        }

        // ── FBM (fractal brownian motion) ───────────────────────
        float fbm(vec2 p) {
            float v = 0.0, a = 0.5;
            mat2 rot = mat2(cos(0.5), sin(0.5), -sin(0.5), cos(0.5));
            for (int i = 0; i < 5; i++) {
                v += a * snoise(p);
                p  = rot * p * 2.0 + vec2(100.0);
                a *= 0.5;
            }
            return v;
        }

        void main() {
            float aspect = u_res.x / u_res.y;
            vec2 uv = vUv;
            uv.x *= aspect;

            float t = u_time;
            float amp = u_amp;
            float nrg = u_energy;

            // ── Mehrschichtige, organische Welle ────────────────
            float y = 0.5;  // Zentrierung

            // Hauptwelle: langsam, breit
            float w1 = sin(uv.x * 2.0 + t * 0.6) * 0.08 * amp;
            // Sekundaerwelle: mittel
            float w2 = sin(uv.x * 4.5 - t * 0.9 + 1.0) * 0.05 * amp;
            // Tertiaerwelle: schnell, fein
            float w3 = sin(uv.x * 8.0 + t * 1.4 + 2.5) * 0.025 * amp;
            // Noise-Welle: organisch, lebendig
            float wn = fbm(vec2(uv.x * 1.8 - t * 0.35, t * 0.15)) * 0.07 * amp;
            // Audio-Reaktion: schnelle Mikro-Textur
            float wa = snoise(vec2(uv.x * 12.0 + t * 3.0, t * 2.0)) * 0.04 * nrg;

            float wave = w1 + w2 + w3 + wn + wa;
            float dist = abs(uv.y - y - wave);

            // ── Glow-Layers ─────────────────────────────────────
            // Kern-Linie (duenn, hell)
            float core  = smoothstep(0.004, 0.0, dist);
            // Innerer Glow
            float glow1 = 0.006 / (dist + 0.006);
            // Aeusserer Glow (weich, weit)
            float glow2 = 0.04 / (dist * dist + 0.04);

            // Breathing Pulse
            float breath = 0.5 + 0.5 * sin(t * 0.4);
            float pulse  = 0.5 + 0.5 * sin(t * 1.2);

            // Farb-Komposition
            vec3 coreColor  = u_color * 1.8;
            vec3 glowColor  = u_color * (0.5 + nrg * 0.5);
            vec3 outerColor = u_color * 0.15;

            vec3 col = vec3(0.0);
            col += coreColor  * core;
            col += glowColor  * glow1 * (0.5 + 0.2 * breath);
            col += outerColor * glow2 * (0.4 + 0.15 * pulse);

            // ── Partikel-Feld (kleine leuchtende Punkte) ────────
            for (float i = 0.0; i < 8.0; i++) {
                float seed = i * 127.1 + 311.7;
                float px = fract(sin(seed) * 43758.5) * aspect;
                float py = 0.5 + sin(t * 0.3 + i * 2.0) * 0.15 * amp
                         + fbm(vec2(i, t * 0.2)) * 0.08;
                float pd = length(vec2(uv.x - px, uv.y - py));
                float brightness = amp * (0.3 + nrg * 0.7);
                float pt = 0.0008 / (pd * pd + 0.0008) * brightness;
                col += u_color * pt * 0.4;
            }

            // ── Horizontale Scan-Linien (subtil, CRT-Effekt) ───
            float scan = sin(uv.y * u_res.y * 1.5) * 0.015 + 0.985;
            col *= scan;

            // ── Vignette ────────────────────────────────────────
            vec2 vig = vUv * 2.0 - 1.0;
            float v = 1.0 - dot(vig * 0.45, vig * 0.45);
            v = smoothstep(0.0, 1.0, v);
            col *= v;

            // ── Hintergrund-Atem ────────────────────────────────
            float bgBreath = sin(t * 0.3) * 0.008 + 0.012;
            col += u_color * bgBreath * v;

            gl_FragColor = vec4(col, 1.0);
        }
    `;

    // ══════════════════════════════════════════════════════════════════
    //  SHADER — ORB (Energie-Sphaere)
    // ══════════════════════════════════════════════════════════════════

    const ORB_FRAG = `
        precision highp float;

        uniform float u_time;
        uniform float u_amp;
        uniform float u_energy;
        uniform vec3  u_color;
        uniform vec2  u_res;

        varying vec2 vUv;

        // ── Simplex noise ───────────────────────────────────────
        vec3 mod289(vec3 x) { return x - floor(x / 289.0) * 289.0; }
        vec2 mod289(vec2 x) { return x - floor(x / 289.0) * 289.0; }
        vec3 permute(vec3 x) { return mod289((x * 34.0 + 1.0) * x); }

        float snoise(vec2 v) {
            const vec4 C = vec4(0.211324865, 0.366025403, -0.577350269, 0.024390243);
            vec2 i = floor(v + dot(v, C.yy));
            vec2 x0 = v - i + dot(i, C.xx);
            vec2 i1 = (x0.x > x0.y) ? vec2(1.0, 0.0) : vec2(0.0, 1.0);
            vec4 x12 = x0.xyxy + C.xxzz;
            x12.xy -= i1;
            i = mod289(i);
            vec3 p = permute(permute(i.y + vec3(0.0, i1.y, 1.0)) + i.x + vec3(0.0, i1.x, 1.0));
            vec3 m = max(0.5 - vec3(dot(x0, x0), dot(x12.xy, x12.xy), dot(x12.zw, x12.zw)), 0.0);
            m = m * m; m = m * m;
            vec3 x = 2.0 * fract(p * C.www) - 1.0;
            vec3 h = abs(x) - 0.5;
            vec3 ox = floor(x + 0.5);
            vec3 a0 = x - ox;
            m *= 1.79284291400159 - 0.85373472095314 * (a0 * a0 + h * h);
            vec3 g;
            g.x = a0.x * x0.x + h.x * x0.y;
            g.yz = a0.yz * x12.xz + h.yz * x12.yw;
            return 130.0 * dot(m, g);
        }

        float fbm(vec2 p) {
            float v = 0.0, a = 0.5;
            mat2 rot = mat2(cos(0.5), sin(0.5), -sin(0.5), cos(0.5));
            for (int i = 0; i < 6; i++) {
                v += a * snoise(p);
                p  = rot * p * 2.0 + vec2(100.0);
                a *= 0.5;
            }
            return v;
        }

        void main() {
            float aspect = u_res.x / u_res.y;
            vec2 uv = (vUv - 0.5) * 2.0;
            uv.x *= aspect;

            float t = u_time;
            float amp = u_amp;
            float nrg = u_energy;

            float dist = length(uv);

            // ── Kern-Radius mit organischer Verzerrung ──────────
            float baseR = 0.22 + amp * 0.08;
            float angle = atan(uv.y, uv.x);

            // Verzerrung: die Oberflaeche "atmet"
            float distort = 0.0;
            distort += sin(angle * 3.0 + t * 0.7) * 0.03 * amp;
            distort += sin(angle * 5.0 - t * 1.1) * 0.02 * amp;
            distort += snoise(vec2(angle * 2.0, t * 0.5)) * 0.04 * amp;
            // Audio-Reaktion auf der Oberflaeche
            distort += snoise(vec2(angle * 8.0, t * 3.0)) * 0.03 * nrg;

            float r = baseR + distort;

            // ── Kern (solider Bereich) ──────────────────────────
            float inner = smoothstep(r + 0.01, r - 0.03, dist);

            // Kern-Textur: wirbelndes Plasma
            float plasma = fbm(uv * 3.0 + t * 0.3) * 0.5 + 0.5;
            float plasma2 = fbm(uv * 5.0 - t * 0.4 + 50.0) * 0.5 + 0.5;
            float kernelTex = mix(plasma, plasma2, 0.5 + 0.5 * sin(t * 0.2));

            // ── Glow-Ringe ──────────────────────────────────────
            float glow1 = 0.03 / (abs(dist - r) + 0.03);
            float glow2 = 0.15 / (dist * dist + 0.15);
            float glow3 = 0.008 / (abs(dist - r - 0.05) + 0.008);

            // ── Tentakel / Strahlen ─────────────────────────────
            float rays = 0.0;
            for (float i = 0.0; i < 12.0; i++) {
                float ra = i * 3.14159 * 2.0 / 12.0 + t * 0.15 + sin(t * 0.3 + i) * 0.3;
                float rLen = 0.15 + amp * 0.25 + nrg * 0.15
                           + sin(t * 0.8 + i * 1.7) * 0.05;
                vec2 dir = vec2(cos(ra), sin(ra));

                // Abstand von Punkt zur Tentakel-Linie
                float proj = dot(uv, dir);
                if (proj > r * 0.5 && proj < r + rLen) {
                    vec2 closest = dir * proj;
                    float d = length(uv - closest);
                    float thick = 0.008 + 0.005 * sin(proj * 20.0 + t * 4.0);
                    float ray = thick / (d + thick);
                    ray *= smoothstep(r + rLen, r * 0.8, proj);
                    ray *= amp;
                    rays += ray * 0.15;
                }
            }

            // ── Partikel ────────────────────────────────────────
            float particles = 0.0;
            for (float i = 0.0; i < 15.0; i++) {
                float seed = i * 73.1 + 137.3;
                float pa = fract(sin(seed) * 43758.5) * 6.283;
                float pr = 0.25 + fract(cos(seed) * 22578.3) * 0.4;
                pa += t * (0.1 + fract(sin(seed * 2.0) * 1234.5) * 0.2);
                pr += sin(t * 0.4 + i) * 0.08;
                vec2 pp = vec2(cos(pa), sin(pa)) * pr;
                float pd = length(uv - pp);
                float bright = 0.3 + nrg * 0.7;
                particles += (0.0004 / (pd * pd + 0.0004)) * bright * amp;
            }

            // ── Farb-Zusammenbau ────────────────────────────────
            vec3 col = vec3(0.0);

            // Kern
            vec3 kernColor = u_color * (0.6 + kernelTex * 0.6);
            col += kernColor * inner;

            // Glows
            vec3 surfaceGlow = u_color * 1.2;
            col += surfaceGlow * glow1 * 0.25;
            col += u_color * glow2 * 0.15 * amp;
            col += u_color * 0.6 * glow3 * 0.3;

            // Tentakel
            col += u_color * rays;

            // Partikel
            col += u_color * 0.7 * particles;

            // Breathing
            float breath = 0.5 + 0.5 * sin(t * 0.35);
            col *= 0.85 + 0.15 * breath;

            // ── Scan-Linien ─────────────────────────────────────
            float scan = sin(vUv.y * u_res.y * 1.2) * 0.012 + 0.988;
            col *= scan;

            // ── Vignette ────────────────────────────────────────
            float vig = 1.0 - dot(vUv * 2.0 - 1.0, (vUv * 2.0 - 1.0) * 0.35);
            vig = smoothstep(0.0, 1.0, vig);
            col *= vig;

            gl_FragColor = vec4(col, 1.0);
        }
    `;

    // ══════════════════════════════════════════════════════════════════
    //  MATERIAL + MESHES
    // ══════════════════════════════════════════════════════════════════

    function makeUniforms() {
        return {
            u_time:   { value: 0.0 },
            u_amp:    { value: 0.15 },
            u_energy: { value: 0.0 },
            u_color:  { value: new THREE.Vector3(0.0, 1.0, 0.53) },
            u_res:    { value: new THREE.Vector2(window.innerWidth, window.innerHeight) },
        };
    }

    const waveMat = new THREE.ShaderMaterial({
        vertexShader: VERT,
        fragmentShader: WAVE_FRAG,
        uniforms: makeUniforms(),
    });

    const orbMat = new THREE.ShaderMaterial({
        vertexShader: VERT,
        fragmentShader: ORB_FRAG,
        uniforms: makeUniforms(),
    });

    const geo = new THREE.PlaneGeometry(2, 2);

    const waveMesh = new THREE.Mesh(geo, waveMat);
    const orbMesh  = new THREE.Mesh(geo, orbMat);
    orbMesh.visible = false;

    scene.add(waveMesh);
    scene.add(orbMesh);

    // ══════════════════════════════════════════════════════════════════
    //  RESIZE
    // ══════════════════════════════════════════════════════════════════

    window.addEventListener('resize', () => {
        renderer.setSize(window.innerWidth, window.innerHeight);
        const w = window.innerWidth, h = window.innerHeight;
        waveMat.uniforms.u_res.value.set(w, h);
        orbMat.uniforms.u_res.value.set(w, h);
    });

    // ══════════════════════════════════════════════════════════════════
    //  MODE PRESETS
    // ══════════════════════════════════════════════════════════════════

    const MODES = {
        idle:      { amp: 0.35, energy: 0.0,  r: 0.0, g: 1.0, b: 0.53 },
        listening: { amp: 0.45, energy: 0.1,  r: 0.0, g: 0.9, b: 0.65 },
        speaking:  { amp: 0.85, energy: 0.7,  r: 0.0, g: 1.0, b: 0.53 },
        thinking:  { amp: 0.55, energy: 0.35, r: 0.15, g: 0.5, b: 1.0 },
        critical:  { amp: 0.7,  energy: 0.6,  r: 1.0, g: 0.15, b: 0.15 },
    };

    // ══════════════════════════════════════════════════════════════════
    //  PUBLIC API  →  window.SomaFace
    // ══════════════════════════════════════════════════════════════════

    window.SomaFace = {

        /** Setzt den Status-Modus (idle|listening|speaking|thinking|critical) */
        setMode(mode) {
            const p = MODES[mode] || MODES.idle;
            state.mode = mode;
            state.targetAmplitude = p.amp;
            state.targetEnergy    = p.energy;
            state.targetR = p.r;
            state.targetG = p.g;
            state.targetB = p.b;
        },

        /** Direkte Audio-Pegel Zufuehrung (0-1) */
        pushAudioLevel(level) {
            state.audioLevel = Math.max(0, Math.min(1, level));
            // Audio-Boost auf die Energie
            const base = MODES[state.mode]?.energy || 0;
            state.targetEnergy = base + level * 0.6;
            // Amplitude ebenfalls leicht anheben
            const baseAmp = MODES[state.mode]?.amp || 0.35;
            state.targetAmplitude = baseAmp + level * 0.3;
        },

        /** Umschalten Wave <-> Orb */
        toggleViz() {
            if (state.vizMode === 'wave') {
                state.vizMode = 'orb';
                waveMesh.visible = false;
                orbMesh.visible  = true;
            } else {
                state.vizMode = 'wave';
                waveMesh.visible = true;
                orbMesh.visible  = false;
            }
            return state.vizMode;
        },

        /** Aktuelle Visualisierung abfragen */
        getVizMode() {
            return state.vizMode;
        },

        setAmplitude(v) { state.targetAmplitude = Math.max(0, Math.min(1.2, v)); },
        setEnergy(v)    { state.targetEnergy    = Math.max(0, Math.min(1.2, v)); },
    };

    // ══════════════════════════════════════════════════════════════════
    //  ANIMATION LOOP  (performance.now – keine Spruenge!)
    // ══════════════════════════════════════════════════════════════════

    let prevNow    = performance.now();
    let smoothTime = 0;

    function animate(now) {
        requestAnimationFrame(animate);

        // Delta mit Clamp: maximal 50ms (20 FPS minimum).
        // Verhindert "Spruenge" bei Tab-Wechsel komplett.
        let rawDelta = (now - prevNow) * 0.001;  // ms -> s
        prevNow = now;
        const delta = Math.min(rawDelta, 0.05);

        smoothTime += delta;

        // ── Sanfte Interpolation ────────────────────────────────
        const lerpSpeed  = 3.5;   // Amplitude/Energie
        const colorSpeed = 2.5;   // Farbe

        state.amplitude += (state.targetAmplitude - state.amplitude) * delta * lerpSpeed;
        state.energy    += (state.targetEnergy    - state.energy)    * delta * lerpSpeed;
        state.colorR    += (state.targetR - state.colorR) * delta * colorSpeed;
        state.colorG    += (state.targetG - state.colorG) * delta * colorSpeed;
        state.colorB    += (state.targetB - state.colorB) * delta * colorSpeed;

        // ── Uniforms updaten ────────────────────────────────────
        const mat = (state.vizMode === 'wave') ? waveMat : orbMat;
        mat.uniforms.u_time.value   = smoothTime;
        mat.uniforms.u_amp.value    = state.amplitude;
        mat.uniforms.u_energy.value = state.energy;
        mat.uniforms.u_color.value.set(state.colorR, state.colorG, state.colorB);

        renderer.render(scene, camera);
    }

    // ══════════════════════════════════════════════════════════════════
    //  START
    // ══════════════════════════════════════════════════════════════════

    window.SomaFace.setMode('idle');
    requestAnimationFrame(animate);

    console.log('[SOMA Face] Visual interface v2 initialized.');
})();
