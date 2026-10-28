"""Knowledge base — the agent answers from uploaded files, not guesswork.

Vapi's Knowledge Base capability, rebuilt on our own stack with no Vapi and no
hosted vector service. A file is parsed to text, split into chunks, and at reply
time the chunks most relevant to the question are pulled with BM25 and pasted
into the prompt as reference material.

BM25 (rank-bm25) is deliberate over embeddings: it is a few kilobytes of pure
Python, needs no model download or GPU, and for a shop's price list or FAQ —
where the customer's words overlap the document's words — keyword ranking is
both faster and perfectly adequate. Swap in embeddings only if semantic recall
ever proves necessary.
"""

import csv
import io
import logging
import os
import re

import db

log = logging.getLogger("voiceflow.kb")

# ── Semantic search ──────────────────────────────────────────────────────
# A multilingual embedding model turns keyword matching into meaning matching:
# a Hindi question retrieves an English answer, "how many faculties" ranks the
# same block as "faculty count", and misspelled/verbose queries still land. It
# runs on the already-installed onnxruntime (fastembed, no PyTorch). If the model
# can't load, everything degrades to the BM25 path below — nothing breaks.
EMBED_MODEL = os.getenv("KB_EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
_embedder = None
_embed_unavailable = False


def _get_embedder():
    global _embedder, _embed_unavailable
    if _embedder is None and not _embed_unavailable:
        try:
            from fastembed import TextEmbedding
            _embedder = TextEmbedding(model_name=EMBED_MODEL)
            log.info("KB embedding model loaded: %s", EMBED_MODEL)
        except Exception as e:  # noqa: BLE001 - any failure falls back to BM25
            _embed_unavailable = True
            log.warning("KB embedding model unavailable (%s) — using BM25 only", e)
    return _embedder


def _embed(texts: list[str]):
    """L2-normalized embeddings (dot product == cosine), or None if unavailable."""
    emb = _get_embedder()
    if emb is None:
        return None
    import numpy as np
    vecs = np.asarray(list(emb.embed(list(texts))), dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vecs / norms


def embeddings_ready() -> bool:
    return _get_embedder() is not None


# ── Agentic / Corrective retrieval helpers ───────────────────────────────
# Cosine below this on the top semantic hit means "weak" — the corrective step
# reformulates the query and retries instead of trusting a poor match.
WEAK_HIT = 0.45
# Below this, with no keyword overlap either, the best chunk is unrelated —
# measured: off-topic queries score 0.11-0.22, real ones ~0.40. Return nothing;
# the agent's search_knowledge tool can still look again with a better query.
MIN_SIM = float(os.getenv("KB_MIN_SIM") or 0.30)

# Devanagari → Latin (Hunterian-ish) for the CORRECTIVE step. Proper names are
# transliterations, not translations, so the embedding model never aligns
# "मनीष दीक्षित" with "Manish Dixit". Romanizing the query and re-retrieving is
# what recovers those — validated: "मनीष दीक्षित"→"manish dikshit" finds the
# right person. Rough is fine; it feeds fuzzy semantic + keyword search, not a UI.
_ROM_C = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "च": "ch", "छ": "chh", "ज": "j",
    "झ": "jh", "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t",
    "थ": "th", "द": "d", "ध": "dh", "न": "n", "प": "p", "फ": "ph", "ब": "b",
    "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
    "ष": "sh", "स": "s", "ह": "h", "ड़": "r", "फ़": "f", "ज़": "z",
}
_ROM_V = {"ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "े": "e", "ै": "ai",
          "ो": "o", "ौ": "au", "ृ": "ri", "ॉ": "o", "ॅ": "e", "ऑ": "o"}
# Romanized filler words to drop so the CORRECTIVE query keeps only the name /
# distinctive term ("doctor pooja mishra ke bare men batao" → "pooja mishra").
_ROM_STOP = {
    "doctor", "doktor", "doktar", "daktar", "dr", "sir", "sar", "professor",
    "prof", "madam", "mam", "kaun", "kon", "kaunsa", "hai",
    "hain", "ke", "ka", "ki", "ko", "se", "men", "mein", "bare", "baare",
    "batao", "bata", "bataye", "mujhe", "aur", "ya", "kya", "ye", "vo", "wo",
    "naam", "nam", "about", "who", "is", "the", "of", "tell", "me", "ke",
}


def _refine_roman(text: str) -> str:
    kept = [w for w in text.split() if w not in _ROM_STOP and len(w) > 1]
    return " ".join(kept) if kept else text
_ROM_IND = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo",
            "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au"}
_ROM_NAS = {"ं": "n", "ँ": "n", "ः": "h"}


def _has_devanagari(s: str) -> bool:
    return any("ऀ" <= ch <= "ॿ" for ch in s)


