"""
Lee todos los CSV de curated_inputs/quinielas/ y genera data/ui/friends_quinielas.json.

Formato del CSV (ver TEMPLATE.csv):
  Fila 1: nombre,[Nombre del participante]
  Fila 2+: [numero_partido],[goles_local],[goles_visitante]

El numero de partido va del 1 al 104 segun el fixture oficial.
Puedes dejar partidos sin completar; solo se cuentan los que esten presentes.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
QUINIELAS_DIR = PROJECT_ROOT / "curated_inputs" / "quinielas"
OUTPUT_PATH   = PROJECT_ROOT / "data" / "ui" / "friends_quinielas.json"


def _slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "participante"


def _parse_csv(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8-sig")
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        return None

    first = [c.strip() for c in rows[0]]
    if len(first) < 2 or first[0].lower() != "nombre":
        print(f"  [skip] {path.name}: la primera fila debe ser 'nombre,[nombre]'")
        return None

    name = first[1].strip() or path.stem
    picks: dict[str, str] = {}

    for line_no, row in enumerate(rows[1:], start=2):
        if not row or not row[0].strip() or row[0].strip().startswith("#"):
            continue
        try:
            match_id = str(int(row[0].strip()))
            home     = int(row[1].strip())
            away     = int(row[2].strip())
            picks[match_id] = f"{home}-{away}"
        except (ValueError, IndexError):
            print(f"  [warn] {path.name}:{line_no} fila ignorada: {row}")

    return {"id": _slugify(name), "name": name, "picks": picks, "source": path.name}


def main() -> int:
    if not QUINIELAS_DIR.exists():
        print(f"Carpeta no encontrada: {QUINIELAS_DIR}")
        return 1

    csv_files = sorted(
        p for p in QUINIELAS_DIR.glob("*.csv")
        if p.name.upper() not in ("TEMPLATE.CSV",)
    )
    if not csv_files:
        print("No se encontraron CSVs (excluido TEMPLATE.csv). Crea uno por participante.")
        # Write empty output so dashboard no falla
        _write({"generated_at": _now(), "friends": []})
        return 0

    friends: list[dict] = []
    seen_ids: set[str] = set()

    for path in csv_files:
        print(f"  Procesando: {path.name}")
        result = _parse_csv(path)
        if not result:
            continue
        # Desambiguar IDs duplicados
        base = result["id"]
        uid  = base
        n    = 2
        while uid in seen_ids:
            uid = f"{base}-{n}"
            n  += 1
        result["id"] = uid
        seen_ids.add(uid)
        friends.append(result)
        print(f"    OK {result['name']}: {len(result['picks'])} pronosticos")

    _write({"generated_at": _now(), "friends": friends})
    print(f"\nGenerado: {OUTPUT_PATH}")
    print(f"Participantes: {len(friends)}")
    return 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write(data: dict) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
