"""
audit_frozen_predictions.py — Audita y recupera predicciones pre-partido desde git.

Para cada partido completado, muestra la evolución de la predicción a través de los
commits de git y la compara con el valor actualmente congelado en prediction_overrides.json.

Uso:
  python scripts/audit_frozen_predictions.py              # todos los partidos R16+
  python scripts/audit_frozen_predictions.py --match 93  # solo M93
  python scripts/audit_frozen_predictions.py --fix        # sobreescribe con el valor del último pre-partido

Opciones:
  --match N      Auditar solo el partido número N
  --from-match N Auditar desde el partido N en adelante (default: 89)
  --fix          Restaurar valores del último commit pre-partido para partidos divergentes
  --verbose      Mostrar todos los commits, no solo los relevantes
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OVERRIDES_PATH = PROJECT_ROOT / "data" / "ui" / "prediction_overrides.json"
OVERRIDES_GIT_PATH = "data/ui/prediction_overrides.json"


def git_log_file(git_path: str) -> list[tuple[str, str, str]]:
    """Returns list of (hash, iso_date, subject) for all commits touching the file."""
    result = subprocess.run(
        ["git", "log", "--format=%H %ai %s", "--follow", "--", git_path],
        capture_output=True, text=True, cwd=PROJECT_ROOT, encoding="utf-8",
    )
    commits = []
    for line in result.stdout.strip().splitlines():
        parts = line.split(" ", 2)
        if len(parts) == 3:
            commits.append((parts[0], parts[1], parts[2]))
    return commits


def git_show_json(commit: str, git_path: str) -> dict | None:
    result = subprocess.run(
        ["git", "show", f"{commit}:{git_path}"],
        capture_output=True, text=True, cwd=PROJECT_ROOT, encoding="utf-8",
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def get_match_entry(data: dict, match_num: str) -> dict | None:
    matches = data.get("matches", data)
    return matches.get(match_num)


def format_pick(entry: dict | None) -> str:
    if not entry:
        return "(sin datos)"
    qp = entry.get("quiniela_pick") or {}
    score = qp.get("score", "?")
    ep = qp.get("expected_points")
    frozen = entry.get("frozen_pick", False)
    ep_str = f" ep={ep:.4f}" if isinstance(ep, float) else ""
    frozen_str = " [FROZEN]" if frozen else ""
    return f"{score}{ep_str}{frozen_str}"


def audit_match(
    match_num: str,
    commits: list[tuple[str, str, str]],
    verbose: bool = False,
) -> dict:
    """
    Returns audit result for a single match:
      - history: list of (commit, date, subject, entry) for commits where the entry changed
      - current_frozen: current entry in prediction_overrides.json
      - last_pre_result: entry from the last commit BEFORE the result was recorded
      - result_commit: commit where frozen_pick first became True
    """
    history = []
    prev_score = None
    result_commit_idx = None

    for i, (h, date, subject) in enumerate(commits):
        data = git_show_json(h, OVERRIDES_GIT_PATH)
        if data is None:
            continue
        entry = get_match_entry(data, match_num)
        if entry is None:
            continue

        qp = entry.get("quiniela_pick") or {}
        score = qp.get("score")
        frozen = entry.get("frozen_pick", False)

        changed = (score != prev_score)
        if changed or verbose:
            history.append((h, date, subject, entry))
        prev_score = score

        if frozen and result_commit_idx is None:
            result_commit_idx = i

    current_data = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    current_entry = get_match_entry(current_data, match_num)

    # "last pre-result" = commit just before the result commit
    last_pre_result_entry = None
    if result_commit_idx is not None and result_commit_idx + 1 < len(commits):
        pre_h = commits[result_commit_idx + 1][0]
        pre_data = git_show_json(pre_h, OVERRIDES_GIT_PATH)
        if pre_data:
            last_pre_result_entry = get_match_entry(pre_data, match_num)

    return {
        "match_num": match_num,
        "history": history,
        "current_entry": current_entry,
        "last_pre_result_entry": last_pre_result_entry,
        "result_commit_idx": result_commit_idx,
        "result_commit": commits[result_commit_idx] if result_commit_idx is not None else None,
    }


def print_audit(result: dict, show_history: bool = True) -> bool:
    """Prints the audit and returns True if there's a divergence."""
    match_num = result["match_num"]
    current = result["current_entry"]
    pre = result["last_pre_result_entry"]

    current_str = format_pick(current)
    pre_str = format_pick(pre) if pre else "(no encontrado)"

    frozen = current and current.get("frozen_pick", False)
    if not frozen:
        print(f"  M{match_num}: NO CONGELADO aún — pick actual: {current_str}")
        return False

    rc = result.get("result_commit")
    rc_str = f"{rc[0][:8]} {rc[1][:10]} '{rc[2][:40]}'" if rc else "?"
    diverge = pre and (
        (current or {}).get("quiniela_pick", {}) != (pre or {}).get("quiniela_pick", {})
    )

    mark = " *** DIVERGE ***" if diverge else " OK"
    print(f"  M{match_num}:{mark}")
    print(f"    Congelado en : {rc_str}")
    print(f"    Valor actual : {current_str}")
    print(f"    Último pre-p : {pre_str}")

    if show_history and result["history"]:
        print(f"    Historial de cambios:")
        for h, date, subject, entry in result["history"]:
            print(f"      {h[:8]} {date[:10]}  {format_pick(entry):30s}  {subject[:50]}")

    print()
    return bool(diverge)


