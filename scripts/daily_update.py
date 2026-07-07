"""
daily_update.py — Flujo diario completo de la Quiniela2026.

Pasos en orden:
  1. Descarga de datos + canónico + estado del torneo   (run_daily.py)
  2. Modelos de predicción → data/ui/prediction_overrides.json  (run_model.py)
  3. Quinielas de amigos → data/ui/friends_quinielas.json
  4. Dashboard HTML → docs/index.html                  (generate_dashboard.py)
  5. git add · commit · push → development

Uso habitual:
  python scripts/daily_update.py

Opciones:
  --skip-download    Omite descarga (usa la última ingesta guardada)
  --skip-models      Omite la ejecución de modelos
  --skip-friends     Omite la consulta a Google Sheets
  --skip-git         No hace commit ni push
  --dry-run          Muestra los pasos sin ejecutar ninguno
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Forzar UTF-8 en consola Windows (evita UnicodeEncodeError con caracteres españoles)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable  # mismo intérprete que ejecutó este script

# Archivos que se commitean en cada ejecución diaria
DAILY_GIT_FILES = [
    "docs/index.html",
    "data/ui/prediction_overrides.json",
    "data/ui/friends_quinielas.json",
    "configs/knockout.yaml",
]


# --------------------------------------------------------------
# Helpers
# --------------------------------------------------------------

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _banner(msg: str) -> None:
    print(f"\n{'-'*60}")
    print(f"  {msg}")
    print(f"{'-'*60}")


def _run(
    label: str,
    cmd: list[str],
    *,
    check: bool = True,
    warn_only: bool = False,
) -> int:
    """Ejecuta un comando, imprime su salida en tiempo real y devuelve el exit code."""
    _banner(f"[{_ts()}] {label}")
    print(f"  $ {' '.join(str(c) for c in cmd)}\n")
    t0 = time.monotonic()
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    elapsed = time.monotonic() - t0
    ok = result.returncode == 0
    status = "OK" if ok else f"ERROR (código {result.returncode})"
    print(f"\n  {status}  ({elapsed:.1f}s)")

    if not ok:
        if warn_only:
            print(f"  ADVERTENCIA: '{label}' falló pero el flujo continúa.")
        elif check:
            print(f"\nABORTADO: '{label}' falló. Corrige el error y vuelve a ejecutar.")
            sys.exit(result.returncode)
    return result.returncode


def _git_current_branch() -> str:
    r = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )
    return r.stdout.strip()


def _git_has_changes(files: list[str]) -> bool:
    r = subprocess.run(
        ["git", "status", "--porcelain", *files],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )
    return bool(r.stdout.strip())


def _git_diff_stat(files: list[str]) -> str:
    r = subprocess.run(
        ["git", "diff", "--stat", "HEAD", "--", *files],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )
    return r.stdout.strip()


# --------------------------------------------------------------
# CLI
# --------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Flujo diario: descarga → modelos → amigos → dashboard → git push.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--skip-download", action="store_true",
        help="Omite la descarga de datos (usa la última ingesta guardada en la BD).",
    )
    parser.add_argument(
        "--skip-models", action="store_true",
        help="Omite la ejecución de modelos (conserva prediction_overrides.json actual).",
    )
    parser.add_argument(
        "--skip-friends", action="store_true",
        help="Omite la consulta a Google Sheets (conserva friends_quinielas.json actual).",
    )
    parser.add_argument(
        "--skip-git", action="store_true",
        help="No hace commit ni push a development.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Muestra los pasos que se ejecutarían sin correr ninguno.",
    )
    return parser.parse_args()


# --------------------------------------------------------------
# Main
# --------------------------------------------------------------

def main() -> int:
    args = parse_args()
    run_start = time.monotonic()
    date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    date_tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    print(f"\n{'='*60}")
    print(f"  Quiniela2026 · daily_update · {date_utc}")
    print(f"{'='*60}")

    # -- Verificar que estamos en development ------------------
    branch = _git_current_branch()
    if branch != "development":
        print(f"\nERROR: rama actual = '{branch}'. Se requiere 'development'.")
        print("  Ejecuta: git checkout development")
        return 1
    print(f"\n  Rama: {branch} OK")

    # -- Dry-run: solo mostrar los pasos ----------------------
    if args.dry_run:
        print("\n[DRY-RUN] Pasos que se ejecutarían:\n")
        steps = []
        if not args.skip_download:
            steps.append("1. run_daily.py (descarga + canónico + estado)")
        if not args.skip_models:
            steps.append("1b. calibrate_knockout.py (goal_deflator, draw_inflation)")
            steps.append("1c. snapshot prediction_overrides.json → data/ui/snapshots/")
            steps.append("2. run_model.py (modelos → prediction_overrides.json)")
        if not args.skip_friends:
            steps.append("3. build_friends_quinielas.py (Google Sheets → friends_quinielas.json)")
        steps.append("4. generate_dashboard.py (→ docs/index.html)")
        if not args.skip_git:
            steps.append("5. git add · commit · push origin development")
        for s in steps:
            print(f"   - {s}")
        print()
        return 0

    # -- Paso 1: descarga + canónico + estado -----------------
    run_daily_cmd = [PYTHON, "scripts/run_daily.py", "--skip-dashboard"]
    if args.skip_download:
        run_daily_cmd.append("--skip-download")
    _run("Paso 1 · Descarga de datos + canónico + estado", run_daily_cmd)

    # -- Paso 1b: calibración knockout --------------------------
    if args.skip_models:
        _banner(f"[{_ts()}] Paso 1b · Calibración knockout: OMITIDO (--skip-models)")
    else:
        _run(
            "Paso 1b · Calibración knockout (goal_deflator, draw_inflation)",
            [PYTHON, "scripts/calibrate_knockout.py"],
            warn_only=True,
        )

    # -- Paso 1c: snapshot de predicciones previas -------------
    if not args.skip_models:
        ui_src = PROJECT_ROOT / "data" / "ui" / "prediction_overrides.json"
        if ui_src.exists():
            snap_dir = PROJECT_ROOT / "data" / "ui" / "snapshots"
            snap_dir.mkdir(parents=True, exist_ok=True)
            snap_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            snap_dst = snap_dir / f"prediction_overrides_{snap_ts}.json"
            shutil.copy2(ui_src, snap_dst)
            _banner(f"[{_ts()}] Paso 1c · Snapshot guardado: {snap_dst.name}")

    # -- Paso 2: modelos ---------------------------------------
    if args.skip_models:
        _banner(f"[{_ts()}] Paso 2 · Modelos: OMITIDO (--skip-models)")
    else:
        _run("Paso 2 · Modelos de predicción", [PYTHON, "scripts/run_model.py"])

    # -- Paso 3: amigos (Google Sheets) -----------------------
    if args.skip_friends:
        _banner(f"[{_ts()}] Paso 3 · Amigos: OMITIDO (--skip-friends)")
    else:
        _run(
            "Paso 3 · Quinielas amigos (Google Sheets)",
            [PYTHON, "scripts/build_friends_quinielas.py"],
            warn_only=True,  # fallo de red no aborta el flujo
        )

    # -- Paso 4: dashboard -------------------------------------
    _run("Paso 4 · Dashboard HTML", [PYTHON, "scripts/generate_dashboard.py"])

    # -- Paso 5: git -------------------------------------------
    if args.skip_git:
        _banner(f"[{_ts()}] Paso 5 · Git: OMITIDO (--skip-git)")
    else:
        _banner(f"[{_ts()}] Paso 5 · Git: commit + push → development")
        if not _git_has_changes(DAILY_GIT_FILES):
            print("  Sin cambios en los archivos generados. No se hace commit.")
        else:
            stat = _git_diff_stat(DAILY_GIT_FILES)
            if stat:
                print(f"  Cambios:\n{stat}\n")
            _run("git add", ["git", "add", *DAILY_GIT_FILES])
            _run("git commit", [
                "git", "commit", "-m",
                f"Daily update {date_tag}: datos · modelos · amigos · dashboard",
            ])
            _run("git push", ["git", "push", "origin", "development"])

    # -- Resumen -----------------------------------------------
    total = time.monotonic() - run_start
    print(f"\n{'='*60}")
    print(f"  Flujo completado en {total:.0f}s · {date_utc}")
    print(f"{'='*60}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
