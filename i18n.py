from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from usage_lang import detect_lang

I18N_PATH = Path(__file__).with_name("i18n.json")


@lru_cache(maxsize=1)
def _load_i18n_bundle() -> dict[str, dict[str, str]]:
    data = json.loads(I18N_PATH.read_text(encoding="utf-8"))
    return {
        str(lang): {str(key): str(value) for key, value in values.items()}
        for lang, values in data.items()
    }


def _t(language: str, key: str, **kwargs: object) -> str:
    bundle = _load_i18n_bundle()
    table = bundle.get(language) or bundle["en"]
    template = table.get(key) or bundle["en"].get(key) or key
    return template.format(**kwargs)


def t(key: str, **kwargs: object) -> str:
    return _t(detect_lang(), key, **kwargs)
