#!/usr/bin/env python3
"""Genera una versión standalone del HTML con el dataset completo incrustado."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML_IN = ROOT / "mercascore.html"
DATA_IN = ROOT / "data-completo.json"
HTML_OUT = ROOT / "mercascore-completo.html"

MARKER_RE = re.compile(
    r"/\* =+\n\s+DATOS DE PRODUCTOS .*?\*/\nconst productos = \[.*?\];",
    re.S,
)


def _to_template_literal_payload(text: str) -> str:
    return text.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")


def main() -> None:
    html = HTML_IN.read_text(encoding="utf-8")
    productos = json.loads(DATA_IN.read_text(encoding="utf-8"))
    dataset_json = json.dumps(productos, ensure_ascii=False, separators=(",", ":"))
    dataset_payload = _to_template_literal_payload(dataset_json)
    replacement = (
        "/* ============================================================\n"
        f"   DATOS DE PRODUCTOS ({len(productos)} productos de Mercadona; dataset completo con score parcial)\n"
        "   ============================================================ */\n"
        f"const productos = JSON.parse(String.raw`{dataset_payload}`);"
    )
    new_html, n = MARKER_RE.subn(replacement, html, count=1)
    if n != 1:
        raise SystemExit("No se encontró el bloque de dataset en mercascore.html")
    HTML_OUT.write_text(new_html, encoding="utf-8")
    size_kb = HTML_OUT.stat().st_size / 1024
    print(f"OK: {HTML_OUT.name} generado con {len(productos)} productos ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
