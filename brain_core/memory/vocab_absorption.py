"""
Personal Vocabulary Absorption — SOMA spricht wie du.
======================================================
Wenn der Nutzer immer "das Ding da oben" fuer den Router sagt,
lernt SOMA "das Ding da oben".  Ausdruecke, Spitznamen, Insider,
Redewendungen — nach einigen Wochen klingt SOMA wie der Nutzer.

Pipeline (siehe Architektur-Diagramm):
  Episodic Memory (User-Utterances)
       │
  VocabExtractor  →  IdiolectScorer
       │                   │
       └───────┬───────────┘
               ▼
       EmbeddingClusterer  (nomic-embed-text + HDBSCAN/Greedy)
               │
    ┌──────────┼──────────┐──────────┐
    ▼          ▼          ▼          ▼
  "das Ding" "Hammer"  "kurz machen" Cluster N…
               │
               ▼
         VocabAbsorber
    ┌──────────┼──────────┐
    ▼          ▼          ▼
  Persona   Context    Dreaming
  Prompt    Injection  Integration
"""

from __future__ import annotations

import math
import os
import re
import sqlite3
import time
import asyncio
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from brain_core.memory.embedding_service import get_embedding_service
from brain_core.memory.user_identity import get_user_name_sync

logger = logging.getLogger("soma.memory.vocab")

DB_PATH = Path(os.getenv("SOMA_MEMORY_DB", "data/soma_memory.db"))

# ── Config ───────────────────────────────────────────────────────────
MIN_OCCURRENCES = 3           # Mindestfrequenz bevor ein Term zaehlt
IDIOLECT_THRESHOLD = 2.0      # TF-IDF Score > X = nutzerspezifisch
CLUSTER_SIM_THRESHOLD = 0.72  # Cosine-Similarity fuer Embedding-Cluster
MIN_CLUSTER_MEMBERS = 2       # Mindestens 2 Terme pro Cluster
MAX_VOCAB_ENTRIES = 500        # Obergrenze gespeicherter Terme
MATURITY_DAYS = 7              # Minimum-Alter bevor ein Term in den Prompt darf
MAX_PROMPT_TERMS = 12          # Max Terme im Idiolekt-Prompt-Block
DECAY_HALFLIFE_DAYS = 60       # Halbwertszeit: unbenutzte Terme verblassen

# Deutsche Stoppwoerter — diese sind NICHT nutzerspezifisch
_STOPWORDS: frozenset[str] = frozenset(
    "der die das ein eine einer einem eines den dem "
    "und oder aber denn wenn weil dass ob als wie "
    "ich du er sie es wir ihr man sich mich dich ihm ihn ihnen "
    "nicht kein keine keinen keiner keinem "
    "ist sind war waren wird werden hat haben hatte hatten bin bist "
    "kannst kann muss musst soll sollst willst will darf darfst "
    "habe hast habe hätte hätten würde würdest wäre wären "
    "auf in an aus bei mit nach von vor zu um fuer ueber unter "
    "ja nein doch schon noch auch nur sehr viel "
    "so da hier dort dann jetzt gerade eben mal "
    "was wer wo wann warum wie welche welcher welches "
    "mein dein sein ihr unser euer "
    "dieser diese dieses jener jene jenes "
    "ok okay aha hmm hm mhm alles klar gut "
    "bitte danke schön vielen herzlichen "
    "mir dir uns euch ihnen ihm "
    "den dem des zur zum ".split()
)

# Deutsche Basis-Frequenzen (Top-500 approximiert)
# Wörter die JEDER Deutsche benutzt → niedriger IDF
_BASE_GERMAN_FREQ: dict[str, float] = {
    w: 0.01
    for w in (
        "hallo bitte danke ja nein gut schlecht "
        "machen gehen kommen sagen wissen können "
        "müssen sollen wollen möchten dürfen "
        "heute morgen gestern abend nacht tag "
        "haus zimmer küche bad wohnzimmer schlafzimmer "
        "licht lampe fenster tür wasser strom heizung "
        "computer handy internet telefon "
        "musik film buch essen trinken kochen "
        "arbeit schule kind kinder frau mann freund "
        "auto fahren bus bahn zug flugzeug "
        "geld kaufen preis euro teuer billig "
        "wetter regen sonne warm kalt "
        "problem hilfe frage antwort idee "
        "super toll cool nice prima klasse "
        "blöd doof scheiße mist verdammt "
        "bitte danke entschuldigung sorry "
        "nochmal wieder weiter fertig "
        "einfach schwer leicht schnell langsam "
        "groß klein viel wenig mehr weniger "
        "alt neu erste letzte nächste "
        "richtig falsch wichtig egal normal".split()
    )
}


