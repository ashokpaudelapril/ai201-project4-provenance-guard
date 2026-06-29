# Provenance Guard

A backend API for classifying whether submitted creative text is human-written or AI-generated. Built for content platforms that need attribution transparency — not to police creativity, but to give audiences the context they need.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate       # Mac/Linux
pip install -r requirements.txt
cp .env.example .env            # then add your GROQ_API_KEY
python app.py
```

---

## Architecture Overview

A submitted piece of text takes this path through the system:

```
POST /submit
  │
  ├─► Signal 1: Groq LLM (llama-3.3-70b-versatile) ──► llm_score (0–1)
  ├─► Signal 2: Stylometric heuristics (pure Python) ──► stylo_score (0–1)
  │
  ▼
Confidence scoring: 0.6 × llm_score + 0.4 × stylo_score = confidence
  │
  ▼
Transparency label (one of three variants based on confidence thresholds)
  │
  ▼
Audit log (SQLite) — writes content_id, both signal scores, label, status
  │
  ▼
JSON response → { content_id, attribution, confidence, label, llm_score, stylo_score }

POST /appeal
  │
  ├─► Validate content_id exists in audit log
  ├─► Set status = "under_review", append appeal_reasoning + timestamp
  └─► Return confirmation

GET /log → returns last N audit entries as JSON
```

The two signals are run on every submission. Their outputs are combined into a single confidence score, which maps to one of three transparency labels. Every decision — including any subsequent appeal — is written to the SQLite audit log before the response is sent.

---

## Detection Signals

### Signal 1: LLM-based classification (Groq)

**File:** [`signals/llm_signal.py`](signals/llm_signal.py)

**What it measures:** Semantic and stylistic coherence holistically. Sends the text to `llama-3.3-70b-versatile` with a structured prompt and asks it to return a JSON object with an `ai_probability` float (0–1). The LLM looks for: unnatural smoothness, over-balanced phrasing, hedged language, absence of personal voice, and mechanical enumeration of points.

**Why it differs between human and AI:** LLMs have macro-level writing patterns — comprehensive, contradiction-free, smoothly organized prose. Human writing has idiosyncratic voice, opinion, non-sequiturs, and emotional variation that is hard to simulate consistently.

**Output format:** Float 0–1 (1 = confident AI, 0 = confident human), clamped to [0, 1].

**What it misses:** Very short texts (<50 words) give the model too little signal. Heavily human-edited AI output can fool it. Formal human writing (academic papers, legal text) may score higher than expected because it deliberately mimics some LLM-like patterns of precision and completeness.

### Signal 3: Lexical sophistication (pure Python) — *Stretch: Ensemble Detection*

**File:** [`signals/lexical_signal.py`](signals/lexical_signal.py)

**What it measures:** Vocabulary complexity — whether the text skews toward formal, polished language (AI-like) or casual, everyday vocabulary (human-like). Two sub-scores:

| Sub-score | What it measures | Why it works |
|-----------|-----------------|--------------|
| Average word length | Mean character length of all words | AI favors longer, more formal words ("utilize" vs "use") |
| Long-word ratio | Fraction of words with 9+ characters | AI deploys more polysyllabic vocabulary than casual human writing |

**Output format:** Float 0–1 (1 = AI-like, 0 = human-like), average of two normalized sub-scores.

**What it misses:** Academic and legal human writing uses sophisticated vocabulary by necessity and will score AI-like. Intentionally casual AI output (prompted to "write informally") may score human-like. This signal is strongest for discriminating casual human text from default AI output.

### Signal 2: Stylometric heuristics (pure Python)

**File:** [`signals/stylometric.py`](signals/stylometric.py)

Three statistical sub-scores, each normalized to [0, 1] where 1 = AI-like:

| Sub-score | What it measures | Why it works |
|-----------|-----------------|--------------|
| Sentence-length variance | Variance of word counts across sentences | AI text is more uniform; human writing varies more |
| Type-token ratio (TTR) | Vocabulary diversity (unique words / total words) via sliding 50-word window | AI deploys vocabulary more mechanically; humans vary more |
| Punctuation diversity | Fraction of punctuation that is "expressive" (!, ?, —, …, ;) vs plain | Humans use expressive punctuation more; AI favors periods and commas |

Combined as: `stylo_score = (variance_score + ttr_score + punct_score) / 3`

**What it misses:** Formal human writing (academic/legal) has inherently low variance and may score AI-like. Very short texts (<50 words) produce unreliable variance estimates. Poems with intentional repetition (villanelles, ghazals) may score high due to structural repetition.

---

## Confidence Scoring

**Formula (Ensemble — Stretch Feature):** `confidence = 0.50 × llm_score + 0.30 × stylo_score + 0.20 × lexical_score`

| Signal | Weight | Rationale |
|--------|--------|-----------|
| LLM (Groq) | 50% | Captures semantic/holistic patterns — most reliable signal |
| Stylometric heuristics | 30% | Structural surface properties (sentence variance, TTR, punctuation) |
| Lexical sophistication | 20% | Vocabulary complexity (avg word length, long-word ratio) |

The LLM signal carries the most weight because it assesses meaning and context; the two surface signals capture orthogonal structural dimensions and together compensate for LLM blind spots on very short or heavily edited text.

**Thresholds:**

| Confidence range | Attribution | Rationale |
|-----------------|-------------|-----------|
| ≥ 0.72 | `likely_ai` | High-confidence AI label |
| 0.35 – 0.72 | `uncertain` | Signals insufficient to commit |
| ≤ 0.35 | `likely_human` | High-confidence human label |

The upper threshold is **0.72** (not 0.5 or 0.65) because a false positive — labeling a human creator's work as AI-generated — is more damaging than a false negative on a creative platform. The system requires stronger evidence before making the AI call.

### Example submissions with noticeably different confidence scores

**High-confidence AI example:**
> *"Artificial intelligence represents a transformative paradigm shift in modern society. It is important to note that while the benefits of AI are numerous, it is equally essential to consider the ethical implications. Furthermore, stakeholders across various sectors must collaborate to ensure responsible deployment. The intersection of machine learning and data analytics provides unprecedented opportunities for innovation across multiple domains."*

Result: `llm_score: 0.8`, `stylo_score: 0.6681`, `confidence: 0.7472`, `attribution: likely_ai`

**Clearly human example:**
> *"ok so i finally tried that new ramen place downtown and honestly? underwhelming. the broth was fine but they put WAY too much sodium in it and i was thirsty for like three hours after. my friend got the spicy version and said it was better. probably wont go back"*

Result: `llm_score: 0.2`, `stylo_score: 0.3046`, `confidence: 0.2418`, `attribution: likely_human`

*(These are real outputs from the detection pipeline on these exact inputs.)*

---

## Transparency Label

The label returned by `POST /submit` changes based on the confidence score. All three variants are shown below with their exact display text:

**High-confidence AI** (`confidence ≥ 0.72`, `attribution: likely_ai`):
> "AI-Assisted Content — Our system detected strong indicators of AI-generated text (confidence: {score}). This content may have been created with AI tools. The author can contest this classification via an appeal."

**Uncertain** (`0.35 < confidence < 0.72`, `attribution: uncertain`):
> "Attribution Uncertain — Our system could not determine with confidence whether this content is human- or AI-written (confidence: {score}). Signals were inconclusive. The author may contest this classification via an appeal."

**High-confidence human** (`confidence ≤ 0.35`, `attribution: likely_human`):
> "Human-Written Content — Our system detected strong indicators of human authorship (confidence: {score}). This content appears to have been written by a person."

*(The `{score}` placeholder is replaced with the actual rounded float at runtime.)*

---

## API Endpoints

### `POST /submit`

Classify a piece of text.

**Request:**
```json
{
  "text": "The content to classify...",
  "creator_id": "user-123"
}
```

**Response (200):**
```json
{
  "content_id": "3f7a2b1e-...",
  "attribution": "likely_ai",
  "confidence": 0.895,
  "label": "AI-Assisted Content — Our system detected ...",
  "llm_score": 0.95,
  "stylo_score": 0.81
}
```

Rate limited: **10 requests/minute, 100 requests/day** per IP. Returns 429 when exceeded.

### `POST /appeal`

Contest a classification. Use the `content_id` from your `/submit` response.

**Request:**
```json
{
  "content_id": "3f7a2b1e-...",
  "creator_reasoning": "I wrote this myself for my literature class..."
}
```

**Response (200):**
```json
{
  "message": "Appeal received. Your submission has been flagged for human review.",
  "content_id": "3f7a2b1e-...",
  "status": "under_review"
}
```

### `GET /analytics` *(Stretch: Analytics Dashboard)*

Returns detection patterns, appeal rate, and average signal scores across all submissions.

**Response (200):**
```json
{
  "total_submissions": 3,
  "attribution_counts": {
    "likely_ai": 1,
    "likely_human": 1,
    "uncertain": 1
  },
  "appeal_rate": 0.3333,
  "total_appeals": 1,
  "average_scores": {
    "confidence": 0.5563,
    "llm_score": 0.6,
    "stylo_score": 0.4909,
    "lexical_score": 0.512
  }
}
```

### `GET /log`

Return recent audit log entries. Optional `?limit=N` (max 100, default 20).

**Response (200):**
```json
{
  "entries": [
    {
      "content_id": "3f7a2b1e-...",
      "creator_id": "user-123",
      "timestamp": "2025-04-01T14:32:10.123Z",
      "text_preview": "Artificial intelligence represents...",
      "attribution": "likely_ai",
      "confidence": 0.895,
      "llm_score": 0.95,
      "stylo_score": 0.81,
      "sentence_variance_score": 0.78,
      "ttr_score": 0.82,
      "punctuation_diversity_score": 0.83,
      "label": "AI-Assisted Content — ...",
      "status": "classified",
      "appeal_reasoning": null,
      "appeal_timestamp": null
    }
  ]
}
```

---

## Rate Limiting

Applied to `POST /submit` via Flask-Limiter:
- **10 requests per minute** — A real creator might submit a few pieces in a burst (pasting, tweaking, resubmitting), but 10/minute is generous for legitimate use while catching simple script flooding.
- **100 requests per day** — Even a prolific creator on an active day wouldn't need more than 100 submissions. An adversary trying to profile the system or map its thresholds would hit this limit quickly.

These numbers are designed to be defensible, not arbitrary: they reflect realistic usage on a writing platform while preventing abuse without punishing legitimate creators.

To verify rate limiting is active, run 12 rapid requests while the server is running:
```bash
for i in $(seq 1 12); do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:5001/submit \
    -H "Content-Type: application/json" \
    -d '{"text": "test", "creator_id": "test-user"}'