def fix_match(match_num: str, pre_entry: dict) -> None:
    """Overwrites the frozen entry for match_num with the pre-result entry."""
    data = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    matches = data.get("matches", {})
    if match_num not in matches:
        print(f"  M{match_num}: no encontrado en prediction_overrides.json")
        return
    pre_entry["frozen_pick"] = True
    matches[match_num] = pre_entry
    data["matches"] = matches
    OVERRIDES_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"  M{match_num}: restaurado → {format_pick(pre_entry)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--match", type=int, default=None, help="Auditar solo este partido")
    parser.add_argument("--from-match", type=int, default=89, dest="from_match",
                        help="Auditar desde el partido N en adelante (default: 89)")
    parser.add_argument("--fix", action="store_true", help="Restaurar valores pre-partido para divergencias")
    parser.add_argument("--verbose", action="store_true", help="Mostrar todos los commits, no solo cambios")
    args = parser.parse_args()

    print("Cargando historial de commits de git...")
    commits = git_log_file(OVERRIDES_GIT_PATH)
    print(f"  {len(commits)} commits encontrados\n")

    current_data = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    matches = current_data.get("matches", {})

    if args.match:
        target_matches = [str(args.match)]
    else:
        target_matches = [
            k for k in matches
            if k.isdigit() and int(k) >= args.from_match
            and matches[k].get("frozen_pick", False)
        ]
        target_matches = sorted(target_matches, key=int)

    print(f"Auditando {len(target_matches)} partidos congelados (M{target_matches[0] if target_matches else '?'} – M{target_matches[-1] if target_matches else '?'}):\n")

    divergences = []
    for m in target_matches:
        result = audit_match(m, commits, verbose=args.verbose)
        has_diverge = print_audit(result, show_history=True)
        if has_diverge:
            divergences.append((m, result["last_pre_result_entry"]))

    if divergences:
        print(f"\n{'='*60}")
        print(f"  {len(divergences)} divergencias encontradas: M{', M'.join(d[0] for d in divergences)}")
        if args.fix:
            print("\nAplicando correcciones...")
            for match_num, pre_entry in divergences:
                if pre_entry:
                    fix_match(match_num, pre_entry)
                else:
                    print(f"  M{match_num}: no se encontró entrada pre-partido, omitiendo")
            print("\nPrediction_overrides.json actualizado. Regenera el dashboard:")
            print("  python scripts/generate_dashboard.py")
        else:
            print("\n  Usa --fix para restaurar los valores pre-partido.")
    else:
        print("Ninguna divergencia encontrada. Todos los valores congelados coinciden con el último pre-partido.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
