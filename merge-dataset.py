import json
import re
import unicodedata
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
RAW_CANDIDATES = [
    BASE_DIR / "data-mercadona-ean.json",
    BASE_DIR / "data-mercadona-raw.json",
]
ENRICHED_FILE = BASE_DIR / "data-final.json"
LLM_ENRICHED_FILE = BASE_DIR / "data-llm-enrichment.json"
OUTPUT_FILE = BASE_DIR / "data-completo.json"

NUTRITION_FIELDS = [
    "calorias",
    "proteinas",
    "carbohidratos",
    "grasas",
    "grasas_sat",
    "azucares",
    "fibra",
    "sal",
    "nutriscore",
    "nova",
]

FOOD_CATEGORIES = {
    "Aceite, especias y salsas",
    "Agua y refrescos",
    "Aperitivos",
    "Arroz, legumbres y pasta",
    "Azúcar, caramelos y chocolate",
    "Bodega",
    "Cacao, café e infusiones",
    "Carne",
    "Cereales y galletas",
    "Charcutería y quesos",
    "Congelados",
    "Conservas, caldos y cremas",
    "Fruta y verdura",
    "Huevos, leche y mantequilla",
    "Marisco y pescado",
    "Panadería y pastelería",
    "Pizzas y platos preparados",
    "Postres y yogures",
    "Zumos",
}

NON_FOOD_CATEGORIES = {
    "Bebé",
    "Cuidado del cabello",
    "Cuidado facial y corporal",
    "Fitoterapia y parafarmacia",
    "Limpieza y hogar",
    "Maquillaje",
    "Mascotas",
}

MOJIBAKE_MARKERS = ("Ã", "Â", "â", "€", "™", "œ", "ž", "�")
ADDITIVE_RE = re.compile(r"(?<![A-Z0-9])E\s*[-–]?\s*(\d{3,4})([A-Z]?)(?![A-Z0-9])", re.I)
ROMAN_SUFFIXES = {"I", "II", "III", "IV", "V"}
TOKEN_RE = re.compile(r"[a-z0-9áéíóúüñ]{2,}", re.I)
BRANDS = (
    "Hacendado",
    "Deliplus",
    "Bosque Verde",
    "Compy",
    "Baby Smile",
)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFD", value or "")
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", value).strip().lower()


def repair_text(value):
    if not isinstance(value, str) or not any(marker in value for marker in MOJIBAKE_MARKERS):
        return value
    best = value
    best_badness = sum(best.count(marker) for marker in MOJIBAKE_MARKERS)
    for source_encoding in ("latin1", "cp1252"):
        try:
            candidate = value.encode(source_encoding).decode("utf-8")
        except Exception:
            continue
        candidate_badness = sum(candidate.count(marker) for marker in MOJIBAKE_MARKERS)
        if candidate_badness < best_badness:
            best = candidate
            best_badness = candidate_badness
    return best


