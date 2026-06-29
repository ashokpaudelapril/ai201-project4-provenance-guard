"""
Provenance Guard — Flask API
Endpoints:
  POST /submit  — classify text content
  POST /appeal  — contest a classification
  GET  /log     — view recent audit log entries
"""

import uuid

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import audit
from signals import llm_signal, stylometric

load_dotenv()

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Rate limiting
# 10 submissions per minute prevents casual flooding.
# 100 submissions per day is generous for a real creator (most won't submit
# more than a handful of pieces per day) while blocking script-driven abuse.
# ---------------------------------------------------------------------------
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

# ---------------------------------------------------------------------------
# Confidence scoring thresholds
# Upper threshold is 0.72 (not 0.65) because false positives — labeling a
# human creator's work as AI — are more damaging than false negatives on a
# creative platform. We require stronger evidence to flag AI.
# ---------------------------------------------------------------------------
THRESHOLD_AI = 0.72
THRESHOLD_HUMAN = 0.35

# Weights for combining signals.
# LLM signal carries more weight (0.6) because it captures semantic patterns
# holistically; stylometrics (0.4) captures surface structure.
LLM_WEIGHT = 0.6
STYLO_WEIGHT = 0.4


def compute_confidence(llm_score: float, stylo_score: float) -> float:
    return round(LLM_WEIGHT * llm_score + STYLO_WEIGHT * stylo_score, 4)


def get_attribution(confidence: float) -> str:
    if confidence >= THRESHOLD_AI:
        return "likely_ai"
    if confidence <= THRESHOLD_HUMAN:
        return "likely_human"
    return "uncertain"


def get_label(attribution: str, confidence: float) -> str:
    score_display = round(confidence, 2)
    if attribution == "likely_ai":
        return (
            f"AI-Assisted Content — Our system detected strong indicators of "
            f"AI-generated text (confidence: {score_display}). This content may "
            f"have been created with AI tools. The author can contest this "
            f"classification via an appeal."
        )
    if attribution == "likely_human":
        return (
            f"Human-Written Content — Our system detected strong indicators of "
            f"human authorship (confidence: {score_display}). This content appears "
            f"to have been written by a person."
        )
    return (
        f"Attribution Uncertain — Our system could not determine with confidence "
        f"whether this content is human- or AI-written (confidence: {score_display}). "
        f"Signals were inconclusive. The author may contest this classification "
        f"via an appeal."
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    creator_id = (data.get("creator_id") or "").strip()

    if not text:
        return jsonify({"error": "text is required"}), 400
    if not creator_id:
        return jsonify({"error": "creator_id is required"}), 400

    content_id = str(uuid.uuid4())

    # Run both signals
    llm_score = llm_signal.score(text)
    stylo_breakdown = stylometric.score_with_breakdown(text)
    stylo_score = stylo_breakdown["stylo_score"]

    confidence = compute_confidence(llm_score, stylo_score)
    attribution = get_attribution(confidence)
    label = get_label(attribution, confidence)

    audit.log_submission(
        content_id=content_id,
        creator_id=creator_id,
        text=text,
        attribution=attribution,
        confidence=confidence,
        llm_score=llm_score,
        stylo_breakdown=stylo_breakdown,
        label=label,
    )

    return jsonify(
        {
            "content_id": content_id,
            "attribution": attribution,
            "confidence": confidence,
            "label": label,
            "llm_score": round(llm_score, 4),
            "stylo_score": stylo_score,
        }
    ), 200


@app.route("/appeal", methods=["POST"])
def appeal():
    data = request.get_json(silent=True) or {}
    content_id = (data.get("content_id") or "").strip()
    reasoning = (data.get("creator_reasoning") or "").strip()

    if not content_id:
        return jsonify({"error": "content_id is required"}), 400
    if not reasoning:
        return jsonify({"error": "creator_reasoning is required"}), 400

    updated = audit.log_appeal(content_id, reasoning)
    if not updated:
        return jsonify({"error": "content_id not found"}), 404

    return jsonify(
        {
            "message": "Appeal received. Your submission has been flagged for human review.",
            "content_id": content_id,
            "status": "under_review",
        }
    ), 200


@app.route("/log", methods=["GET"])
def log():
    limit = min(int(request.args.get("limit", 20)), 100)
    entries = audit.get_recent_entries(limit=limit)
    return jsonify({"entries": entries}), 200


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    audit.init_db()
    app.run(debug=True, port=5001)