done
```
You should see `200` for the first 10 and `429` for the last 2.

---

## Audit Log Sample

The `/log` endpoint returns structured JSON entries. Real output from `GET /log` covering all three attribution outcomes (including one appeal):

```json
{
  "entries": [
    {
      "content_id": "2ad4ddd1-20fb-4043-b0c5-72131c36b18d",
      "creator_id": "test-user-3",
      "timestamp": "2026-06-29T00:50:00.854206+00:00",
      "text_preview": "Artificial intelligence represents a transformative paradigm shift in modern society...",
      "attribution": "likely_ai",
      "confidence": 0.7472,
      "llm_score": 0.8,
      "stylo_score": 0.6681,
      "sentence_variance_score": 0.9244,
      "ttr_score": 0.08,
      "punctuation_diversity_score": 1.0,
      "label": "AI-Assisted Content — Our system detected strong indicators of AI-generated text (confidence: 0.75). This content may have been created with AI tools. The author can contest this classification via an appeal.",
      "status": "classified",
      "appeal_reasoning": null,
      "appeal_timestamp": null
    },
    {
      "content_id": "be427f6e-e4f2-46ae-b004-476629985ad4",
      "creator_id": "test-user-2",
      "timestamp": "2026-06-29T00:46:57.611584+00:00",
      "text_preview": "ok so i finally tried that new ramen place downtown and honestly? underwhelming...",
      "attribution": "likely_human",
      "confidence": 0.2418,
      "llm_score": 0.2,
      "stylo_score": 0.3046,
      "sentence_variance_score": 0.8337,
      "ttr_score": 0.08,
      "punctuation_diversity_score": 0.0,
      "label": "Human-Written Content — Our system detected strong indicators of human authorship (confidence: 0.24). This content appears to have been written by a person.",
      "status": "classified",
      "appeal_reasoning": null,
      "appeal_timestamp": null
    },
    {
      "content_id": "56c84b60-035d-4646-bcce-bb2a6a43aca7",
      "creator_id": "test-user-1",
      "timestamp": "2026-06-29T00:45:55.290236+00:00",
      "text_preview": "Artificial intelligence is transforming every industry today.",
      "attribution": "uncertain",
      "confidence": 0.68,
      "llm_score": 0.8,
      "stylo_score": 0.5,
      "sentence_variance_score": 0.5,
      "ttr_score": 0.0,
      "punctuation_diversity_score": 1.0,
      "label": "Attribution Uncertain — Our system could not determine with confidence whether this content is human- or AI-written (confidence: 0.68). Signals were inconclusive. The author may contest this classification via an appeal.",
      "status": "under_review",
      "appeal_reasoning": "I wrote this myself for my AI ethics class.",
      "appeal_timestamp": "2026-06-29T00:46:31.844846+00:00"
    }
  ]
}
---

