#!/usr/bin/env python3
"""Resume FR/ES/DE translation: only translate keys still identical to EN."""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from deep_translator import GoogleTranslator

ROOT = Path(__file__).resolve().parents[1]
I18N = ROOT / "frontend" / "src" / "assets" / "i18n"
CACHE = ROOT / "scripts" / ".i18n-translate-cache.json"
TARGETS = {"fr": "fr", "es": "es", "de": "de"}
WORKERS = 10
_cache_lock = threading.Lock()

PH_RE = re.compile(r"\{\{[^}]+\}\}")
TAG_RE = re.compile(r"<[^>]+>")
URL_RE = re.compile(r"^https?://")
CODE_HINTS = ("curl ", "from openai", "client.", "Authorization: Bearer", "base_url=")


def flatten(d: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(flatten(v, key))
        else:
            out[key] = str(v)
    return out


def unflatten(flat: dict[str, str]) -> dict:
    root: dict = {}
    for path, value in flat.items():
        parts = path.split(".")
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return root


def protect(text: str) -> tuple[str, list[str]]:
    tokens: list[str] = []

    def stash(match: re.Match[str]) -> str:
        tokens.append(match.group(0))
        return f"__PH{len(tokens) - 1}__"

    out = TAG_RE.sub(stash, text)
    out = PH_RE.sub(stash, out)
    return out, tokens


def restore(text: str, tokens: list[str]) -> str:
    for i, token in enumerate(tokens):
        text = text.replace(f"__PH{i}__", token)
    return text


def should_skip(key: str, text: str, skip_keys: set[str]) -> bool:
    if key in skip_keys:
        return True
    stripped = text.strip()
    if not stripped:
        return True
    if URL_RE.match(stripped):
        return True
    if any(h in stripped for h in CODE_HINTS):
        return True
    if key.startswith("settings.general.font") and ("Mono" in stripped or "monospace" in stripped):
        return True
    return False


def load_cache() -> dict:
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict) -> None:
    with _cache_lock:
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def translate_one(text: str, target: str, retries: int = 5) -> str:
    protected, tokens = protect(text)
    for attempt in range(retries):
        try:
            translated = GoogleTranslator(source="en", target=target).translate(protected)
            if translated:
                return restore(translated, tokens)
        except Exception:
            time.sleep(0.35 * (attempt + 1))
    return text


def translate_key(key: str, source: str, target: str, lang_cache: dict[str, str]) -> tuple[str, str]:
    cached = lang_cache.get(key)
    if cached is not None:
        return key, cached
    value = translate_one(source, target)
    lang_cache[key] = value
    return key, value


def resume_locale(lang: str, target: str, en_flat: dict[str, str], cur_flat: dict[str, str], skip_keys: set[str], cache: dict) -> dict[str, str]:
    lang_cache = cache.setdefault(lang, {})
    out = dict(cur_flat)
    pending = [
        key for key, en_val in en_flat.items()
        if out.get(key, en_val) == en_val and not should_skip(key, en_val, skip_keys)
    ]
    total = len(pending)
    print(f"  {lang}: {total} keys to translate")
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(translate_key, key, en_flat[key], target, lang_cache) for key in pending]
        for fut in as_completed(futures):
            key, value = fut.result()
            out[key] = value
            done += 1
            if done % 75 == 0:
                save_cache(cache)
                print(f"  {lang}: {done}/{total}")
    save_cache(cache)
    return out


def main() -> None:
    en = json.loads((I18N / "en.json").read_text(encoding="utf-8"))
    it = json.loads((I18N / "it.json").read_text(encoding="utf-8"))
    en_flat = flatten(en)
    it_flat = flatten(it)
    skip_keys = {k for k in en_flat if k in it_flat and it_flat[k] == en_flat[k]}
    cache = load_cache()

    for lang, target in TARGETS.items():
        print(f"Resuming {lang}...")
        cur = json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8"))
        cur_flat = flatten(cur)
        flat = resume_locale(lang, target, en_flat, cur_flat, skip_keys, cache)
        (I18N / f"{lang}.json").write_text(
            json.dumps(unflatten(flat), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        same = sum(1 for k in en_flat if flat.get(k) == en_flat[k])
        print(f"  wrote {lang}.json ({same} keys identical to EN)")

    print("done")


if __name__ == "__main__":
    main()
