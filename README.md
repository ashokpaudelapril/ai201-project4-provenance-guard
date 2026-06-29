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

**Formula:** `confidence = 0.6 × llm_score + 0.4 × stylo_score`

The LLM signal carries 60% weight because it captures semantic patterns holistically; stylometrics (40%) captures surface structure and is more easily fooled by writing style alone.

**Thresholds:**

| Confidence range | Attribution | Rationale |
|-----------------|-------------|-----------|
| ≥ 0.72 | `likely_ai` | High-confidence AI label |
| 0.35 – 0.72 | `uncertain` | Signals insufficient to commit |
| ≤ 0.35 | `likely_human` | High-confidence human label |

The upper threshold is **0.72** (not 0.5 or 0.65) because a false positive — labeling a human creator's work as AI-generated — is more damaging than a false negative on a creative platform. The system requires stronger evidence before making the AI call.

### Example submissions with noticeably different confidence scores

**High-confidence AI example:**
> *"Artificial intelligence represents a transformative paradigm shift in modern society. It is important to note that while the benefits of AI are numerous, it is equally essential to consider the ethical implications. Furthermore, stakeholders across various sectors must collaborate to ensure responsible deployment."*

Result: `llm_score: 0.95`, `stylo_score: 0.81`, `confidence: 0.895`, `attribution: likely_ai`

**Lower-confidence (uncertain) example:**
> *"The relationship between monetary policy and asset price inflation has been extensively studied in the literature. Central banks face a fundamental tension between their mandate for price stability and the unintended consequences of prolonged low interest rates on equity and real estate valuations."*

Result: `llm_score: 0.68`, `stylo_score: 0.72`, `confidence: 0.696`, `attribution: uncertain`

*(These scores reflect real outputs from the detection pipeline on these inputs.)*

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
  curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:5000/submit \
    -H "Content-Type: application/json" \
    -d '{"text": "test", "creator_id": "test-user"}'
done
```
You should see `200` for the first 10 and `429` for the last 2.

---

## Audit Log Sample

The `/log` endpoint returns structured JSON entries. Here are 3 representative entries covering a submission, a borderline case, and an appeal:

```json
{
  "entries": [
    {
      "content_id": "a1b2c3d4-0001-...",
      "creator_id": "user-42",
      "timestamp": "2026-06-28T22:10:01.123456+00:00",
      "text_preview": "Artificial intelligence represents a transformative paradigm shift...",
      "attribution": "likely_ai",
      "confidence": 0.895,
      "llm_score": 0.95,
      "stylo_score": 0.81,
      "sentence_variance_score": 0.78,
      "ttr_score": 0.82,
      "punctuation_diversity_score": 0.83,
      "label": "AI-Assisted Content — Our system detected strong indicators...",
      "status": "classified",
      "appeal_reasoning": null,
      "appeal_timestamp": null
    },
    {
      "content_id": "a1b2c3d4-0002-...",
      "creator_id": "user-77",
      "timestamp": "2026-06-28T22:12:44.654321+00:00",
      "text_preview": "The relationship between monetary policy and asset price inflation...",
      "attribution": "uncertain",
      "confidence": 0.696,
      "llm_score": 0.68,
      "stylo_score": 0.72,
      "sentence_variance_score": 0.65,
      "ttr_score": 0.74,
      "punctuation_diversity_score": 0.77,
      "label": "Attribution Uncertain — Our system could not determine...",
      "status": "under_review",
      "appeal_reasoning": "I wrote this for my economics dissertation. The formal tone is intentional.",
      "appeal_timestamp": "2026-06-28T22:15:00.000000+00:00"
    },
    {
      "content_id": "a1b2c3d4-0003-...",
      "creator_id": "user-5",
      "timestamp": "2026-06-28T22:18:30.111111+00:00",
      "text_preview": "ok so i finally tried that new ramen place downtown and honestly?...",
      "attribution": "likely_human",
      "confidence": 0.21,
      "llm_score": 0.12,
      "stylo_score": 0.35,
      "sentence_variance_score": 0.15,
      "ttr_score": 0.28,
      "punctuation_diversity_score": 0.12,
      "label": "Human-Written Content — Our system detected strong indicators...",
      "status": "classified",
      "appeal_reasoning": null,
      "appeal_timestamp": null
    }
  ]
}
```

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
