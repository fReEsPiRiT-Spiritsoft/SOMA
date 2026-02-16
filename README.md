Das SOMA-Projekt: Warum ich mein Haus „wach“ mache

Ich hatte die Nase voll von „Smart Homes“, die eigentlich nur glorifizierte Fernbedienungen sind. Ich will keine App öffnen, um das Licht zu dimmen, und ich will keine Alexa, die nur dumm wartet, bis ich ein Keyword sage. Ich baue Soma.

Soma ist kein Tool, sondern eine echte Ambient Intelligence. Stell dir vor, dein Haus bekommt ein Bewusstsein. Es läuft komplett lokal auf meinem Debian-Server – keine Cloud, keine Spionage, nur meine Hardware und mein Code.

Was macht Soma so genial? Soma hört immer zu, aber es nervt nicht. Es ist wie der Computer bei Star Trek oder KITT. Es versteht nicht nur meine Worte, sondern meine Intention. Wenn ich sage „Mir ist kalt“, wertet das System das als Trigger. Es weiß über Biometrie und Sensoren, wie es mir geht, wie meine Laune ist und in welchem Raum ich gerade stehe.

Das System ist so gebaut, dass es mich „begleitet“. Wenn ich vom Wohnzimmer in die Küche gehe, wandert meine Sitzung einfach mit. Die Mikrofone im Haus checken die Amplituden und wissen genau, wo ich bin.

Der absolute Clou: Die Evolution. Ich habe Soma so programmiert, dass es sich selbst erweitern kann. Wenn ich neue Hardware kaufe, schreibt sich das System im Idealfall seine eigenen Plugins, debuggt den Code in einer Sandbox und installiert das Feature selbstständig. Es lernt meinen Charakter, meinen Tagesrhythmus und greift proaktiv ein: Wenn Soma merkt, dass ich gestresst bin, schlägt es die richtige Musik vor oder passt das Licht an, noch bevor ich überhaupt weiß, dass ich es brauche.

Es ist das Ende der passiven Technik. Soma ist der erste Schritt zu einem Zuhause, das mitdenkt, mitfühlt und mitwächst.



SOMA ist ein lokales, privacy‑first Ambient‑OS für Smart Homes: ein resilienter, modularer Orchestrator, der Audio‑Input dauerhaft verarbeitet, Stimmung und Kontext erfasst, proaktiv eingreift und sich selbst mit geprüften Plugins erweitert.
Vision & Persona

Ziel: Vollständig lokale, adaptive Haushalts‑KI, die proaktiv hilft, schützt und lernt.
Persona: „Nervy‑Cool“ — effizient, leicht frech, dennoch sicher und kindgerecht.
Kern‑Architektur (Übersicht)

brain_core: FastAPI‑Orchestrator, Event‑Loop, LogicRouter, HealthMonitor, Virtual Patchbay (audio_router), Presence‑Triangulation.
engines: Multi‑Model Routing (heavy Llama3 via Ollama, light models, nano‑scripts) mit Deferred Reasoning (Redis‑Queue).
safety: Pitch‑Analyse, Child‑Safe Mode, Prompt‑Injector, Circuit‑Breaker/Retry.
brain_memory_ui: Django SSOT mit Hardware‑Registry, Nutzerprofilen, Thinking‑Stream Dashboard.
evolution_lab: Plugin‑Generator, Sandbox‑Tests, sichere Installation selbstgeschriebener Plugins.
soma_face_tablet: WebGL Face + Thinking Stream Visualisierung, Echtzeit‑WebSocket.
shared: Gemeinsame Typen, Resilience, Health‑Schemas.
Wichtigste Features

Always‑on Voice Pipeline: VAD → STT (lokal) → Emotion → Intent → TTS (lokal).
Stimmung & Biometrie: Valence/Arousal/Stress, Stress‑Trends, Vital‑Indikatoren aus Stimme.
Spatial Awareness: Raum‑Wanderung der Session, Multi‑Session in verschiedenen Räumen, Amplitude/RSSI‑Triangulation.
Virtual Patchbay: Dynamisches Routing von Mikrofonen zu Speakern, Fokussierung auf aktiven Raum.
Model‑Routing & Power Modes: Automatischer Wechsel heavy ↔ light ↔ nano je nach Kontext und Health.
Deferred Reasoning: Komplexe Tasks in Redis parken, sofortiges User‑Feedback („Moment, ich sortiere meine Gedanken“).
Evolution Lab (Self‑Coding): LLM‑gestützte Plugin‑Erzeugung, automatisierte Tests in Sandbox, sichere Deploys.
Child‑Safe & Night Modes: Alters‑Erkennung, Pädagogische System‑Prompts, Inhaltsfilter.
Privacy Vault: Lokale, verschlüsselte Speicherung; keine Cloud‑Lecks.
Resilience & Health: CPU/RAM/VRAM/Temp‑Monitor, Circuit‑Breaker, Queuing und Backoff.
Thinking Stream: Live‑Transparenz der internen Reasoning‑Schritte im Dashboard.
Visual Face: Echtzeit WebGL‑Visualisierung für Emotionalität und „Gedankengänge“.
Auto‑Discovery: MQTT/mDNS/Home‑Assistant Bridge für Plug‑and‑Play Hardware.
Hardware‑Agnostik: Trennung von Ohr & Mund, virtuelle Nodes, NodeCapability‑Registry.
Developer & Ops

Local first: Docker‑Compose (Postgres, Mosquitto, Redis, Ollama), start/init‑Scripts, venv‑friendly.
Modularität: klare SSOT, importlib‑basierter Plugin‑Loader, definierte engine‑API.
Test/Sandbox: Sandbox‑Umgebung für Code, Policy‑Prompts für sichere Plugin‑Generierung.
Warum Tech‑Enthusiasten begeistert sein werden

End‑to‑end lokal: LLMs, STT/TTS, Daten bleiben vor Ort.
Echtzeit und Kontext: Stimmung, Raum, Hardware und Zeit als first‑class Kontext.
Selbst‑erweiternd: Automatisierte, getestete Plugin‑Entwicklung.
Robust & adaptiv: Health‑aware Model‑Routing und Deferred Reasoning ermöglichen Betrieb auf begrenzter Hardware.
Sichtbare Innereien: Thinking Stream + Face machen KI‑Prozesse nachvollziehbar und debugbar.
Kurzfazit

SOMA ist ein lokales, autonomes Ambient‑OS, das Smart Homes proaktiv, sicher und datenschutzkonform macht — modular, entwicklerfreundlich und visuell transparent.