def _romanize(text: str) -> str:
    """Rough Devanagari→Latin with word-final schwa deletion (मनीष→manish)."""
    words = []
    for w in text.split():
        out = []
        for j, ch in enumerate(w):
            if ch in _ROM_C:
                nxt = w[j + 1] if j + 1 < len(w) else ""
                out.append(_ROM_C[ch] + ("" if nxt in _ROM_V or nxt == "्" else "a"))
            elif ch in _ROM_IND:
                out.append(_ROM_IND[ch])
            elif ch in _ROM_V:
                out.append(_ROM_V[ch])
            elif ch in _ROM_NAS:
                out.append(_ROM_NAS[ch])
            elif ch == "्":
                pass
            else:
                out.append(ch)
        s = "".join(out)
        if s.endswith("a") and w and w[-1] in _ROM_C:  # schwa deletion
            s = s[:-1]
        words.append(s)
    return " ".join(words)


def _semantic_scored(agent_id, query: str, k: int):
    """[(text, cosine)] top-k, or None if no embeddings/model available."""
    rows = db.kb_chunks_with_embeddings(agent_id)
    embedded = [(t, e) for t, e in rows if e]
    if not embedded:
        return None
    qv = _embed([query])
    if qv is None:
        return None
    import numpy as np
    mat = np.stack([np.frombuffer(e, dtype=np.float32) for _, e in embedded])
    sims = mat @ qv[0]
    order = np.argsort(-sims)[:k]
    return [(embedded[i][0], float(sims[i])) for i in order]


def _bm25_ranked(chunks: list[str], query: str, k: int) -> list[str]:
    q_tokens = _tokenize(query)
    q_set = set(q_tokens)
    tokenized = [_tokenize(c) for c in chunks]
    try:
        from rank_bm25 import BM25Okapi
        bm25 = BM25Okapi(tokenized, b=0.25)
        scores = bm25.get_scores(q_tokens)
    except ImportError:
        import math
        corpus_size = len(tokenized)
        avgdl = sum(len(doc) for doc in tokenized) / corpus_size if corpus_size else 1.0
        doc_freqs = []
        nd = {}
        for doc in tokenized:
            freq = {}
            for w in doc:
                freq[w] = freq.get(w, 0) + 1
            doc_freqs.append(freq)
            for w in freq:
                nd[w] = nd.get(w, 0) + 1
        scores = [0.0] * corpus_size
        k1 = 1.5
        b = 0.25
        for q in q_tokens:
            if q not in nd:
                continue
            idf = math.log((corpus_size - nd[q] + 0.5) / (nd[q] + 0.5) + 1)
            for i, freq in enumerate(doc_freqs):
                if q not in freq:
                    continue
                tf = freq[q]
                doc_len = len(tokenized[i])
                scores[i] += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * (doc_len / avgdl)))

    ranked = [(s, len(q_set & set(t)), c) for s, t, c in zip(scores, tokenized, chunks)
              if q_set & set(t)]
    ranked.sort(key=lambda r: (r[0], r[1]), reverse=True)
    return [c for _, _, c in ranked[:k]]


def _rrf(rank_lists, k: int) -> list[str]:
    """Weighted reciprocal-rank fusion — merges rankings from different retrievers
    without reconciling score scales. Each entry may be a plain list or a
    (list, weight) tuple so a more trustworthy retriever counts for more."""
    fused: dict[str, float] = {}
    for entry in rank_lists:
        lst, weight = entry if isinstance(entry, tuple) else (entry, 1.0)
        for rank, text in enumerate(lst):
            fused[text] = fused.get(text, 0.0) + weight / (60 + rank)
    return sorted(fused, key=lambda t: fused[t], reverse=True)[:k]

# Formats we can read. Anything else is rejected with a clear message rather
# than stored as garbled bytes.
TEXT_EXTS = {".txt", ".md", ".csv", ".tsv", ".json", ".yaml", ".yml", ".log", ".xml"}
PDF_EXTS = {".pdf"}

MAX_BYTES = 2_000_000        # a price list or FAQ, not a book
# 1400, not 700: a department faculty roster ("15 faculty: name, name, …") must
# stay in ONE chunk, or "list all faculty in X" returns only a fragment. The
# largest roster (~28 names) fits comfortably under 1400.
CHUNK_CHARS = 1400           # keep a whole roster/section together
TOP_K = 3                    # chunks pasted into a reply


