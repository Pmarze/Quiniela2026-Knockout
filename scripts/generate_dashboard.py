from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from quiniela.ui import generate_dashboard


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Genera el dashboard local HTML desde el ultimo estado del torneo."
    )
    parser.add_argument(
        "--db",
        default=str(PROJECT_ROOT / "data" / "quiniela.db"),
        help="Ruta de la base SQLite.",
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "docs" / "index.html"),
        help="Ruta del HTML generado.",
    )
    parser.add_argument(
        "--predictions",
        default=str(PROJECT_ROOT / "data" / "ui" / "prediction_overrides.json"),
        help="JSON opcional con pronosticos y picks por match_id.",
    )
    parser.add_argument(
        "--friends",
        default=str(PROJECT_ROOT / "data" / "ui" / "friends_quinielas.json"),
        help="JSON con quinielas de amigos generado por scripts/build_friends_quinielas.py.",
    )
    parser.add_argument(
        "--include-private",
        action="store_true",
        help="Compatibilidad: el dashboard ya incluye amigos por defecto si el JSON existe.",
    )
    parser.add_argument(
        "--exclude-friends",
        "--public",
        dest="exclude_friends",
        action="store_true",
        help="Genera una version sin quinielas de amigos.",
    )
    parser.add_argument(
        "--private-access-hash",
        default=os.environ.get("QUINIELA_PRIVATE_ACCESS_HASH", ""),
        help="Hash SHA-256 opcional para bloquear secciones privadas en builds locales.",
    )
    parser.add_argument(
        "--scoring-config",
        default=str(PROJECT_ROOT / "configs" / "scoring.yaml"),
        help="YAML con perfiles de scoring.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    predictions_path = Path(args.predictions)
    friends_path = Path(args.friends)
    scoring_config_path = Path(args.scoring_config)
    public_mode = bool(args.exclude_friends)
    result = generate_dashboard(
        db_path=Path(args.db),
        project_root=PROJECT_ROOT,
        output_path=Path(args.output),
        predictions_path=predictions_path if predictions_path.exists() else None,
        friends_path=friends_path if not public_mode and friends_path.exists() else None,
        scoring_config_path=scoring_config_path if scoring_config_path.exists() else None,
        public_mode=public_mode,
        private_access_hash=args.private_access_hash if not public_mode else "",
    )
    print(f"dashboard: {result.output_path}")
    print(f"mode: {'without-friends' if public_mode else 'with-friends'}")
    print(f"state_id: {result.state_id}")
    print(f"as_of_utc: {result.as_of_utc}")
    print(f"matches: {result.total_matches}")
    print(f"completed: {result.completed_matches}")
    print(f"pending: {result.pending_matches}")
    print(f"teams: {result.teams}")
    print(f"groups: {result.groups}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