# ══════════════════════════════════════════════════════════════════════
#  DATA MODELS
# ══════════════════════════════════════════════════════════════════════


@dataclass
class VocabEntry:
    """Ein gelernter Term/Ausdruck des Nutzers."""
    id: int = 0
    term: str = ""             # Der Ausdruck ("das Ding da oben")
    normalized: str = ""       # Lowercase, stripped
    frequency: int = 0         # Wie oft der Nutzer ihn benutzt hat
    idiolect_score: float = 0  # TF-IDF: wie spezifisch für DIESEN Nutzer
    cluster_id: int = -1       # Semantischer Cluster (-1 = unclustered)
    cluster_label: str = ""    # Z.B. "Router-Spitznamen"
    first_seen: float = 0      # Timestamp
    last_seen: float = 0       # Timestamp
    soma_used_count: int = 0   # Wie oft SOMA den Term selbst benutzt hat
    embedding: Optional[np.ndarray] = None
    maturity: float = 0.0      # 0.0–1.0: Reife (Zeit + Frequenz + Konsistenz)
    example_context: str = ""  # Ein Beispiel-Satz wo der Nutzer den Term benutzte
    active: bool = True        # False = verfallen (lange nicht benutzt)


@dataclass
class VocabCluster:
    """Ein semantischer Cluster von Termen."""
    cluster_id: int
    label: str               # z.B. "Geräte-Spitznamen"
    terms: list[str] = field(default_factory=list)
    centroid: Optional[np.ndarray] = None
    created: float = 0.0


# ══════════════════════════════════════════════════════════════════════
#  VOCAB EXTRACTOR — Tokenize · Freq · N-Grams
# ══════════════════════════════════════════════════════════════════════


class VocabExtractor:
    """
    Extrahiert Vokabular aus User-Utterances.
    Sammelt Unigrams, Bigrams und Trigrams.
    Filtert Stoppwoerter, zaehlt Frequenzen.
    """

    # Splits: Whitespace + einfache Interpunktion
    _SPLIT_RE = re.compile(r"[^\wäöüßÄÖÜ]+", re.UNICODE)

    def tokenize(self, text: str) -> list[str]:
        """Text → Lowercase Tokens (ohne Stoppwoerter)."""
        tokens = self._SPLIT_RE.split(text.lower().strip())
        return [t for t in tokens if t and t not in _STOPWORDS and len(t) > 1]

    def extract_ngrams(
        self,
        text: str,
        max_n: int = 3,
    ) -> list[str]:
        """
        Extrahiere relevante N-Grams (1–3).

        Returns:
            Liste von Strings: ["router", "ding oben", "das ding oben"]
        """
        # Unigrams (ohne Stoppwoerter)
        tokens_clean = self.tokenize(text)
        ngrams = list(tokens_clean)

        # Fuer Bigrams/Trigrams: MIT Stoppwoertern (damit "das Ding da oben" erhalten bleibt)
        tokens_raw = self._SPLIT_RE.split(text.lower().strip())
        tokens_raw = [t for t in tokens_raw if t and len(t) > 0]

        for n in range(2, max_n + 1):
            for i in range(len(tokens_raw) - n + 1):
                gram = " ".join(tokens_raw[i : i + n])
                # N-Gram muss mindestens ZWEI bedeutungsvolle Woerter enthalten
                # (laenger als 3 Zeichen UND kein Stoppwort)
                # → verhindert "ich bin", "kannst du", "bitte mach" etc.
                content_words = [
                    t for t in tokens_raw[i : i + n]
                    if t not in _STOPWORDS and len(t) > 3
                ]
                if len(content_words) >= 2:
                    ngrams.append(gram)

        return ngrams


