#!/usr/bin/env python3
import argparse
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib import error, request

ROOT = Path(__file__).resolve().parent
INPUT_FILE = ROOT / "data-completo.json"
OUTPUT_FILE = ROOT / "data-llm-enrichment.json"
DEFAULT_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.nousresearch.com/v1")
DEFAULT_MODEL = os.getenv("LLM_MODEL", "moonshotai/kimi-k2.6")
API_KEY = os.getenv("LLM_API_KEY") or os.getenv("NOUS_API_KEY") or os.getenv("OPENAI_API_KEY")

SYSTEM_PROMPT = """Eres un normalizador de catálogo. Devuelves SOLO JSON válido.
No inventes nutrición, ingredientes, porcentajes, claims médicos ni datos regulatorios.
Tu trabajo es clasificar el producto y generar metadatos de búsqueda útiles y prudentes.
Si no estás seguro, usa listas vacías y confianza 'baja'."""

USER_PROMPT = """Analiza este lote de productos de supermercado y devuelve un array JSON con un objeto por producto.

Campos de entrada por producto:
- id
- ean
- nombre
- categoria
- subcategoria
- ingredientes
- score_cobertura
- score_aplicable

Devuelve exactamente estos campos por producto:
- id: string o null
- ean: string o null
- ai_edible: boolean
- ai_resumen: string breve (max 18 palabras)
- ai_aliases: array de hasta 6 alias o formas de búsqueda reales
- ai_keywords: array de hasta 10 keywords sueltas útiles para búsqueda
- ai_confianza: 'alta' | 'media' | 'baja'
- ai_notes: array de hasta 3 notas cortas sobre tipo de producto o uso, sin inventar nutrición

Reglas:
- Si no es alimento o bebida para humanos, ai_edible=false.
- Usa solo lo inferible por nombre/categoría/ingredientes.
- No repitas palabras inútiles ni metas frases comerciales.
- Mantén español neutro.
- Responde SOLO con JSON, sin markdown."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enriquecer catálogo con metadatos opcionales generados por LLM")
    parser.add_argument("--input", default=str(INPUT_FILE))
    parser.add_argument("--output", default=str(OUTPUT_FILE))
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--only-missing", action="store_true", help="solo productos sin overlay previo")
    parser.add_argument("--food-only", action="store_true", default=True)
    parser.add_argument("--include-non-food", action="store_true")
    return parser.parse_args()


def load_json(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise ValueError(f"{path} no contiene un array JSON")
    return [row for row in data if isinstance(row, dict)]


def call_chat(base_url: str, model: str, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not API_KEY:
        raise RuntimeError("Falta LLM_API_KEY / NOUS_API_KEY / OPENAI_API_KEY")

    payload = {
        "model": model,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": USER_PROMPT + "\n\nLote:\n" + json.dumps(batch, ensure_ascii=False),
            },
        ],
    }
    req = request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=120) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:600]}") from exc

    content = raw["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    rows = parsed.get("items") if isinstance(parsed, dict) else parsed
    if not isinstance(rows, list):
        raise RuntimeError(f"Respuesta inesperada del LLM: {content[:500]}")
    return [row for row in rows if isinstance(row, dict)]


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id") or None,
        "ean": str(row.get("ean") or "").strip() or None,
        "ai_edible": bool(row.get("ai_edible")),
        "ai_resumen": str(row.get("ai_resumen") or "").strip()[:160],
        "ai_aliases": [str(x).strip() for x in (row.get("ai_aliases") or []) if str(x).strip()][:6],
        "ai_keywords": [str(x).strip() for x in (row.get("ai_keywords") or []) if str(x).strip()][:10],
        "ai_confianza": row.get("ai_confianza") if row.get("ai_confianza") in {"alta", "media", "baja"} else "baja",
        "ai_notes": [str(x).strip() for x in (row.get("ai_notes") or []) if str(x).strip()][:3],
    }


def row_key(row: dict[str, Any]) -> str:
    return str(row.get("ean") or "").strip() or str(row.get("id") or "").strip() or str(row.get("nombre") or "").strip().lower()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    items = load_json(input_path)
    existing = load_json(output_path) if output_path.exists() else []
    existing_by_key = {row_key(row): row for row in existing if row_key(row)}

    include_non_food = bool(args.include_non_food)
    candidates: list[dict[str, Any]] = []
    for row in items:
        if args.only_missing and row_key(row) in existing_by_key:
            continue
        if not include_non_food and row.get("catalog_scope") != "food":
            continue
        candidates.append(
            {
                "id": row.get("id"),
                "ean": row.get("ean"),
                "nombre": row.get("nombre"),
                "categoria": row.get("categoria"),
                "subcategoria": row.get("subcategoria"),
                "ingredientes": row.get("ingredientes"),
                "score_cobertura": row.get("score_cobertura"),
                "score_aplicable": row.get("score_aplicable"),
            }
        )

    if args.limit:
        candidates = candidates[: args.limit]

    if not candidates:
        print("Nada que enriquecer")
        return

    enriched = dict(existing_by_key)
    total = len(candidates)
    for start in range(0, total, args.batch_size):
        batch = candidates[start : start + args.batch_size]
        rows = call_chat(args.base_url, args.model, batch)
        for row in rows:
            normalized = normalize_row(row)
            key = row_key(normalized)
            if key:
                enriched[key] = normalized
        print(f"Lote {start // args.batch_size + 1}: {len(batch)} productos procesados")
        time.sleep(args.sleep)

    output = sorted(enriched.values(), key=lambda row: ((row.get("ean") or ""), (row.get("id") or ""), row.get("ai_resumen") or ""))
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: {output_path.name} con {len(output)} filas de overlay")


if __name__ == "__main__":
    main()
