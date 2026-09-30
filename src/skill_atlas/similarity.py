"""Similar skills inside one snapshot (SPEC section 5.7).

Two lexical signals, both deterministic and computed without a model or network:

- topic: cosine of TF-IDF vectors over stemmed words of the name, description and body.
  IDF comes from the snapshot's own skills, so words every skill uses weigh little.
- overlap: the share of a skill's body (as 5-word shingles) that the other skill's body
  has word for word. It finds copy-edited partial duplicates, including a short skill that
  a longer one contains.

`score = max(topic, overlap)`: either signal alone is enough to call two skills similar.
The score is symmetric. Identical copies (same `views.dup_key`) are one entry.

This is lexical, not semantic in the embedding sense: two skills that describe the same
task in different words score low. Skill duplicates in practice share domain terms and
copied passages, which both signals catch.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from hashlib import blake2b

from skill_atlas.model import Skill, Snapshot
from skill_atlas.views import dup_key

THRESHOLD = 0.4
SHINGLE = 5
MIN_SHINGLES = 20  # below this, a shared passage is too short to mean anything
MAX_TERMS = 200  # per vector; the tail of rare-in-doc words changes cosines by little
FIELD_WEIGHTS = {"name": 3.0, "description": 2.0, "body": 1.0}

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_CAMEL_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|[0-9]+")
_ACRONYMS_RE = re.compile(r"[A-Z]{2,}s")  # APIs, URLs: one word, not AP + Is
_STOP_WORDS = """a about above after again against all also am an and any are as at be because been
    before being below between both but by can could did do does doing down during each
    either else etc few for from further get got had has have having he her here hers him
    his how i if in into is it its itself just me more most must my no nor not now of off
    on once only or other our ours out over own same she should so some such than that the
    their them then there these they this those through to too under until up us very was
    we were what when where which while who whom why will with would you your yours e g
    ie eg via may might"""
_STOP = frozenset(_STOP_WORDS.split())


@lru_cache(maxsize=65536)
def _stem(word: str) -> str:
    """A light suffix stripper: plurals and common verb endings only."""
    for suffix, repl in (("ies", "y"), ("sses", "ss"), ("ing", ""), ("ed", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + repl
    if word.endswith("s") and not word.endswith(("ss", "us", "is")) and len(word) > 3:
        return word[:-1]
    return word


def words(text: str) -> list[str]:
    """Lowercase words; identifiers split on case changes (`GradleBuild` -> gradle, build)."""
    out: list[str] = []
    for token in _WORD_RE.findall(text):
        if token.islower() or _ACRONYMS_RE.fullmatch(token):
            out.append(token.lower())
        else:
            out.extend(p.lower() for p in _CAMEL_RE.findall(token))
    return out


def terms(text: str) -> list[tuple[str, str]]:
    """(stem, word) pairs without stop words."""
    return [(_stem(w), w) for w in words(text) if len(w) > 1 and w not in _STOP]


def _shingles(text: str) -> frozenset[int]:
    ws = [w for w in words(text) if len(w) > 1]
    return frozenset(
        int.from_bytes(blake2b(" ".join(ws[i : i + SHINGLE]).encode(), digest_size=8).digest())
        for i in range(len(ws) - SHINGLE + 1)
    )


def percent(x: float) -> str:
    """Rounded down, so a 99.6% match never reads as 100%."""
    return f"{math.floor(x * 100 + 1e-9)}%"


def level(score: float) -> str:
    if score >= 0.9:
        return "near-identical"
    if score >= 0.6:
        return "strong"
    return "related"


@dataclass(frozen=True)
class Match:
    skill: Skill
    score: float
    topic: float
    # Share of the queried skill's text found in `skill`, and the reverse.
    overlap_here: float
    overlap_there: float
    shared_terms: tuple[str, ...]
    copies: int  # identical copies of `skill` folded into this match

    @property
    def level(self) -> str:
        return level(self.score)


Key = tuple[str | None, ...]


class Index:
    """Vectors and shingles for every skill with content in one snapshot.

    Building costs about 0.4 s for 300 skills; a query is one pass over the vectors.
    """

    def __init__(self, snap: Snapshot) -> None:
        self.groups: dict[Key, list[Skill]] = {}
        for s in snap.skills:
            if s.category == "external":
                continue  # placeholders: no content
            self.groups.setdefault(dup_key(s), []).append(s)
        counts: dict[Key, dict[str, float]] = {}
        df: dict[str, int] = {}
        # The most frequent word for each stem, to show "enabled" rather than "enabl".
        surface: dict[str, dict[str, int]] = {}
        self.shingles: dict[Key, frozenset[int]] = {}
        for key, (s, *_) in self.groups.items():
            tf: dict[str, float] = {}
            fields = (("name", s.name), ("description", s.description), ("body", s.body))
            for field_name, text in fields:
                weight = FIELD_WEIGHTS[field_name]
                for stem, word in terms(text or ""):
                    tf[stem] = tf.get(stem, 0.0) + weight
                    forms = surface.setdefault(stem, {})
                    forms[word] = forms.get(word, 0) + 1
            counts[key] = tf
            for stem in tf:
                df[stem] = df.get(stem, 0) + 1
            # The body only: a differing description should not hide a copied body.
            self.shingles[key] = _shingles(s.body or "")
        self.surface = {
            stem: min(forms.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            for stem, forms in surface.items()
        }
        n = len(self.groups)
        self.vectors: dict[Key, dict[str, float]] = {}
        for key, tf in counts.items():
            weights = {
                t: (1 + math.log(c)) * (math.log((1 + n) / (1 + df[t])) + 1) for t, c in tf.items()
            }
            top = sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_TERMS]
            norm = math.sqrt(sum(w * w for _, w in top)) or 1.0
            self.vectors[key] = {t: w / norm for t, w in top}

    def similar(self, skill: Skill, threshold: float = THRESHOLD) -> list[Match]:
        """Skills at or above `threshold`, most similar first. Excludes identical copies."""
        key = dup_key(skill)
        if key not in self.vectors:
            return []
        vec, sh = self.vectors[key], self.shingles[key]
        out: list[Match] = []
        for other, ovec in self.vectors.items():
            if other == key:
                continue
            small, large = (vec, ovec) if len(vec) <= len(ovec) else (ovec, vec)
            contrib = {t: w * large[t] for t, w in small.items() if t in large}
            topic = min(1.0, sum(contrib.values()))
            osh = self.shingles[other]
            common = len(sh & osh)
            here = common / len(sh) if sh else 0.0
            there = common / len(osh) if osh else 0.0
            overlap = max(here, there) if min(len(sh), len(osh)) >= MIN_SHINGLES else 0.0
            score = max(topic, overlap)
            if score < threshold:
                continue
            ranked = sorted(contrib.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
            shared = tuple(self.surface.get(t, t) for t, _ in ranked)
            group = self.groups[other]
            out.append(Match(group[0], score, topic, here, there, shared, len(group) - 1))
        out.sort(key=lambda m: (-m.score, m.skill.name or "", m.skill.id))
        return out
