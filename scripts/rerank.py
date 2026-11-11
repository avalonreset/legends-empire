#!/usr/bin/env python3
"""rerank.py: query-time reranker for chunk candidates.

Takes a query string + a list of candidate chunks (from BM25, vector, or any
upstream stage) and reorders them using semantic similarity.

Strategy (in preference order, automatically chosen at runtime):
  1. If Ollama is reachable AND the selected embedding model is pulled
       → embed the query with Nomic's ``search_query:`` task prefix and
         each candidate's contextualized_text with ``search_document:``, then
         rank by cosine. Caches per-chunk embeddings in
         .vault-meta/embed-cache.json under a model + scheme + input-hash key.
  2. Otherwise
       → no-op rerank: return candidates in input order with a synthesized
         note. Caller (retrieve.py) still gets a useful result; downstream
         drill-into-page logic is unchanged.

The default is Ollama's multilingual nomic-embed-text-v2-moe. It is optional
and never downloaded automatically. The smaller English-oriented v1.5 model
remains available through explicit ``--model nomic-embed-text`` selection.

Mirrors the localhost-only OLLAMA_URL guard from scripts/tiling-check.py:
remote ollama endpoints require --allow-remote-ollama because page bodies
are POSTed as embedding input.

Usage:
  rerank.py "query string" --candidates candidates.json [--top 5]
  rerank.py "query string" --candidates - --top 5    # stdin
  rerank.py --peek "query string"                     # show strategy chosen
  rerank.py --model nomic-embed-text "query" --peek  # explicit v1.5 opt-in

Candidates JSON shape:
  [{"chunk_id": "c-000042:3", "path": ".vault-meta/chunks/.../chunk-003.json", "score": 7.1}, ...]

Output: ranked candidates with `rerank_score` added.

Exit codes:
  0: success
  2: usage error
  3: candidate input malformed
  10: ollama unreachable (no-op rerank performed, exit 0 with note)
  11: model not pulled (no-op rerank performed, exit 0 with note)
"""

import argparse
import hashlib
import ipaddress
import json
import math
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from claude_empire import __version__
from claude_empire.paths import VaultSelectionError, assert_within, resolve_vault_root
from claude_empire.transaction import (
    MutationLock,
    TransactionError,
    _atomic_vault_write,
    _safe_vault_path,
)

if os.name == "nt":
    from claude_empire.windows_fs import os_proxy as os


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code, "Redirects are disabled", headers, fp
        )


def _build_opener():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirectHandler(),
    )


def _assert_safe_resolved_host(url):
    parsed = validate_ollama_base_url(url)
    if parsed is None:
        raise ValueError("Invalid Ollama URL")
    host = (parsed.hostname or "").strip()
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    infos = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
    if not infos:
        raise ValueError(f"Could not resolve host {host!r}")
    for info in infos:
        addr = info[4][0].split("%", 1)[0]
        ip = ipaddress.ip_address(addr)
        if ip.is_link_local or ip.is_unspecified or ip.is_multicast:
            raise ValueError(f"Refusing non-routable/metadata IP {addr} for {host!r}")


_STD_URLOPEN = urllib.request.urlopen


def _urlopen(req, timeout=None):
    url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
    base_url = url.rsplit("/api/", 1)[0]
    _assert_safe_resolved_host(base_url)
    if urllib.request.urlopen is not _STD_URLOPEN:
        return urllib.request.urlopen(req, timeout=timeout)
    return _build_opener().open(req, timeout=timeout)

VAULT_ROOT = Path.cwd().resolve()
META_DIR = VAULT_ROOT / ".vault-meta"
EMBED_CACHE_PATH = META_DIR / "embed-cache.json"
CACHE_LOCK = META_DIR / ".embed-cache.lock"

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "nomic-embed-text-v2-moe"
OLLAMA_TIMEOUT_SEC = 3
EMBED_TIMEOUT_SEC = 30
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_MODEL_NAME_LENGTH = 255
MAX_TOP_RESULTS = 1_000
MODEL_NAME_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9][A-Za-z0-9._-]*)?\Z"
)

