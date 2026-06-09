from __future__ import annotations

import argparse
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    predictions_path = Path(args.predictions)
    friends_path = Path(args.friends)
    result = generate_dashboard(
        db_path=Path(args.db),
        project_root=PROJECT_ROOT,
        output_path=Path(args.output),
        predictions_path=predictions_path if predictions_path.exists() else None,
        friends_path=friends_path if friends_path.exists() else None,
    )
    print(f"dashboard: {result.output_path}")
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