def _ext(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot != -1 else ""


def extract_text(filename: str, data: bytes) -> str:
    """Pull plain text out of an uploaded file, by type."""
    ext = _ext(filename)
    if ext in PDF_EXTS:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if ext in {".csv", ".tsv"}:
        delim = "\t" if ext == ".tsv" else ","
        text = data.decode("utf-8", errors="replace")
        rows = csv.reader(io.StringIO(text), delimiter=delim)
        # One line per row reads better to the model than raw comma soup.
        return "\n".join(" | ".join(cell.strip() for cell in row) for row in rows if any(row))
    if ext in TEXT_EXTS:
        return data.decode("utf-8", errors="replace")
    raise ValueError(
        f"Unsupported file type '{ext or filename}'. Allowed: PDF, and "
        "TXT/MD/CSV/TSV/JSON/YAML."
    )


def chunk(text: str) -> list[str]:
    """Split text into retrieval-sized pieces on paragraph and line boundaries.

    Splitting on blank lines first keeps price rows and FAQ entries whole; only
    an over-long block is then hard-wrapped so no single chunk dominates.
    """
    pieces: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        while len(block) > CHUNK_CHARS:
            cut = block.rfind("\n", 0, CHUNK_CHARS)
            if cut <= 0:
                cut = CHUNK_CHARS
            pieces.append(block[:cut].strip())
            block = block[cut:].strip()
        if block:
            pieces.append(block)
    return pieces


def add_document(agent_id: int | None, filename: str, data: bytes) -> dict:
    """Parse, chunk and store one uploaded file. Returns its record."""
    if len(data) > MAX_BYTES:
        raise ValueError(f"File too large ({len(data) // 1000} KB). Keep it under 2 MB.")
    text = extract_text(filename, data).strip()
    if not text:
        raise ValueError("No readable text found in that file.")
    chunks = chunk(text)
    if not chunks:
        raise ValueError("File had no usable content after parsing.")
    # Embed each chunk now so retrieval is semantic. None → text-only (BM25).
    vecs = _embed(chunks)
    embeddings = [v.tobytes() for v in vecs] if vecs is not None else None
    return db.add_kb_document(agent_id, filename, chunks, embeddings=embeddings)


def _tokenize(s: str) -> list[str]:
    # \w alone shatters Indic words: Devanagari combining marks (matras, virama,
    # anusvara — category Mn) are not \w, so "कंप्यूटर" split into क/प/य/टर and
    # Hindi queries matched nothing useful. Including the full Devanagari block
    # (U+0900–U+097F) keeps whole words together. Latin is unaffected.
    return re.findall(r"[\wऀ-ॿ]+", s.lower())


def search(agent_id: int | None, query: str, k: int = TOP_K) -> list[str]:
    """Top-k chunks for `query` — agentic, corrective, hybrid retrieval.

    1. SEMANTIC first: embed the query, rank by cosine. Handles Hindi↔English,
       verbose and misspelled questions, acronyms.
    2. CORRECTIVE (CRAG): if the top semantic hit is weak, OR the query is in
       Devanagari (proper names never align cross-script), reformulate and retry
       rather than trust a poor match.
    3. AGENTIC reformulation: transliterate a Devanagari query to Latin
       ("मनीष दीक्षित" → "manish dikshit") and re-query.
    4. HYBRID fusion: fuse semantic + keyword rankings over the original and
       reformulated queries with reciprocal-rank fusion, so a name semantic
       missed is still caught by keyword and vice-versa.

    Degrades to plain BM25 when embeddings are unavailable, so it never breaks.
    """
    query = (query or "").strip()
    if not query:
        return []
    rows = db.kb_chunks_with_embeddings(agent_id)
    if not rows:
        return []
    chunks = [t for t, _ in rows]

    sem = _semantic_scored(agent_id, query, max(k * 3, 8))
    if sem is None:
        # No embeddings/model — pure BM25 (still corrected with transliteration).
        lists = [_bm25_ranked(chunks, query, k * 3)]
        if _has_devanagari(query):
            lists.append(_bm25_ranked(chunks, _romanize(query), k * 3))
        return _rrf(lists, k)

    top_score = sem[0][1] if sem else 0.0
    sem_list = [t for t, _ in sem]

    # Fast path: a confidently-relevant English/Hinglish hit needs no correction.
    if top_score >= WEAK_HIT and not _has_devanagari(query):
        return sem_list[:k]

    # Corrective + agentic reformulation.
    roman = _refine_roman(_romanize(query)) if _has_devanagari(query) else ""
    if roman:
        # Cross-script proper name: the raw-Devanagari lists are noise (they match
        # on surname alone → wrong person), so fuse ONLY the romanized retrievers.
        # Keyword catches exact spellings ("sanjiv"), semantic bridges spelling
        # drift ("puja"≈"Pooja"); fused equally, a name that is strong in EITHER
        # wins over a wrong name strong in only one.
        alt_sem = _semantic_scored(agent_id, roman, max(k * 3, 8))
        keyword = _bm25_ranked(chunks, roman, k * 3)
        if not keyword and (not alt_sem or alt_sem[0][1] < MIN_SIM) and top_score < MIN_SIM:
            return []
        lists = [(keyword, 1.0)]
        if alt_sem:
            lists.append(([t for t, _ in alt_sem], 1.0))
        return _rrf(lists, k)

    # Weak English/Hinglish hit: fuse semantic + keyword on the original query.
    keyword = _bm25_ranked(chunks, query, k * 3)
    if not keyword and top_score < MIN_SIM:
        return []  # nothing relevant — an off-topic chunk in the prompt invites a made-up answer
    return _rrf([(sem_list, 1.0), (keyword, 1.0)], k)


def context_block(agent_id: int | None, query: str) -> str:
    """Reference material for the prompt, or "" when nothing relevant is found."""
    hits = search(agent_id, query)
    if not hits:
        return ""
    joined = "\n\n---\n\n".join(hits)
    return (
        "REFERENCE (auto-retrieved from the business's own documents — quote "
        "figures from here exactly. It may be incomplete or off-topic: if it does "
        "not answer the question, search again rather than guess):\n" + joined
    )