def repair_value(value):
    if isinstance(value, dict):
        return {k: repair_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [repair_value(v) for v in value]
    if isinstance(value, str):
        return repair_text(value)
    return value


def extract_additives(text: str):
    if not text:
        return []
    found = []
    for digits, suffix in ADDITIVE_RE.findall(text.upper()):
        suffix = suffix.upper()
        if suffix in ROMAN_SUFFIXES:
            suffix = ""
        found.append(f"E-{digits}{suffix}")
    return sorted(set(found))


def choose_raw_file() -> Path:
    for path in RAW_CANDIDATES:
        if path.exists():
            return path
    raise FileNotFoundError("No se encontró data-mercadona-ean.json ni data-mercadona-raw.json")


def detect_scope(categoria: str) -> str:
    if categoria in NON_FOOD_CATEGORIES:
        return "non_food"
    if categoria in FOOD_CATEGORIES:
        return "food"
    return "unknown"


def infer_brand(nombre: str) -> str | None:
    for brand in BRANDS:
        if brand.lower() in (nombre or "").lower():
            return brand
    return None


def tokenize(*values: str) -> list[str]:
    seen: set[str] = set()
    tokens: list[str] = []
    for value in values:
        for raw in TOKEN_RE.findall(normalize(value)):
            if raw not in seen:
                seen.add(raw)
                tokens.append(raw)
    return tokens


def build_search_terms(base: dict, src: dict | None, ingredientes: str | None, aditivos: list[str], scope: str) -> list[str]:
    pieces = [
        base.get("nombre"),
        base.get("categoria"),
        base.get("subcategoria"),
        ingredientes or "",
        base.get("ean") or "",
        base.get("id") or "",
    ]
    if src:
        pieces.extend(
            [
                src.get("nombre"),
                src.get("categoria"),
                src.get("subcategoria"),
            ]
        )
    brand = infer_brand(base.get("nombre") or "")
    if brand:
        pieces.append(brand)
    pieces.extend(aditivos)
    pieces.append("alimentacion" if scope == "food" else "no alimentario")
    return tokenize(*[str(piece or "") for piece in pieces])[:80]


def llm_overlay_index() -> dict[tuple[str, str], dict]:
    if not LLM_ENRICHED_FILE.exists():
        return {}

    payload = repair_value(load_json(LLM_ENRICHED_FILE))
    index: dict[tuple[str, str], dict] = {}
    for row in payload:
        if not isinstance(row, dict):
            continue
        ean = str(row.get("ean") or "").strip()
        name = normalize(str(row.get("nombre") or ""))
        if ean:
            index[("ean", ean)] = row
        if name:
            index[("name", name)] = row
    return index


raw_file = choose_raw_file()
raw_products = repair_value(load_json(raw_file))
enriched_products = repair_value(load_json(ENRICHED_FILE))
llm_index = llm_overlay_index()

by_ean = {}
for item in enriched_products:
    if not isinstance(item, dict):
        continue
    ean = str(item.get("ean") or "").strip()
    if ean:
        by_ean[ean] = item

by_name_cat = {}
for item in enriched_products:
    if not isinstance(item, dict):
        continue
    key = (normalize(str(item.get("nombre", ""))), normalize(str(item.get("categoria", ""))))
    by_name_cat[key] = item

merged = []
for base in raw_products:
    if not isinstance(base, dict):
        continue
    ean = str(base.get("ean") or "").strip()
    src = by_ean.get(ean)
    if src is None:
        key = (normalize(str(base.get("nombre", ""))), normalize(str(base.get("categoria", ""))))
        src = by_name_cat.get(key)

    ingredientes = (base.get("ingredientes") or "").strip() or None
    aditivos_off = src.get("aditivos") if src else []
    if not isinstance(aditivos_off, list):
        aditivos_off = []
    aditivos_ingredientes = extract_additives(ingredientes or "")
    aditivos = aditivos_off or aditivos_ingredientes

    if src is not None:
        score_cobertura = "completa"
        fuente_datos = "off"
        aditivos_fuente = "off" if aditivos_off else ("ingredientes" if aditivos_ingredientes else None)
    elif ingredientes:
        score_cobertura = "parcial"
        fuente_datos = "mercadona"
        aditivos_fuente = "ingredientes" if aditivos_ingredientes else None
    else:
        score_cobertura = "sin_datos"
        fuente_datos = "mercadona"
        aditivos_fuente = None

    scope = detect_scope(str(base.get("categoria") or ""))
    brand = infer_brand(str(base.get("nombre") or ""))
    search_terms = build_search_terms(base, src, ingredientes, aditivos, scope)

    item = {
        "id": base.get("id"),
        "nombre": base.get("nombre"),
        "categoria": base.get("categoria"),
        "subcategoria": base.get("subcategoria"),
        "precio": base.get("precio"),
        "ean": ean or None,
        "marca": brand,
        "catalog_scope": scope,
        "score_aplicable": scope == "food",
        "ingredientes": ingredientes,
        "aditivos": aditivos,
        "aditivos_fuente": aditivos_fuente,
        "fuente_datos": fuente_datos,
        "score_disponible": score_cobertura != "sin_datos",
        "score_cobertura": score_cobertura,
        "search_terms": search_terms,
    }

    if src:
        for field in NUTRITION_FIELDS:
            item[field] = src.get(field)
    else:
        for field in NUTRITION_FIELDS:
            item[field] = None

    llm_row = llm_index.get(("ean", ean)) or llm_index.get(("name", normalize(str(base.get("nombre") or ""))))
    if isinstance(llm_row, dict):
        for key, value in llm_row.items():
            if key in {"ean", "nombre", "categoria"}:
                continue
            item[key] = value
        if isinstance(llm_row.get("ai_aliases"), list):
            item["search_terms"] = tokenize(
                *(item.get("search_terms") or []),
                *llm_row.get("ai_aliases"),
                *(llm_row.get("ai_keywords") or []),
            )[:100]

    merged.append(item)

OUTPUT_FILE.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

count_full = sum(1 for p in merged if p["score_cobertura"] == "completa")
count_partial = sum(1 for p in merged if p["score_cobertura"] == "parcial")
count_none = sum(1 for p in merged if p["score_cobertura"] == "sin_datos")
count_food = sum(1 for p in merged if p.get("catalog_scope") == "food")
count_non_food = sum(1 for p in merged if p.get("catalog_scope") == "non_food")
print(
    f"OK: {OUTPUT_FILE.name} generado con {len(merged)} productos "
    f"({count_full} completos, {count_partial} parciales, {count_none} sin datos; "
    f"{count_food} food, {count_non_food} non-food)"
)