# ══════════════════════════════════════════════════════════════════════
#  IDIOLECT SCORER — Nutzer vs. Deutsch allgemein
# ══════════════════════════════════════════════════════════════════════


class IdiolectScorer:
    """
    Berechnet wie spezifisch ein Term fuer DIESEN Nutzer ist.
    Hoher Score = der Nutzer benutzt dieses Wort viel oefter
    als ein durchschnittlicher Deutschsprecher.

    Methode: Modifiziertes TF-IDF
      TF  = log(1 + frequency_user)
      IDF = log(1 / (base_german_freq + epsilon))
      Score = TF × IDF
    """

    def score(self, term: str, user_freq: int, total_user_terms: int) -> float:
        """
        Berechne den Idiolekt-Score eines Terms.

        Args:
            term:             Der Term (lowercase)
            user_freq:        Wie oft der Nutzer diesen Term benutzt hat
            total_user_terms: Gesamtzahl aller Terme des Nutzers

        Returns:
            Idiolekt-Score (hoeher = nutzerspezifischer)
        """
        if user_freq < MIN_OCCURRENCES:
            return 0.0

        # TF: Log-normalisierte Frequenz relativ zur Gesamtzahl
        tf = math.log(1 + user_freq)

        # IDF: Wie selten ist der Term in allgemeinem Deutsch?
        # Basis-Frequenz checken (Wort fuer Wort bei N-Grams)
        words = term.split()
        base_freq = 0.0
        for w in words:
            base_freq = max(base_freq, _BASE_GERMAN_FREQ.get(w, 0.0001))

        # N-Grams sind generell spezifischer → Bonus
        ngram_bonus = 1.0 + 0.3 * (len(words) - 1)

        idf = math.log(1.0 / (base_freq + 1e-6))

        return tf * idf * ngram_bonus


# ══════════════════════════════════════════════════════════════════════
#  EMBEDDING CLUSTERER — nomic-embed-text + Greedy/HDBSCAN
# ══════════════════════════════════════════════════════════════════════


