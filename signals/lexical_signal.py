"""
Signal 3: Lexical sophistication (pure Python).

Measures vocabulary complexity — specifically whether the text leans toward
formal, polished language (AI-like) or casual, everyday vocabulary (human-like).

Two sub-scores:
  1. Average word length — AI writing tends toward slightly longer, more
     formal words ("utilize" vs "use", "endeavor" vs "try").
  2. Long-word ratio — fraction of words with 9+ characters. AI text at
     most formality levels deploys more polysyllabic vocabulary than casual
     human writing.

These measure vocabulary choice, which is genuinely orthogonal to:
  - Sentence-length variance (sentence structure, not word choice)
  - Type-token ratio (vocabulary diversity, not word complexity)
  - LLM signal (semantic coherence, not surface word length)

Blind spot: formal human writing (academic papers, legal text) uses
sophisticated vocabulary by necessity and will score AI-like. This is
a documented limitation in planning.md and README.
"""

import re


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\b[a-zA-Z']+\b", text.lower())


def _avg_word_length_score(tokens: list[str]) -> float:
    """
    Average word length normalized to [0, 1] where 1 = AI-like (longer words).

    Observed ranges:
      Casual human text:  avg ~4.0–5.0 chars
      AI / formal text:   avg ~5.0–7.0 chars
    Normalize [4, 7] → [0, 1].
    """
    if not tokens:
        return 0.5

    avg_len = sum(len(t) for t in tokens) / len(tokens)
    normalized = (avg_len - 4.0) / 3.0
    return max(0.0, min(1.0, normalized))


def _long_word_ratio_score(tokens: list[str]) -> float:
    """
    Fraction of words with 9+ characters, normalized to [0, 1].

    Observed ranges:
      Casual human text:  ~0.03–0.08 (3–8 % long words)
      AI / formal text:   ~0.10–0.22 (10–22 % long words)
    Normalize [0, 0.25] → [0, 1].
    """
    if not tokens:
        return 0.5

    long_word_count = sum(1 for t in tokens if len(t) >= 9)
    ratio = long_word_count / len(tokens)
    return max(0.0, min(1.0, ratio / 0.25))


def score(text: str) -> float:
    """Return a 0-1 AI probability score using lexical sophistication."""
    tokens = _tokenize(text)
    s1 = _avg_word_length_score(tokens)
    s2 = _long_word_ratio_score(tokens)
    return round((s1 + s2) / 2.0, 4)


def score_with_breakdown(text: str) -> dict:
    tokens = _tokenize(text)
    s1 = _avg_word_length_score(tokens)
    s2 = _long_word_ratio_score(tokens)
    combined = round((s1 + s2) / 2.0, 4)
    avg_len = round(sum(len(t) for t in tokens) / len(tokens), 2) if tokens else 0
    long_count = sum(1 for t in tokens if len(t) >= 9)

    return {
        "lexical_score": combined,
        "avg_word_length_score": round(s1, 4),
        "long_word_ratio_score": round(s2, 4),
        "avg_word_length": avg_len,
        "long_word_count": long_count,
        "token_count": len(tokens),
    }
