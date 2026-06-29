"""
Signal 2: Stylometric heuristics (pure Python, no external libraries).

Computes three measurable statistical properties that differ between
human and AI writing, then averages them into a single 0-1 score
(1 = AI-like, 0 = human-like).

Sub-scores:
  1. Sentence-length variance score — AI text is more uniform; human
     writing has higher variance in sentence lengths.
  2. Type-token ratio (TTR) score — vocabulary diversity. AI tends to
     re-deploy the same vocabulary more mechanically. Higher TTR = more
     human-like.
  3. Punctuation diversity score — humans use dashes, ellipses, question
     marks, and exclamation marks more liberally. AI tends toward cleaner,
     more uniform punctuation.

Blind spot: formal human writing (academic/legal) has low variance and
may score as AI-like. Very short texts (<50 words) produce unreliable
variance estimates.
"""

import re
import statistics


def _split_sentences(text: str) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return [s for s in sentences if s.strip()]


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\b[a-zA-Z']+\b", text.lower())


def _sentence_length_variance_score(sentences: list[str]) -> float:
    """
    Returns a score where 0 = very high variance (human-like) and
    1 = very low variance (AI-like).

    Normalize against a reference max variance of 400 word² (20-word stddev),
    which covers most realistic human-written text.
    """
    if len(sentences) < 2:
        return 0.5  # Not enough data — neutral

    lengths = [len(_tokenize(s)) for s in sentences]
    try:
        var = statistics.variance(lengths)
    except statistics.StatisticsError:
        return 0.5

    # A variance of ~400 corresponds to highly variable human writing.
    # Clamp to [0, 1] and invert so high variance → low (human) score.
    normalized = min(var / 400.0, 1.0)
    return 1.0 - normalized


def _ttr_score(tokens: list[str]) -> float:
    """
    Type-token ratio: unique_tokens / total_tokens.
    Higher TTR = more diverse vocabulary = more human-like.
    Returns a score where 0 = high TTR (human-like) and
    1 = low TTR (AI-like / repetitive).
    """
    if not tokens:
        return 0.5

    # Use a sliding window of 50 tokens to make TTR length-invariant.
    window = 50
    if len(tokens) < window:
        ttr = len(set(tokens)) / len(tokens)
    else:
        window_ttrs = []
        for i in range(0, len(tokens) - window + 1, window):
            chunk = tokens[i : i + window]
            window_ttrs.append(len(set(chunk)) / len(chunk))
        ttr = statistics.mean(window_ttrs)

    # TTR typically falls between 0.4 (repetitive) and 0.9 (very diverse).
    # Normalize to [0,1] where 1 = AI-like (low TTR).
    normalized = (ttr - 0.4) / 0.5  # scale so 0.4→1.0 becomes 0→1
    normalized = max(0.0, min(1.0, normalized))
    return 1.0 - normalized  # invert: low TTR → high AI score


def _punctuation_diversity_score(text: str) -> float:
    """
    Measures how uniform punctuation is.
    AI text tends to use periods/commas almost exclusively.
    Human text uses !, ?, —, …, ; more liberally.

    Returns 0 (diverse/human-like) to 1 (uniform/AI-like).
    """
    total_punct = len(re.findall(r"[^\w\s]", text))
    if total_punct == 0:
        return 0.5

    expressive = len(re.findall(r"[!?;:—–…\-]", text))
    diversity_ratio = expressive / total_punct

    # High expressive ratio → human-like → low AI score.
    # Normalize: ratio > 0.25 is fairly expressive.
    normalized = min(diversity_ratio / 0.25, 1.0)
    return 1.0 - normalized  # invert: expressive → low AI score


def score(text: str) -> float:
    """Return a 0-1 AI probability score using stylometric heuristics."""
    sentences = _split_sentences(text)
    tokens = _tokenize(text)

    s1 = _sentence_length_variance_score(sentences)
    s2 = _ttr_score(tokens)
    s3 = _punctuation_diversity_score(text)

    return round((s1 + s2 + s3) / 3.0, 4)


def score_with_breakdown(text: str) -> dict:
    """Same as score() but returns sub-scores for audit log transparency."""
    sentences = _split_sentences(text)
    tokens = _tokenize(text)

    s1 = _sentence_length_variance_score(sentences)
    s2 = _ttr_score(tokens)
    s3 = _punctuation_diversity_score(text)
    combined = round((s1 + s2 + s3) / 3.0, 4)

    return {
        "stylo_score": combined,
        "sentence_variance_score": round(s1, 4),
        "ttr_score": round(s2, 4),
        "punctuation_diversity_score": round(s3, 4),
        "sentence_count": len(sentences),
        "token_count": len(tokens),
    }