## Known Limitations

**Formal human writing (academic/legal text) may be mislabeled as AI or uncertain.** The stylometric signal is calibrated against casual human writing. A human-written legal brief or PhD abstract will have low sentence-length variance, dense vocabulary deployment, and clean punctuation — all of which score AI-like. The LLM signal partially compensates (it can recognize authentic academic voice), but the combined score may still land in the "uncertain" range. The appeal pathway is the intended correction mechanism for this case.

---

## Spec Reflection

**One way the spec helped:** The spec's requirement to write out the three label variants *before* building the UI forced a concrete design decision early. Deciding what "uncertain" should say to a non-technical user — especially the acknowledgment that signals were inconclusive and the appeal path exists — shaped how the confidence score was designed. Without that upfront commitment, the label would have been an afterthought.

**One way implementation diverged from the spec:** The spec planned for both signals to run in parallel. In practice, the Groq API call introduces ~1-2 seconds of latency, and running the stylometric signal sequentially after it adds negligible time (stylometrics is pure Python, <1ms). True parallel execution (threading or async) was omitted to keep the code simple, since there's no observable UX difference at this scale. In a production system with many concurrent users, parallelism would matter.

---

## AI Usage

**Instance 1 — Groq LLM signal prompt engineering:**
I directed Claude to generate the system prompt for `llm_signal.py` that would produce a structured JSON response with an `ai_probability` field. The initial output used a verbose prompt that produced inconsistent JSON formatting. I revised it to add explicit instruction to not use markdown fences and to respond with only the JSON object, then added fallback stripping logic in the parser as a safety net.

**Instance 2 — Stylometric sub-score normalization:**
I asked Claude to implement the three stylometric heuristics and normalize them to [0,1]. The generated TTR normalization assumed TTR ranges from 0 to 1, which produced scores that were too concentrated near 0 for typical texts. I revised the normalization to use a [0.4, 0.9] realistic range and a sliding 50-word window to make TTR length-invariant — neither of which appeared in the initial generation.