QUERY_TASK_PREFIX = "search_query: "
DOCUMENT_TASK_PREFIX = "search_document: "
NOMIC_EMBED_SCHEME = "nomic-search-v1"
RAW_EMBED_SCHEME = "raw-v1"


def configure_vault(explicit=None):
    global VAULT_ROOT, META_DIR, EMBED_CACHE_PATH, CACHE_LOCK
    try:
        selected = resolve_vault_root(
            explicit,
            start=Path.cwd(),
            plugin_root=PLUGIN_ROOT,
        )
    except VaultSelectionError as exc:
        log(f"ERR {exc.code}: {exc}")
        return False
    VAULT_ROOT = selected.root
    META_DIR = VAULT_ROOT / ".vault-meta"
    EMBED_CACHE_PATH = META_DIR / "embed-cache.json"
    CACHE_LOCK = META_DIR / ".embed-cache.lock"
    try:
        for relative in (
            ".vault-meta",
            ".vault-meta/embed-cache.json",
            ".vault-meta/.embed-cache.lock",
            ".vault-meta/chunks",
        ):
            _safe_vault_path(VAULT_ROOT, relative)
    except (VaultSelectionError, TransactionError) as exc:
        log(f"ERR {exc.code}: {exc}")
        return False
    return True

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_CANDIDATES = 3
EXIT_NO_OLLAMA = 10
EXIT_NO_MODEL = 11


def log(msg):
    print(msg, file=sys.stderr)


def validate_model_name(model):
    """Return a bounded Ollama model identifier or raise ValueError.

    Model selection is data sent to Ollama, not a URL, path, or shell fragment.
    Keeping the grammar narrow also makes cache and diagnostic keys stable.
    """
    repository = model.split(":", 1)[0] if isinstance(model, str) else ""
    if (
        not isinstance(model, str)
        or not model
        or len(model) > MAX_MODEL_NAME_LENGTH
        or MODEL_NAME_RE.fullmatch(model) is None
        or any(part in {"", ".", ".."} for part in repository.split("/"))
    ):
        raise ValueError(
            "model must be a bounded Ollama name such as "
            "nomic-embed-text-v2-moe or nomic-embed-text:v1.5"
        )
    return model


def parse_top_k(value):
    """Parse a positive, bounded result count for command-line callers."""
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if not 1 <= parsed <= MAX_TOP_RESULTS:
        raise argparse.ArgumentTypeError(
            f"must be between 1 and {MAX_TOP_RESULTS}"
        )
    return parsed


def model_is_available(model, available_models):
    """Match the exact request, treating only ``:latest`` as its untagged alias."""
    selected = validate_model_name(model)
    names = {
        item
        for item in available_models
        if isinstance(item, str) and item
    }
    if selected in names:
        return True
    if ":" in selected:
        return False
    return f"{selected}:latest" in names


def is_nomic_model(model):
    """Return whether ``model`` uses Nomic's asymmetric retrieval contract."""
    leaf = str(model).lower().rsplit("/", 1)[-1].split(":", 1)[0]
    return leaf.startswith("nomic")


def embedding_scheme(model):
    """Version token for the exact text-to-vector input convention."""
    return NOMIC_EMBED_SCHEME if is_nomic_model(model) else RAW_EMBED_SCHEME


def with_task_prefix(text, role, model=None):
    """Apply the task prefix required by Nomic retrieval embeddings.

    Non-Nomic models keep their prior raw-text behavior. A role typo is rejected
    instead of silently putting a query and document in incompatible spaces.
    """
    model = model or DEFAULT_MODEL
    if role not in ("query", "document"):
        raise ValueError(f"role must be 'query' or 'document', got {role!r}")
    if not is_nomic_model(model):
        return text
    prefix = QUERY_TASK_PREFIX if role == "query" else DOCUMENT_TASK_PREFIX
    return prefix + text