class EmbeddingClusterer:
    """
    Gruppiert aehnliche Terme semantisch.

    1. Embeddings via nomic-embed-text (shared Service)
    2. Clustering via HDBSCAN (falls installiert) oder Greedy-Fallback
    3. Cluster-Labels via Centroid-Nearest-Term
    """

    async def cluster(
        self,
        entries: list[VocabEntry],
    ) -> list[VocabCluster]:
        """
        Clustere VocabEntries nach semantischer Aehnlichkeit.

        Args:
            entries: Liste von VocabEntry (muessen embedding haben)

        Returns:
            Liste von VocabCluster
        """
        # Nur Entries mit Embedding
        with_emb = [e for e in entries if e.embedding is not None]
        if len(with_emb) < MIN_CLUSTER_MEMBERS:
            return []

        # Versuche HDBSCAN, sonst Greedy Fallback
        try:
            clusters = await self._cluster_hdbscan(with_emb)
        except ImportError:
            logger.info("vocab_hdbscan_unavailable_using_greedy")
            clusters = self._cluster_greedy(with_emb)
        except Exception as e:
            logger.warning("vocab_hdbscan_failed_fallback_greedy", error=str(e))
            clusters = self._cluster_greedy(with_emb)

        return clusters

    async def _cluster_hdbscan(
        self,
        entries: list[VocabEntry],
    ) -> list[VocabCluster]:
        """Clustering via HDBSCAN (scikit-learn-extra oder hdbscan)."""
        from hdbscan import HDBSCAN

        matrix = np.array([e.embedding for e in entries])

        # In Executor: HDBSCAN ist CPU-intensiv
        loop = asyncio.get_event_loop()

        def _fit():
            clusterer = HDBSCAN(
                min_cluster_size=MIN_CLUSTER_MEMBERS,
                min_samples=1,
                metric="cosine",
            )
            return clusterer.fit_predict(matrix)

        labels = await loop.run_in_executor(None, _fit)

        return self._labels_to_clusters(entries, labels)

    def _cluster_greedy(
        self,
        entries: list[VocabEntry],
    ) -> list[VocabCluster]:
        """
        Greedy Clustering (kein scikit-learn noetig).
        Wie in background_tasks.py — bewaehrt und schnell.
        """
        used: set[int] = set()
        clusters: list[VocabCluster] = []
        cluster_id = 0

        for i, anchor in enumerate(entries):
            if i in used:
                continue
            members = [anchor]
            used.add(i)

            for j, candidate in enumerate(entries):
                if j in used:
                    continue
                sim = float(np.dot(anchor.embedding, candidate.embedding))
                if sim > CLUSTER_SIM_THRESHOLD:
                    members.append(candidate)
                    used.add(j)

            if len(members) >= MIN_CLUSTER_MEMBERS:
                # Centroid berechnen
                centroid = np.mean(
                    [m.embedding for m in members], axis=0,
                )
                norm = np.linalg.norm(centroid)
                if norm > 0:
                    centroid = centroid / norm

                # Label = Term mit hoechstem Idiolect-Score im Cluster
                label_term = max(members, key=lambda m: m.idiolect_score)
                cluster = VocabCluster(
                    cluster_id=cluster_id,
                    label=label_term.term,
                    terms=[m.term for m in members],
                    centroid=centroid,
                    created=time.time(),
                )
                clusters.append(cluster)

                # Cluster-ID auf Entries schreiben
                for m in members:
                    m.cluster_id = cluster_id
                    m.cluster_label = label_term.term

                cluster_id += 1

        return clusters

    def _labels_to_clusters(
        self,
        entries: list[VocabEntry],
        labels: np.ndarray,
    ) -> list[VocabCluster]:
        """Konvertiert HDBSCAN-Labels zu VocabCluster-Objekten."""
        groups: dict[int, list[VocabEntry]] = defaultdict(list)
        for entry, label in zip(entries, labels):
            if label >= 0:  # -1 = Noise
                groups[int(label)].append(entry)
                entry.cluster_id = int(label)

        clusters: list[VocabCluster] = []
        for cid, members in groups.items():
            centroid = np.mean(
                [m.embedding for m in members], axis=0,
            )
            norm = np.linalg.norm(centroid)
            if norm > 0:
                centroid = centroid / norm
            label_term = max(members, key=lambda m: m.idiolect_score)
            cluster = VocabCluster(
                cluster_id=cid,
                label=label_term.term,
                terms=[m.term for m in members],
                centroid=centroid,
                created=time.time(),
            )
            clusters.append(cluster)
            for m in members:
                m.cluster_label = label_term.term

        return clusters


# ══════════════════════════════════════════════════════════════════════
#  VOCAB ABSORBER — Die Hauptklasse
# ══════════════════════════════════════════════════════════════════════


