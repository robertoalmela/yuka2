#!/usr/bin/env python3
"""
merge-dataset.py — Merge full Mercadona catalog (with EANs) + Open Food Facts subset.

Reads:
  - data-mercadona-ean.json  (full catalog ~4386 products, includes EAN from Mercadona API)
  - data-final.json          (enriched subset ~137 products with OFF nutritional data)

  Fallback: if data-mercadona-ean.json is missing, uses data-mercadona-raw.json
            (no EAN column, so name+categoria matching only).

Writes:
  - data-completo.json       (all products merged, score_disponible + fuente_datos flags)

No external dependencies — only uses Python stdlib.
"""

import json, os, re, unicodedata, sys

NUTRITIONAL_FIELDS = [
    'calorias', 'proteinas', 'carbohidratos', 'grasas',
    'grasas_sat', 'azucares', 'fibra', 'sal', 'nutriscore',
    'nova', 'aditivos'
]


def normalize(text):
    if not text:
        return ''
    text = unicodedata.normalize('NFD', str(text))
    text = re.sub(r'[\u0300-\u036f]', '', text)
    return text.lower().strip()


def load_json(path, encoding='utf-8-sig'):
    try:
        with open(path, encoding=encoding) as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # ---- Load base catalog (prefer file with EANs) ----
    base = load_json(os.path.join(base_dir, 'data-mercadona-ean.json'))
    base_label = 'data-mercadona-ean.json'
    if base is None:
        base = load_json(os.path.join(base_dir, 'data-mercadona-raw.json'))
        base_label = 'data-mercadona-raw.json (no EANs)'

    if base is None:
        print('ERROR: no base catalog found (data-mercadona-ean.json or data-mercadona-raw.json)')
        sys.exit(1)

    has_ean = 'ean' in (base[0] if base else {})
    print(f"Base: {base_label} — {len(base)} products, has_ean={has_ean}")

    # ---- Load enriched subset ----
    enriched = load_json(os.path.join(base_dir, 'data-final.json'))
    if enriched is None:
        print('ERROR: data-final.json not found')
        sys.exit(1)
    print(f"Enriched: data-final.json — {len(enriched)} products")

    # ---- Normalize enriched data ----
    for p in enriched:
        # Normalize aditivos: PS outputs {} for empty arrays, single strings for one additive
        a = p.get('aditivos')
        if isinstance(a, dict):
            p['aditivos'] = []
        elif isinstance(a, str):
            p['aditivos'] = [a]

    # ---- Build lookups ----
    enriched_by_ean = {}
    for p in enriched:
        ean = str(p.get('ean', '')).strip().strip('"').strip("'")
        if ean:
            enriched_by_ean[ean] = p

    enriched_by_name = {}
    for p in enriched:
        key = f"{normalize(p.get('nombre', ''))}|{normalize(p.get('categoria', ''))}"
        enriched_by_name[key] = p

    # ---- Merge ----
    merged = []
    match_ean = match_name = no_match = 0

    for p in base:
        ean_val = str(p.get('ean', '')).strip().strip('"').strip("'") if has_ean else ''

        entry = {
            'id': p.get('id'),
            'nombre': p.get('nombre', ''),
            'slug': p.get('slug'),
            'precio': p.get('precio'),
            'categoria': p.get('categoria', ''),
            'subcategoria': p.get('subcategoria'),
            'ean': ean_val,
        }

        src = None

        if ean_val and ean_val in enriched_by_ean:
            src = enriched_by_ean[ean_val]
            match_ean += 1

        if src is None:
            key = f"{normalize(entry['nombre'])}|{normalize(entry['categoria'])}"
            if key in enriched_by_name:
                src = enriched_by_name[key]
                match_name += 1

        if src is not None:
            for f in NUTRITIONAL_FIELDS:
                v = src.get(f)
                if f == 'aditivos' and isinstance(v, dict):
                    v = []
                entry[f] = v
            entry['score_disponible'] = True
            entry['fuente_datos'] = 'off'
        else:
            for f in NUTRITIONAL_FIELDS:
                entry[f] = [] if f == 'aditivos' else None
            entry['score_disponible'] = False
            entry['fuente_datos'] = 'mercadona'
            no_match += 1

        merged.append(entry)

    # ---- Write output ----
    out_path = os.path.join(base_dir, 'data-completo.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    kb = os.path.getsize(out_path) / 1024
    scored = sum(1 for e in merged if e['score_disponible'])

    print(f"\nWritten: data-completo.json ({kb:.1f} KB)")
    print(f"  Total:               {len(merged)}")
    print(f"  Match by EAN:        {match_ean}")
    print(f"  Match by name:       {match_name}")
    print(f"  Mercadona-only:      {no_match}")
    print(f"  score_disponible:    {scored}")
    print("OK")


if __name__ == '__main__':
    main()