def embedding_input_hash(text):
    """Hash the exact prefixed text sent to the embedding model."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def embedding_cache_key(model, input_hash):
    """Key embeddings by model, input scheme, and exact embedded content."""
    return f"{model}:{embedding_scheme(model)}:{input_hash}"


def fallback_score(candidate):
    """Read the sparse score from either supported candidate contract."""
    value = candidate.get("bm25_score", candidate.get("score", 0.0))
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return score if math.isfinite(score) else 0.0


def mark_noop(candidates, source):
    """Annotate a fallback without changing the upstream BM25 ordering."""
    for candidate in candidates:
        candidate["rerank_score"] = fallback_score(candidate)
        candidate["rerank_source"] = source


def usable_embedding(value, expected_len=None):
    """Reject empty, malformed, non-finite, zero, or wrong-sized vectors."""
    if not isinstance(value, list) or not value:
        return False
    if expected_len is not None and len(value) != expected_len:
        return False
    if not all(isinstance(item, (int, float)) and not isinstance(item, bool)
               for item in value):
        return False
    return all(math.isfinite(item) for item in value) and any(item != 0 for item in value)


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


MAX_CANDIDATES_BYTES = 1_048_576
MAX_CACHE_BYTES = 8 * 1024 * 1024


def validate_ollama_base_url(url):
    """Accept only credential-free HTTP(S) base URLs."""

    try:
        parsed = urllib.parse.urlparse(url)
        _ = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    host = (parsed.hostname or "").strip().lower()
    if host in {"metadata.google.internal", "instance-data"} or host.endswith(".local"):
        return None
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        ip = None
    if ip is not None and (ip.is_link_local or ip.is_unspecified or ip.is_multicast):
        return None
    return parsed


def ollama_url(allow_remote):
    url = os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL).rstrip("/")
    parsed = validate_ollama_base_url(url)
    if parsed is None:
        log(
            "ERR: OLLAMA_URL must be a credential-free http(s) base URL "
            "without a query, fragment, or link-local/unspecified target."
        )
        sys.exit(EXIT_USAGE)
    if not allow_remote:
        host = parsed.hostname or ""
        if host not in ("127.0.0.1", "localhost", "::1"):
            log(f"ERR: OLLAMA_URL={url} points off-localhost (host={host!r}).")
            log("  Either: (a) run ollama locally: `systemctl --user start ollama` or `ollama serve`")
            log("  Or:     (b) pass --allow-remote-ollama through retrieve.py, which forwards it here.")
            log("  Or:     (c) unset OLLAMA_URL to fall back to the local default (127.0.0.1:11434).")
            sys.exit(EXIT_USAGE)
    return url


def ollama_alive(url):
    if validate_ollama_base_url(url) is None:
        return False, []
    try:
        req = urllib.request.Request(
            f"{url}/api/tags",
            headers={"User-Agent": f"legends-empire/{__version__}"},
            method="GET",
        )
        # `url` passed the credential-free HTTP(S) validator above.
        with _urlopen(req, timeout=OLLAMA_TIMEOUT_SEC) as resp:  # nosec B310
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                return False, []
            data = json.loads(raw)
            models = [
                model["name"]
                for model in data.get("models", [])
                if isinstance(model, dict)
                and isinstance(model.get("name"), str)
                and model["name"]
            ]
            return True, models
    except (urllib.error.URLError, json.JSONDecodeError, ValueError, OSError):
        return False, []


def embed_one(url, model, text):
    if validate_ollama_base_url(url) is None:
        raise ValueError("Ollama URL must be a credential-free HTTP(S) base URL")
    payload = json.dumps({"model": model, "input": text}).encode("utf-8")
    req = urllib.request.Request(
        f"{url}/api/embed",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": f"legends-empire/{__version__}",
        },
        method="POST",
    )
    # `url` passed the credential-free HTTP(S) validator above.
    with _urlopen(req, timeout=EMBED_TIMEOUT_SEC) as resp:  # nosec B310
        raw = resp.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("Ollama response exceeds size limit")
        data = json.loads(raw)
        embeddings = data.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != 1:
            return []
        return embeddings[0]


def load_cache():
    if EMBED_CACHE_PATH.is_symlink() or not EMBED_CACHE_PATH.is_file():
        return {}
    try:
        if EMBED_CACHE_PATH.stat().st_size > MAX_CACHE_BYTES:
            return {}
        cache = json.loads(EMBED_CACHE_PATH.read_text(encoding="utf-8"))
        return cache if isinstance(cache, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_cache(cache):
    """Persist the optional cache through the canonical pinned mutation path.

    Cache persistence is intentionally brief and separate from embedding work.
    A bounded lock attempt preserves the historical non-blocking query
    behavior, while the pinned root and metadata descriptors ensure a replaced
    vault root or ``.vault-meta`` entry cannot redirect the write.
    """
    payload = json.dumps(cache, ensure_ascii=False).encode("utf-8")
    root_fd = -1
    meta_fd = -1
    try:
        with MutationLock(VAULT_ROOT, timeout=0.3) as mutation_lock:
            root_fd = mutation_lock.duplicate_root_fd()
            meta_fd = mutation_lock.duplicate_parent_fd()
            _atomic_vault_write(
                VAULT_ROOT,
                ".vault-meta/embed-cache.json",
                payload,
                mode=0o600,
                root_fd=root_fd,
                meta_fd=meta_fd,
            )
    finally:
        if meta_fd is not None and meta_fd >= 0:
            os.close(meta_fd)
        if root_fd is not None and root_fd >= 0:
            os.close(root_fd)


def load_chunk(chunk_rel_path):
    if not isinstance(chunk_rel_path, str) or not chunk_rel_path:
        return None
    rel = Path(chunk_rel_path)
    # On Windows "/x" and "C:x" are not "absolute" under pathlib but still
    # escape a joined root.
    if (
        rel.is_absolute()
        or chunk_rel_path.startswith(("/", "\\"))
        or re.match(r"^[A-Za-z]:", chunk_rel_path)
    ):
        return None
    raw_p = VAULT_ROOT / rel
    if raw_p.is_symlink():
        return None
    p = raw_p.resolve()
    if not p.is_relative_to((META_DIR / "chunks").resolve()):
        return None
    if not p.is_file():
        return None
    try:
        if p.stat().st_size > MAX_CACHE_BYTES:
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def rerank(query, candidates, top_k=5, allow_remote=False, model=None):
    """Returns candidates list, possibly truncated to top_k, with rerank_score added.
    Falls back to input-order if ollama is unavailable (still adds rerank_source: 'noop').
    """
    selected_model = validate_model_name(model or DEFAULT_MODEL)
    url = ollama_url(allow_remote)
    alive, models = ollama_alive(url)
    if not alive:
        log("ollama unreachable: no-op rerank")
        mark_noop(candidates, "noop-no-ollama")
        return candidates[:top_k]
    if not model_is_available(selected_model, models):
        log(
            f"model {selected_model} not pulled: no-op rerank; "
            f"install explicitly with: ollama pull {selected_model}"
        )
        mark_noop(candidates, "noop-no-model")
        return candidates[:top_k]

    cache = load_cache()
    cache_dirty = False
    try:
        q_emb = embed_one(
            url,
            selected_model,
            with_task_prefix(query, "query", model=selected_model),
        )
        if not usable_embedding(q_emb):
            raise ValueError("query embedder returned an unusable vector")
    except Exception as e:
        log(f"query embed failed: {e}")
        mark_noop(candidates, "noop-embed-error")
        return candidates[:top_k]

    for c in candidates:
        chunk = load_chunk(c.get("path", ""))
        if not chunk:
            log(f"chunk missing for {c.get('chunk_id')}: no-op rerank")
            mark_noop(candidates, "noop-missing-chunk")
            return candidates[:top_k]
        text = chunk.get("contextualized_text") or chunk.get("raw_text", "")
        document_input = with_task_prefix(text, "document", model=selected_model)
        cache_key = embedding_cache_key(
            selected_model,
            embedding_input_hash(document_input),
        )
        emb = cache.get(cache_key)
        if not usable_embedding(emb, expected_len=len(q_emb)):
            try:
                emb = embed_one(
                    url,
                    selected_model,
                    document_input,
                )
                if not usable_embedding(emb, expected_len=len(q_emb)):
                    raise ValueError("document embedder returned an unusable vector")
            except Exception as e:
                log(f"embed failed for {c.get('chunk_id')}: {e}")
                # Cosine scores and BM25 scores are not comparable scales. A
                # partial rerank would promote failures unpredictably, so one
                # candidate failure falls back the complete ordered set.
                mark_noop(candidates, "noop-embed-error")
                return candidates[:top_k]
            cache[cache_key] = emb
            cache_dirty = True
        c["rerank_score"] = cosine(q_emb, emb)
        c["rerank_source"] = f"cosine:{selected_model}"

    if cache_dirty:
        try:
            save_cache(cache)
        except (OSError, TransactionError) as e:
            # Cache persistence is optional. A valid in-memory rerank result
            # must not be discarded just because the derived cache is read-only.
            log(f"WARN: could not persist embed cache: {e}")

    ranked = sorted(candidates, key=lambda x: x.get("rerank_score", 0.0), reverse=True)
    return ranked[:top_k]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rerank chunk candidates by semantic similarity.")
    parser.add_argument("--vault", help="Explicit vault root")
    parser.add_argument("query", nargs="?", help="Query text")
    parser.add_argument("--candidates", help="Path to candidates JSON or `-` for stdin",
                        default=None)
    parser.add_argument("--top", type=parse_top_k, default=5, help="Top-K to return")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=(
            "Installed Ollama embedding model; defaults to multilingual "
            f"{DEFAULT_MODEL} and is never downloaded automatically"
        ),
    )
    parser.add_argument("--peek", action="store_true",
                        help="Print rerank strategy chosen and exit")
    parser.add_argument("--allow-remote-ollama", action="store_true",
                        help="Accept non-localhost OLLAMA_URL (potential data exfil)")
    args = parser.parse_args(argv)
    try:
        args.model = validate_model_name(args.model)
    except ValueError as exc:
        parser.error(str(exc))
    if not configure_vault(args.vault):
        return EXIT_USAGE

    if args.peek:
        if not args.query:
            log("--peek needs a query string")
            sys.exit(EXIT_USAGE)
        url = ollama_url(args.allow_remote_ollama)
        alive, models = ollama_alive(url)
        strategy = "noop-no-ollama"
        present = model_is_available(args.model, models) if alive else False
        if alive:
            strategy = f"cosine:{args.model}" if present else "noop-no-model"
        print(json.dumps({
            "query": args.query,
            "strategy": strategy,
            "model": args.model,
            "ollama_url": url,
            "ollama_alive": alive,
            "model_present": present,
            "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }, indent=2))
        return EXIT_OK

    if not args.query or args.candidates is None:
        log("usage: rerank.py <query> --candidates <path|-> [--top N]")
        return EXIT_USAGE

    if args.candidates == "-":
        cand_text = sys.stdin.read(MAX_CANDIDATES_BYTES + 1)
        if len(cand_text.encode("utf-8")) > MAX_CANDIDATES_BYTES:
            log("ERR: candidates input exceeds 1 MB limit")
            return EXIT_CANDIDATES
    else:
        try:
            raw_cand_path = Path(args.candidates)
            if not raw_cand_path.is_absolute():
                raw_cand_path = VAULT_ROOT / raw_cand_path
            if raw_cand_path.is_symlink():
                raise OSError("candidates path may not be a symlink")
            candidate_path = assert_within(VAULT_ROOT, args.candidates)
            _safe_vault_path(VAULT_ROOT, candidate_path.relative_to(VAULT_ROOT).as_posix())
            if candidate_path.stat().st_size > MAX_CANDIDATES_BYTES:
                raise OSError("candidates file exceeds 1 MB limit")
            cand_text = candidate_path.read_text(encoding="utf-8")
        except (VaultSelectionError, TransactionError, OSError) as exc:
            log(f"ERR: candidates path is not a readable in-vault file: {exc}")
            return EXIT_CANDIDATES
    try:
        candidates = json.loads(cand_text)
        if not isinstance(candidates, list):
            raise ValueError("candidates must be a JSON list")
    except (json.JSONDecodeError, ValueError) as e:
        log(f"ERR: bad candidates JSON: {e}")
        return EXIT_CANDIDATES

    result = rerank(
        args.query,
        candidates,
        top_k=args.top,
        allow_remote=args.allow_remote_ollama,
        model=args.model,
    )
    print(json.dumps(result, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
