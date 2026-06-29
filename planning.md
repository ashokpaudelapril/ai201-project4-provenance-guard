# Provenance Guard — Planning Document

## Architecture Narrative

A piece of text enters the system via `POST /submit`. It carries a `creator_id` and raw `text`. The API immediately generates a unique `content_id` (UUID4) so every downstream component can refer to this specific submission.

The text is then sent through two independent detection signals **in parallel**:

1. **LLM Signal (Groq)** — The text is sent to `llama-3.3-70b-versatile` with a structured prompt asking it to assess whether the writing reads as AI-generated or human-written. The model returns a probability (0–1) where 1 = confident AI, 0 = confident human.

2. **Stylometric Signal (pure Python)** — Three measurable statistical properties of the text are computed:
   - **Sentence-length variance**: AI text tends to be more uniform; human writing is more variable.
   - **Type-token ratio (TTR)**: vocabulary diversity — humans reuse words less mechanically.
   - **Punctuation density**: AI tends toward cleaner punctuation patterns; humans use dashes, ellipses, etc. more.
   These three sub-scores are averaged into a single stylometric score (0–1, 1 = AI-like).

The two signal scores are combined into a **combined confidence score** using a weighted average (60% LLM, 40% stylometric). This weight reflects that the LLM signal is semantic and more holistic, while stylometrics is structural and can miss context.

The confidence score is mapped to a **transparency label** via thresholds:
- `≥ 0.72` → "Likely AI-generated"
- `≤ 0.35` → "Likely human-written"  
- Between → "Uncertain"

