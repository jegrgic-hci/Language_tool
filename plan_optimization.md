# Optimization Plan — spaCy + Mistral Small

## Goal
Reduce Mistral API costs ~75% by replacing the POS tagging call with local spaCy inference and switching conversational replies from mistral-large to mistral-small. Keep mistral-large only for paragraph generation where output quality and length demand it.

---

## Current call stack (per user message)
| Call | Model | Cost tier |
|---|---|---|
| Intent routing | mistral-small | Low |
| Coherence check | mistral-small | Low |
| POS tagging | mistral-small/large | Medium |
| Tutor reply | mistral-large | High |
| Paragraph generation | mistral-large | High (infrequent) |

---

## Target call stack
| Call | Model | Cost tier |
|---|---|---|
| Intent routing | mistral-small | Low |
| Coherence check | mistral-small | Low |
| POS tagging | spaCy (local) | Free |
| Tutor reply | mistral-small | Low |
| Paragraph generation | mistral-large | High (infrequent) |

---

## Change 1 — Install spaCy

### Local setup
```bash
cd /Users/josephgrgic/Documents/GitHub/Language_tool
source .venv/bin/activate
pip install spacy
python -m spacy download fr_core_news_sm
```

### Add to requirements.txt
```
spacy==3.x.x
https://github.com/explosion/spacy-models/releases/download/fr_core_news_sm-3.x.x/fr_core_news_sm-3.x.x.tar.gz
```

The model download must be in requirements.txt so Render installs it on deploy.

---

## Change 2 — Replace POS tagging Mistral call with spaCy

### What spaCy returns
```python
import spacy
nlp = spacy.load("fr_core_news_sm")

doc = nlp("Je vais au marché avec mon ami.")
for token in doc:
    print(token.text, token.pos_, token.lemma_, token.morph)
```

### POS tags available (relevant ones)
| spaCy tag | Meaning |
|---|---|
| `NOUN` | Noun |
| `VERB` | Verb |
| `ADJ` | Adjective |
| `ADV` | Adverb |
| `DET` | Determiner (le, la, les, un) |
| `ADP` | Preposition |
| `PRON` | Pronoun |
| `AUX` | Auxiliary verb (être, avoir) |

### Where to make the change
- Find the Mistral POS tagging call in `server.py` or `shadow_engine.py`
- Replace with a `tag_pos(text)` helper that loads spaCy and returns token/tag pairs
- Load the model once at startup, not per request:

```python
import spacy
nlp = spacy.load("fr_core_news_sm")  # load once at module level

def tag_pos(text: str) -> list[dict]:
    doc = nlp(text)
    return [{"word": t.text, "pos": t.pos_, "lemma": t.lemma_} for t in doc]
```

---

## Change 3 — Switch tutor reply to mistral-small

### Where to change
`tutor.py` — the `get_response()` function's model string.

### Test protocol before switching
1. Make the one-line change locally: `mistral-large-latest` → `mistral-small-latest`
2. Run 20–30 conversational turns across CHAT, SCENARIO, and DRILL modes
3. Check for: coherence, French accuracy, response length, instruction-following
4. If acceptable → keep the change. If noticeably worse → revert and keep large for SCENARIO/CHAT only

### Keep mistral-large for paragraph generation
Paragraph mode generates 5–7 paragraphs of structured French text with vocabulary tags. This is where model quality matters and where mistral-small was hitting limits. Keep `mistral-large-latest` for this call only.

---

## Change 4 — Leverage spaCy beyond POS tagging

These are not required for MVP but spaCy enables them for free:

### Lemmatization → practice list deduplication
```python
def get_lemma(word: str) -> str:
    doc = nlp(word)
    return doc[0].lemma_
```
Use this when adding words to the practice list — `allé` and `aller` map to the same lemma, preventing duplicates.

### Morphological gender detection → noun/article drills
```python
def get_gender(word: str) -> Optional[str]:
    doc = nlp(word)
    morph = doc[0].morph
    gender = morph.get("Gender")
    return gender[0] if gender else None  # "Masc" or "Fem"
```
Powers `le`/`la`/`les` drills without any API call.

### Better tokenization
spaCy's French tokenizer handles elisions natively. Could simplify `elision.py` or validate its output.

---

## Cost impact at 50 users

| | Before | After |
|---|---|---|
| Mistral costs | ~$40–50/mo | ~$8–12/mo |
| spaCy | $0 | $0 |
| Render | $7 | $7 |
| Postgres | $7 | $7 |
| Azure TTS | $5 | $5 |
| **Total** | **~$60–70/mo** | **~$27–32/mo** |
| **Revenue (40 paying @ $5)** | $200 | $200 |
| **Net profit** | ~$130/mo | **~$170/mo** |

---

## Implementation order
1. Install spaCy locally + test `fr_core_news_sm` loads correctly
2. Find POS tagging call in codebase
3. Replace with spaCy `tag_pos()` helper — test that elision/homophone rules still apply correctly
4. Switch tutor reply model to mistral-small — run test conversations
5. Validate paragraph generation stays on mistral-large
6. Update `requirements.txt` with spaCy + model URL
7. Push to Render — verify deploy succeeds with new dependencies
8. Monitor Mistral usage dashboard for first week

---

## Rollback plan
- Each change is independent — model string and POS tagging are separate code paths
- If mistral-small quality is unacceptable: revert `tutor.py` model string, no other changes needed
- If spaCy POS output breaks elision rules: revert the helper, restore Mistral call