class VocabAbsorber:
    """
    Koordiniert den gesamten Vocabulary-Absorption-Prozess:

    1. Feed: Neue User-Utterances entgegennehmen
    2. Score: Idiolekt-Bewertung
    3. Cluster: Semantische Gruppierung (Background)
    4. Prompt: Reife Terme in den Persona-Prompt injizieren
    5. Context: Themenrelevante Terme bei Recall liefern
    6. Track: Mitzaehlen wenn SOMA gelernte Terme benutzt
    """

    def __init__(self):
        self._extractor = VocabExtractor()
        self._scorer = IdiolectScorer()
        self._clusterer = EmbeddingClusterer()
        self._db_path = DB_PATH
        self._conn: Optional[sqlite3.Connection] = None
        self._initialized = False
        # In-Memory Counter für schnelle Frequenz-Updates
        self._term_counter: Counter = Counter()
        self._total_terms: int = 0
        self._feed_count: int = 0

    # ── Lifecycle ────────────────────────────────────────────────────

    async def initialize(self):
        """DB-Tabelle erstellen, bestehende Daten laden."""
        if self._initialized:
            return
        loop = asyncio.get_event_loop()
        self._conn = await loop.run_in_executor(None, self._open_db)
        await loop.run_in_executor(None, self._load_counters)
        self._initialized = True
        logger.info(
            "vocab_absorber_online",
            known_terms=len(self._term_counter),
            total_observations=self._total_terms,
        )

    def _open_db(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vocab_idiolect (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                term            TEXT NOT NULL UNIQUE,
                normalized      TEXT NOT NULL,
                frequency       INTEGER DEFAULT 1,
                idiolect_score  REAL DEFAULT 0.0,
                cluster_id      INTEGER DEFAULT -1,
                cluster_label   TEXT DEFAULT '',
                first_seen      REAL NOT NULL,
                last_seen       REAL NOT NULL,
                soma_used_count INTEGER DEFAULT 0,
                embedding       BLOB,
                maturity        REAL DEFAULT 0.0,
                example_context TEXT DEFAULT '',
                active          INTEGER DEFAULT 1
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vocab_term "
            "ON vocab_idiolect(normalized)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vocab_cluster "
            "ON vocab_idiolect(cluster_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vocab_maturity "
            "ON vocab_idiolect(maturity DESC)"
        )
        conn.commit()
        return conn

    def _load_counters(self):
        """Bestehende Frequenzen aus DB in Memory laden."""
        if not self._conn:
            return
        rows = self._conn.execute(
            "SELECT normalized, frequency FROM vocab_idiolect WHERE active = 1"
        ).fetchall()
        for norm, freq in rows:
            self._term_counter[norm] = freq
            self._total_terms += freq

    # ── FEED: User-Text entgegennehmen ───────────────────────────────

    async def feed(self, user_text: str):
        """
        Haupteinstiegspunkt — nach jeder User-Utterance aufrufen.
        Extrahiert N-Grams, aktualisiert Frequenzen, berechnet Scores.
        Non-blocking: DB-Writes im Executor.
        """
        if not self._initialized:
            await self.initialize()

        if not user_text or len(user_text.strip()) < 3:
            return

        ngrams = self._extractor.extract_ngrams(user_text)
        if not ngrams:
            return

        now = time.time()
        self._feed_count += 1

        # Frequenzen in Memory aktualisieren
        for gram in ngrams:
            self._term_counter[gram] += 1
            self._total_terms += 1

        # Idiolect-Scores berechnen + DB-Update (batch, im Executor)
        scored_terms: list[tuple[str, int, float, str]] = []
        for gram in set(ngrams):  # dedupliziert pro Utterance
            freq = self._term_counter[gram]
            score = self._scorer.score(gram, freq, self._total_terms)
            if score > 0:
                scored_terms.append((gram, freq, score, user_text[:200]))

        # Nur Terme mit genuegend hohem Idiolect-Score speichern
        scored_terms = [
            (gram, freq, score, ctx)
            for gram, freq, score, ctx in scored_terms
            if score >= IDIOLECT_THRESHOLD
        ]

        if scored_terms:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, self._upsert_terms, scored_terms, now,
            )

        # Logging bei Meilensteinen
        if self._feed_count % 50 == 0:
            top = self._term_counter.most_common(5)
            logger.info(
                "vocab_feed_milestone",
                feeds=self._feed_count,
                unique_terms=len(self._term_counter),
                top_5=[t[0] for t in top],
            )

    def _upsert_terms(
        self,
        terms: list[tuple[str, int, float, str]],
        now: float,
    ):
        """Batch-UPSERT: Terme in DB aktualisieren oder einfuegen."""
        if not self._conn:
            return
        for gram, freq, score, context in terms:
            cur = self._conn.execute(
                "UPDATE vocab_idiolect SET "
                "frequency = ?, idiolect_score = ?, last_seen = ?, "
                "example_context = CASE "
                "  WHEN length(example_context) < 10 THEN ? "
                "  ELSE example_context "
                "END "
                "WHERE normalized = ?",
                (freq, score, now, context, gram),
            )
            if cur.rowcount == 0:
                self._conn.execute(
                    "INSERT OR IGNORE INTO vocab_idiolect "
                    "(term, normalized, frequency, idiolect_score, "
                    " first_seen, last_seen, example_context, active) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                    (gram, gram, freq, score, now, now, context),
                )
        self._conn.commit()

    # ── TRACK: SOMA-eigene Nutzung zaehlen ───────────────────────────

    async def track_soma_usage(self, soma_text: str):
        """
        Zaehle wenn SOMA einen gelernten Term selbst benutzt.
        Staerkt die Maturity → Term wird oefter injiziert.
        """
        if not self._initialized or not soma_text:
            return
        lower = soma_text.lower()
        # Nur Terme mit hohem Score tracken
        top_terms = await self._get_mature_terms(min_maturity=0.3)
        used = [t for t in top_terms if t.normalized in lower]
        if not used:
            return

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self._increment_soma_usage, [t.normalized for t in used],
        )
        logger.debug(
            "vocab_soma_used",
            terms=[t.term for t in used],
        )

    def _increment_soma_usage(self, terms: list[str]):
        if not self._conn:
            return
        for t in terms:
            self._conn.execute(
                "UPDATE vocab_idiolect SET soma_used_count = soma_used_count + 1 "
                "WHERE normalized = ?",
                (t,),
            )
        self._conn.commit()

    # ── CLUSTER: Semantische Gruppierung (Background) ────────────────

    async def run_clustering(self):
        """
        Semantische Cluster-Analyse — laeuft im Background (Dreaming).
        1. Lade alle aktiven Terme mit idiolect_score > Threshold
        2. Embed alle (cached!)
        3. Cluster via HDBSCAN/Greedy
        4. Speichere Cluster-Zuordnung
        5. Berechne Maturity
        """
        if not self._initialized:
            await self.initialize()

        entries = await self._get_scoreable_entries()
        if len(entries) < MIN_CLUSTER_MEMBERS:
            logger.debug("vocab_clustering_skipped_too_few", count=len(entries))
            return

        # Embeddings berechnen (cached im EmbeddingService)
        embed_svc = get_embedding_service()
        for entry in entries:
            if entry.embedding is None:
                vec = await embed_svc.embed(entry.term)
                entry.embedding = vec

        # Nur mit Embedding clustern
        embeddable = [e for e in entries if e.embedding is not None]
        if len(embeddable) < MIN_CLUSTER_MEMBERS:
            return

        clusters = await self._clusterer.cluster(embeddable)

        # Maturity berechnen + DB Update
        now = time.time()
        for entry in embeddable:
            entry.maturity = self._calculate_maturity(entry, now)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self._save_clustering_results, embeddable,
        )

        logger.info(
            "vocab_clustering_complete",
            terms_processed=len(embeddable),
            clusters_found=len(clusters),
            cluster_labels=[c.label for c in clusters[:5]],
        )

    def _calculate_maturity(self, entry: VocabEntry, now: float) -> float:
        """
        Maturity = gewichtete Kombination aus:
          40% Frequenz-Faktor (log-skaliert)
          30% Alter (Tage seit first_seen, gekappt bei 1.0)
          20% Konsistenz (last_seen nahe an jetzt = aktiv)
          10% SOMA-Nutzung (SOMA hat den Term selbst benutzt)
        """
        # Frequenz: log(freq) / log(20) → 0..1
        freq_factor = min(1.0, math.log(1 + entry.frequency) / math.log(20))

        # Alter: Tage / MATURITY_DAYS → 0..1
        age_days = (now - entry.first_seen) / 86400
        age_factor = min(1.0, age_days / MATURITY_DAYS)

        # Konsistenz: letzte Nutzung < 7 Tage = frisch
        days_since_last = (now - entry.last_seen) / 86400
        consistency = max(0.0, 1.0 - days_since_last / 14.0)

        # SOMA-Nutzung: Bonus wenn SOMA den Term schon uebernommen hat
        soma_bonus = min(1.0, entry.soma_used_count / 3.0)

        maturity = (
            0.40 * freq_factor
            + 0.30 * age_factor
            + 0.20 * consistency
            + 0.10 * soma_bonus
        )
        return round(maturity, 3)

    def _save_clustering_results(self, entries: list[VocabEntry]):
        """Cluster-IDs, Labels, Embeddings und Maturity in DB speichern."""
        if not self._conn:
            return
        for e in entries:
            blob = e.embedding.tobytes() if e.embedding is not None else None
            self._conn.execute(
                "UPDATE vocab_idiolect SET "
                "cluster_id = ?, cluster_label = ?, embedding = ?, maturity = ? "
                "WHERE normalized = ?",
                (e.cluster_id, e.cluster_label, blob, e.maturity, e.normalized),
            )
        self._conn.commit()

    async def _get_scoreable_entries(self) -> list[VocabEntry]:
        """Lade alle aktiven Terme mit genuegend Frequenz."""
        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(None, self._fetch_scoreable)
        entries = []
        for row in rows:
            entries.append(VocabEntry(
                id=row[0],
                term=row[1],
                normalized=row[2],
                frequency=row[3],
                idiolect_score=row[4],
                cluster_id=row[5],
                cluster_label=row[6] or "",
                first_seen=row[7],
                last_seen=row[8],
                soma_used_count=row[9],
                embedding=(
                    np.frombuffer(row[10], dtype=np.float32)
                    if row[10] else None
                ),
                maturity=row[11],
                example_context=row[12] or "",
                active=bool(row[13]),
            ))
        return entries

    def _fetch_scoreable(self) -> list:
        if not self._conn:
            return []
        return self._conn.execute(
            "SELECT id, term, normalized, frequency, idiolect_score, "
            "cluster_id, cluster_label, first_seen, last_seen, "
            "soma_used_count, embedding, maturity, example_context, active "
            "FROM vocab_idiolect "
            "WHERE active = 1 AND frequency >= ? "
            "ORDER BY idiolect_score DESC LIMIT ?",
            (MIN_OCCURRENCES, MAX_VOCAB_ENTRIES),
        ).fetchall()

    # ── DECAY: Nicht benutzte Terme verblassen ───────────────────────

    async def run_decay(self):
        """
        Aufruf im Dreaming-Cycle: Terme die lange nicht benutzt wurden
        verlieren Maturity. Unter Threshold → active = False.
        """
        if not self._conn:
            return
        loop = asyncio.get_event_loop()
        deactivated = await loop.run_in_executor(None, self._apply_decay)
        if deactivated > 0:
            logger.info("vocab_decay_applied", deactivated=deactivated)

    def _apply_decay(self) -> int:
        """Decay: Maturity reduzieren, alte Terme deaktivieren."""
        if not self._conn:
            return 0
        now = time.time()
        rows = self._conn.execute(
            "SELECT id, last_seen, maturity FROM vocab_idiolect WHERE active = 1"
        ).fetchall()

        deactivated = 0
        for vid, last_seen, maturity in rows:
            age_days = (now - last_seen) / 86400
            decay = 2.0 ** (-age_days / DECAY_HALFLIFE_DAYS)
            new_maturity = maturity * decay

            if new_maturity < 0.05:
                # Term ist so gut wie vergessen
                self._conn.execute(
                    "UPDATE vocab_idiolect SET active = 0, maturity = 0 "
                    "WHERE id = ?",
                    (vid,),
                )
                deactivated += 1
            elif abs(new_maturity - maturity) > 0.01:
                self._conn.execute(
                    "UPDATE vocab_idiolect SET maturity = ? WHERE id = ?",
                    (round(new_maturity, 3), vid),
                )

        self._conn.commit()
        return deactivated

    # ── PROMPT: Idiolekt-Block fuer System-Prompt ────────────────────

    async def get_idiolect_prompt_block(self) -> str:
        """
        Liefert den [SPRACHSTIL]-Block fuer den System-Prompt.
        Nur reife Terme (maturity >= 0.5, Alter >= MATURITY_DAYS).
        Gruppiert nach Cluster.

        Returns:
            Formatierter String oder "" wenn noch nichts gelernt.
        """
        terms = await self._get_mature_terms(min_maturity=0.5)
        if not terms:
            return ""

        # Nach Cluster gruppieren
        clustered: dict[str, list[VocabEntry]] = defaultdict(list)
        unclustered: list[VocabEntry] = []
        for t in terms:
            if t.cluster_label:
                clustered[t.cluster_label].append(t)
            else:
                unclustered.append(t)

        lines: list[str] = []
        user_name = get_user_name_sync()

        # Geclusterte Terme
        for label, members in sorted(
            clustered.items(), key=lambda x: -max(m.maturity for m in x[1]),
        ):
            term_strs = ", ".join(
                f'"{m.term}"' for m in sorted(members, key=lambda m: -m.maturity)[:4]
            )
            lines.append(f"- {label}: {term_strs}")

        # Einzelne reife Terme ohne Cluster
        for t in sorted(unclustered, key=lambda t: -t.maturity)[:5]:
            ctx_hint = ""
            if t.example_context:
                # Kurzer Kontext-Hinweis
                ctx_hint = f' (Kontext: "{t.example_context[:60]}")'
                if len(t.example_context) > 60:
                    ctx_hint = ctx_hint[:-1] + '...")'
            lines.append(f'- "{t.term}"{ctx_hint}')

        if not lines:
            return ""

        block = (
            f"[SPRACHSTIL — So spricht {user_name}]\n"
            f"{user_name} benutzt bestimmte Ausdruecke und Spitznamen.\n"
            f"Uebernimm diese natuerlich in deine Antworten wenn sie zum "
            f"Thema passen — nicht erzwingen:\n"
            + "\n".join(lines[:MAX_PROMPT_TERMS])
        )
        return block

    # ── CONTEXT: Themen-relevante Terme ──────────────────────────────

    async def get_relevant_vocab(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[VocabEntry]:
        """
        Liefert die zum aktuellen Thema passenden Nutzer-Terme.
        Verwendet Embedding-Similarity zum Query.
        """
        query_vec = await get_embedding_service().embed(query)
        if query_vec is None:
            return []

        entries = await self._get_mature_terms(min_maturity=0.3)
        if not entries:
            return []

        # Relevanz berechnen
        for e in entries:
            if e.embedding is not None:
                sim = max(0.0, float(np.dot(query_vec, e.embedding)))
            else:
                sim = 0.0
            # Gewichtung: 60% Similarity + 30% Maturity + 10% Frequenz
            freq_norm = min(1.0, math.log(1 + e.frequency) / math.log(30))
            e.maturity = 0.60 * sim + 0.30 * e.maturity + 0.10 * freq_norm

        entries.sort(key=lambda e: e.maturity, reverse=True)
        return [e for e in entries[:top_k] if e.maturity > 0.3]

    async def _get_mature_terms(
        self,
        min_maturity: float = 0.5,
    ) -> list[VocabEntry]:
        """Lade reife Terme aus der DB."""
        if not self._initialized:
            await self.initialize()
        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(
            None, self._fetch_mature, min_maturity,
        )
        entries = []
        for row in rows:
            entries.append(VocabEntry(
                id=row[0],
                term=row[1],
                normalized=row[2],
                frequency=row[3],
                idiolect_score=row[4],
                cluster_id=row[5],
                cluster_label=row[6] or "",
                first_seen=row[7],
                last_seen=row[8],
                soma_used_count=row[9],
                embedding=(
                    np.frombuffer(row[10], dtype=np.float32)
                    if row[10] else None
                ),
                maturity=row[11],
                example_context=row[12] or "",
            ))
        return entries

    def _fetch_mature(self, min_maturity: float) -> list:
        if not self._conn:
            return []
        return self._conn.execute(
            "SELECT id, term, normalized, frequency, idiolect_score, "
            "cluster_id, cluster_label, first_seen, last_seen, "
            "soma_used_count, embedding, maturity, example_context "
            "FROM vocab_idiolect "
            "WHERE active = 1 AND maturity >= ? "
            "ORDER BY maturity DESC LIMIT ?",
            (min_maturity, MAX_PROMPT_TERMS * 3),
        ).fetchall()

    # ── STATS ────────────────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        return {
            "feed_count": self._feed_count,
            "unique_terms": len(self._term_counter),
            "total_observations": self._total_terms,
            "top_terms": self._term_counter.most_common(10),
        }