The thresholds are deliberately asymmetric: a false positive (labeling a human's work as AI) is more damaging on a creative platform than a false negative, so we require higher confidence to call something AI.

Every submission — including content_id, scores, label, and status — is written to a **structured SQLite audit log** before the response is returned.

The response to `POST /submit` includes: `content_id`, `attribution`, `confidence`, `label`, `llm_score`, `stylo_score`.

When a creator believes they've been misclassified, they call `POST /appeal` with their `content_id` and `creator_reasoning`. The appeal endpoint sets the submission's status to `"under_review"`, appends the reasoning to the audit log entry, and returns a confirmation. No automated re-classification occurs.

`GET /log` returns the most recent audit log entries as structured JSON, surfacing both individual signal scores and any appeal information for transparency.

---

## Architecture

```
POST /submit
  │
  ├─► [Signal 1: Groq LLM] ──► llm_score (0–1)
  │                                          │
  ├─► [Signal 2: Stylometrics] ── stylo_score (0–1)
  │                                          │
  │   raw_text, creator_id                  │
  │                                          ▼
  │                              [Confidence Scoring]
  │                              combined = 0.6*llm + 0.4*stylo
  │                                          │
  │                                          ▼
  │                              [Transparency Label]
  │                              ≥0.72 → "Likely AI-generated"
  │                              ≤0.35 → "Likely human-written"
  │                              else  → "Uncertain"
  │                                          │
  │                                          ▼
  │                              [Audit Log (SQLite)]
  │                              writes: content_id, creator_id,
  │                              timestamp, attribution, confidence,
  │                              llm_score, stylo_score, status
  │                                          │
  └──────────────────────────────────────────▼
                         JSON Response: {content_id, attribution,
                                         confidence, label,
                                         llm_score, stylo_score}

POST /appeal
  │
  ├─ input: {content_id, creator_reasoning}
  │
  ├─► [Audit Log] ── update status → "under_review"
  │                   append appeal_reasoning
  │
  └─► JSON Response: {message: "Appeal received", content_id, status}

GET /log
  │
  └─► [Audit Log] ── return last N entries as JSON
```

**Submission flow:** `POST /submit` → Signal 1 (Groq) → Signal 2 (stylometrics) → confidence scoring → transparency label → audit log write → JSON response.

**Appeal flow:** `POST /appeal` → status update in audit log → appeal reasoning logged → confirmation response.

---

## Detection Signals

### Signal 1: LLM-based classification (Groq)
- **What it measures:** Semantic and stylistic coherence holistically — does the writing "feel" AI-generated? Captures things like hedged language patterns, unnaturally smooth transitions, overly balanced sentence structure.
- **Why it differs between human and AI:** LLMs have distinctive macro-level writing patterns: they tend to be thorough, avoid contradiction, and produce well-organized prose. Human writing has more idiosyncratic voice, non-sequiturs, and emotional variation.
- **Output:** Float 0–1 (1 = AI, 0 = human), extracted from a JSON-structured model response.
- **Blind spot:** Cannot handle very short texts (<50 words) reliably. Will also struggle with AI-written text that has been heavily edited by a human, or human writing that is deliberately formal and polished (legal documents, academic abstracts).

### Signal 2: Stylometric heuristics (pure Python)
- **What it measures:** Statistical surface properties of the text — sentence length variance, type-token ratio, punctuation density.
- **Why it differs:** AI text is more statistically uniform: sentences tend to be similar in length, vocabulary is deployed more evenly, punctuation is cleaner. Human writing has higher variance across all these dimensions.
- **Output:** Float 0–1 (1 = AI-like, 0 = human-like), computed as average of 3 sub-scores.
- **Blind spot:** Formal human writing (academic papers, legal text) scores as AI-like due to its own uniformity. Casual AI output with injected typos or informal phrasing may score as human-like. Short texts produce unreliable variance estimates.

---

## Uncertainty Representation

A confidence score of 0.6 means: the system sees more AI indicators than human indicators, but not by a large enough margin to commit. 

**Mapping:**
- Raw scores from each signal are already 0–1 (1 = AI).
- Combined score: `confidence = 0.6 * llm_score + 0.4 * stylo_score`
- Thresholds:
  - `confidence ≥ 0.72` → `attribution = "likely_ai"` → high-confidence AI label
  - `confidence ≤ 0.35` → `attribution = "likely_human"` → high-confidence human label
  - `0.35 < confidence < 0.72` → `attribution = "uncertain"` → uncertain label

The asymmetric upper threshold (0.72 rather than 0.65) reflects the false-positive problem: we want higher evidence before labeling a human creator's work as AI-generated.

---

## Transparency Label Design

Three variants with exact display text:

**High-confidence AI** (`confidence ≥ 0.72`):
> "AI-Assisted Content — Our system detected strong indicators of AI-generated text (confidence: {score}). This content may have been created with AI tools. The author can contest this classification via an appeal."

**Uncertain** (`0.35 < confidence < 0.72`):
> "Attribution Uncertain — Our system could not determine with confidence whether this content is human- or AI-written (confidence: {score}). Signals were inconclusive. The author may contest this classification via an appeal."

**High-confidence human** (`confidence ≤ 0.35`):
> "Human-Written Content — Our system detected strong indicators of human authorship (confidence: {score}). This content appears to have been written by a person."

*(The `{score}` placeholder is replaced with the actual rounded float at runtime.)*

---

## Appeals Workflow

- **Who can appeal:** Any creator who submitted content (identified by `content_id` from their `/submit` response).
- **What they provide:** `content_id` (required) + `creator_reasoning` (free-text, required).
- **What the system does:**
  1. Looks up the audit log entry by `content_id`.
  2. Sets `status` to `"under_review"`.
  3. Appends `appeal_reasoning` and `appeal_timestamp` to the log entry.
  4. Returns a confirmation with the updated status.
- **What a human reviewer sees:** The `GET /log` endpoint exposes `appeal_reasoning` and `appeal_timestamp` alongside the original classification data — giving a reviewer full context to make a manual decision.
- Automated re-classification is **not** implemented.

---

## Anticipated Edge Cases

1. **Formal human writing (academic/legal text):** A human-written PhD abstract or legal brief will have low sentence-length variance and high uniformity — the stylometric signal may score this as AI-like, pushing the combined score up. The LLM signal should partially counteract this (it can detect authentic academic voice), but the system may produce an "uncertain" label. The appeal pathway is the safety valve here.

2. **Very short text (<50 words):** Stylometric variance metrics are unreliable on short inputs — there aren't enough sentences to compute meaningful variance. The LLM signal also loses reliability. Both signals will produce mid-range scores, pushing the combined score toward "uncertain." The label will honestly reflect this.

3. **Heavily edited AI output:** If a human substantially rewrites AI-generated text (changing sentence structure, adding personal anecdotes, varying rhythm), the stylometric signal will drift toward human-like and the LLM signal may also score it lower. The system may under-flag this. This is a known limitation — the system should not claim to be a ground-truth detector.

4. **Poems with intentional repetition:** Certain poetic forms (villanelles, ghazals) have structural repetition that raises the stylometric AI-score. The LLM signal should recognize poetic form and score it as human, but the combination may still produce "uncertain." Documented as a known limitation.

---

## API Surface

| Endpoint | Method | Request Body | Response |
|----------|--------|-------------|----------|
| `/submit` | POST | `{text: str, creator_id: str}` | `{content_id, attribution, confidence, label, llm_score, stylo_score}` |
| `/appeal` | POST | `{content_id: str, creator_reasoning: str}` | `{message, content_id, status}` |
| `/log` | GET | — | `{entries: [...]}` |

**Rate limiting on `/submit`:** 10 requests/minute, 100 requests/day per IP.

---

## AI Tool Plan

### M3 — Submission endpoint + first signal
- **Spec sections provided:** Detection signals section + Architecture diagram
- **What to ask AI to generate:** Flask app skeleton with `POST /submit` route stub (accepts `text` + `creator_id`, returns hardcoded response) + the Groq LLM signal function (sends text to Groq, parses structured JSON response into a 0–1 float)
- **Verification:** Call the function directly with 3 test inputs (clearly AI, clearly human, borderline) and inspect the raw float before wiring into the endpoint

### M4 — Second signal + confidence scoring
- **Spec sections provided:** Detection signals section + Uncertainty representation section + Architecture diagram
- **What to ask AI to generate:** Stylometric heuristic function (sentence-length variance, TTR, punctuation density → combined 0–1 score) + confidence scoring function (weighted average with documented thresholds)
- **What to check:** Run the 4 test inputs from the project spec through both signals; verify that clearly AI-generated text scores higher than clearly human text; check that combined scores fall in expected ranges for each threshold bucket

### M5 — Production layer
- **Spec sections provided:** Transparency label design + Appeals workflow section + Architecture diagram
- **What to ask AI to generate:** Label generation function (maps confidence float → label text with score interpolated) + `POST /appeal` endpoint (validates content_id exists, updates SQLite row, returns confirmation)
- **Verification:** Test that submitting clearly AI text produces the AI label variant; submitting clearly human text produces the human label variant; submitting a borderline input produces the uncertain variant; filing an appeal on a known content_id updates the log entry's status to "under_review"
