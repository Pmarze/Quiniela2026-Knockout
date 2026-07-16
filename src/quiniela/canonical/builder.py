from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quiniela.storage.sqlite_store import SQLiteStore


GUATEMALA_TZ = ZoneInfo("America/Guatemala")
PRIMARY_SOURCE = "worldcup26_ir"

TEAM_ALIASES = {
    "usa": "united states",
    "u s a": "united states",
    "us": "united states",
    "bosnia herzegovina": "bosnia and herzegovina",
    "bosnia & herzegovina": "bosnia and herzegovina",
    "curacao": "curacao",
    "cote d ivoire": "ivory coast",
    "cote divoire": "ivory coast",
    "dr congo": "democratic republic of the congo",
    "democratic republic congo": "democratic republic of the congo",
    "south korea republic": "south korea",
}


@dataclass(frozen=True)
class CanonicalBuildResult:
    canonical_run_id: str
    reconciliation_run_id: str
    source_run_id: str
    as_of_utc: str
    teams: int
    matches: int
    sources_checked: int
    reconciliation_issues: int


def build_canonical_dataset(
    db_path: Path,
    project_root: Path,
    timezone_config_path: Path | None = None,
    source_run_id: str | None = None,
    primary_source_name: str = PRIMARY_SOURCE,
) -> CanonicalBuildResult:
    timezone_config = _load_timezone_config(timezone_config_path)
    store = SQLiteStore(db_path)
    store.initialize()
    conn = store.conn
    try:
        run = _resolve_source_run(conn, source_run_id)
        canonical_run_id = f"canonical_{_compact_timestamp(run['as_of_utc'])}_{uuid.uuid4().hex[:8]}"
        teams = _build_canonical_teams(conn, canonical_run_id, primary_source_name)
        matches = _build_canonical_matches(
            conn=conn,
            canonical_run_id=canonical_run_id,
            primary_source_name=primary_source_name,
            timezone_config=timezone_config,
            teams_by_source_id={row["primary_source_team_id"]: row for row in teams},
        )
        _resolve_placeholder_teams(conn, matches, teams)
        _enrich_results_from_secondary_sources(conn, matches, run["as_of_utc"])
        _write_canonical_rows(
            conn=conn,
            canonical_run_id=canonical_run_id,
            source_run_id=run["run_id"],
            as_of_utc=run["as_of_utc"],
            primary_source_name=primary_source_name,
            teams=teams,
            matches=matches,
        )
        reconciliation_run_id, issues = _run_reconciliation(
            conn=conn,
            canonical_run_id=canonical_run_id,
            as_of_utc=run["as_of_utc"],
            primary_source_name=primary_source_name,
            canonical_matches=matches,
        )
        return CanonicalBuildResult(
            canonical_run_id=canonical_run_id,
            reconciliation_run_id=reconciliation_run_id,
            source_run_id=run["run_id"],
            as_of_utc=run["as_of_utc"],
            teams=len(teams),
            matches=len(matches),
            sources_checked=_count_sources(conn),
            reconciliation_issues=len(issues),
        )
    finally:
        store.close()


def _resolve_source_run(conn: sqlite3.Connection, source_run_id: str | None) -> sqlite3.Row:
    if source_run_id:
        row = conn.execute("SELECT * FROM ingestion_runs WHERE run_id = ?", (source_run_id,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM v_latest_completed_run").fetchone()
    if row is None:
        raise RuntimeError("No hay una corrida de ingesta completada para canonicalizar.")
    return row


def _load_timezone_config(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _build_canonical_teams(
    conn: sqlite3.Connection,
    canonical_run_id: str,
    primary_source_name: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT group_name, team_source_id, team_name, fifa_code
        FROM v_worldcup26_group_standings
        ORDER BY group_name, CAST(team_source_id AS INTEGER)
        """
    ).fetchall()
    teams = []
    for row in rows:
        fifa_code = row["fifa_code"]
        canonical_team_id = f"team_{_slug(fifa_code or row['team_name'])}"
        aliases = sorted({row["team_name"], _normalize_team_name(row["team_name"])})
        teams.append(
            {
                "canonical_team_id": canonical_team_id,
                "display_name": row["team_name"],
                "fifa_code": fifa_code,
                "group_name": row["group_name"],
                "primary_source_name": primary_source_name,
                "primary_source_team_id": row["team_source_id"],
                "aliases_json": json.dumps(aliases, ensure_ascii=False),
                "canonical_run_id": canonical_run_id,
                "updated_at_utc": _utc_now(),
            }
        )
    if len(teams) != 48:
        raise RuntimeError(f"Se esperaban 48 equipos canónicos, encontrados {len(teams)}.")
    return teams


def _build_canonical_matches(
    conn: sqlite3.Connection,
    canonical_run_id: str,
    primary_source_name: str,
    timezone_config: dict[str, dict[str, str]],
    teams_by_source_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM v_worldcup26_matches
        ORDER BY COALESCE(match_number, CAST(source_match_id AS INTEGER))
        """
    ).fetchall()
    matches = []
    for row in rows:
        match_number = int(row["match_number"] or row["source_match_id"])
        stadium_source_id = str(row["stadium_source_id"] or "")
        kickoff = _normalize_worldcup26_kickoff(
            kickoff_local=row["kickoff_local"],
            stadium_source_id=stadium_source_id,
            timezone_config=timezone_config,
            primary_source_name=primary_source_name,
        )
        team_a = teams_by_source_id.get(str(row["team_a_source_id"] or ""))
        team_b = teams_by_source_id.get(str(row["team_b_source_id"] or ""))
        is_completed = _is_completed(row["status"], row["finished"], row["home_score"], row["away_score"])
        matches.append(
            {
                "canonical_match_id": f"wc2026_{match_number:03d}",
                "match_number": match_number,
                "stage": row["stage"],
                "group_name": row["group_name"],
                "matchday": row["matchday"],
                "primary_source_name": primary_source_name,
                "primary_source_match_id": row["source_match_id"],
                "kickoff_local_raw": row["kickoff_local"],
                "kickoff_local_iso": kickoff["kickoff_local_iso"],
                "kickoff_utc": kickoff["kickoff_utc"],
                "kickoff_timezone": kickoff["kickoff_timezone"],
                "kickoff_guatemala": kickoff["kickoff_guatemala"],
                "team_a_canonical_id": team_a["canonical_team_id"] if team_a else None,
                "team_b_canonical_id": team_b["canonical_team_id"] if team_b else None,
                "team_a_primary_source_id": row["team_a_source_id"],
                "team_b_primary_source_id": row["team_b_source_id"],
                "team_a_name": row["team_a_name"],
                "team_b_name": row["team_b_name"],
                "team_a_fifa_code": row["team_a_fifa_code"],
                "team_b_fifa_code": row["team_b_fifa_code"],
                "stadium_source_id": stadium_source_id,
                "stadium_name": row["stadium_name"],
                "stadium_city": row["stadium_city"],
                "stadium_country": row["stadium_country"],
                "home_score": row["home_score"],
                "away_score": row["away_score"],
                "status": "completed" if is_completed else "scheduled",
                "source_status": row["status"],
                "is_completed": 1 if is_completed else 0,
                "canonical_run_id": canonical_run_id,
                "updated_at_utc": _utc_now(),
            }
        )
    if len(matches) != 104:
        raise RuntimeError(f"Se esperaban 104 partidos canónicos, encontrados {len(matches)}.")
    return matches


def _resolve_placeholder_teams(
    conn: sqlite3.Connection,
    matches: list[dict[str, Any]],
    teams: list[dict[str, Any]],
) -> None:
    teams_by_name = {t["display_name"]: t for t in teams}
    for m in matches:
        if m["team_a_canonical_id"] and m["team_b_canonical_id"]:
            continue
        row = conn.execute(
            """
            SELECT team_a_name, team_b_name FROM matches
            WHERE match_number = ?
              AND team_a_name IS NOT NULL AND team_b_name IS NOT NULL
              AND team_a_name NOT LIKE '%Match %' AND team_b_name NOT LIKE '%Match %'
            LIMIT 1
            """,
            (m["match_number"],),
        ).fetchone()
        if not row:
            continue
        for side, col_name, col_canon, col_src, col_fifa in [
            ("a", "team_a_name", "team_a_canonical_id", "team_a_primary_source_id", "team_a_fifa_code"),
            ("b", "team_b_name", "team_b_canonical_id", "team_b_primary_source_id", "team_b_fifa_code"),
        ]:
            name = row[f"team_{side}_name"]
            team = teams_by_name.get(name) or teams_by_name.get(_normalize_team_name(name))
            if team:
                m[col_name] = team["display_name"]
                m[col_canon] = team["canonical_team_id"]
                m[col_src] = team["primary_source_team_id"]
                m[col_fifa] = team["fifa_code"]


def _write_canonical_rows(
    conn: sqlite3.Connection,
    canonical_run_id: str,
    source_run_id: str,
    as_of_utc: str,
    primary_source_name: str,
    teams: list[dict[str, Any]],
    matches: list[dict[str, Any]],
) -> None:
    with conn:
        conn.execute("DELETE FROM canonical_teams")
        conn.execute("DELETE FROM canonical_matches")
        conn.execute("DELETE FROM canonical_build_runs WHERE canonical_run_id = ?", (canonical_run_id,))
        conn.execute(
            """
            INSERT INTO canonical_build_runs (
                canonical_run_id, source_run_id, as_of_utc, primary_source_name,
                created_at_utc, teams, matches, status, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                canonical_run_id,
                source_run_id,
                as_of_utc,
                primary_source_name,
                _utc_now(),
                len(teams),
                len(matches),
                "completed",
                "canonical data built from primary source and normalized kickoff times",
            ),
        )
        conn.executemany(
            """
            INSERT INTO canonical_teams (
                canonical_team_id, display_name, fifa_code, group_name,
                primary_source_name, primary_source_team_id, aliases_json,
                canonical_run_id, updated_at_utc
            )
            VALUES (
                :canonical_team_id, :display_name, :fifa_code, :group_name,
                :primary_source_name, :primary_source_team_id, :aliases_json,
                :canonical_run_id, :updated_at_utc
            )
            """,
            teams,
        )
        conn.executemany(
            """
            INSERT INTO canonical_matches (
                canonical_match_id, match_number, stage, group_name, matchday,
                primary_source_name, primary_source_match_id, kickoff_local_raw,
                kickoff_local_iso, kickoff_utc, kickoff_timezone, kickoff_guatemala,
                team_a_canonical_id, team_b_canonical_id, team_a_primary_source_id,
                team_b_primary_source_id, team_a_name, team_b_name, team_a_fifa_code,
                team_b_fifa_code, stadium_source_id, stadium_name, stadium_city,
                stadium_country, home_score, away_score, status, source_status,
                is_completed, canonical_run_id, updated_at_utc
            )
            VALUES (
                :canonical_match_id, :match_number, :stage, :group_name, :matchday,
                :primary_source_name, :primary_source_match_id, :kickoff_local_raw,
                :kickoff_local_iso, :kickoff_utc, :kickoff_timezone, :kickoff_guatemala,
                :team_a_canonical_id, :team_b_canonical_id, :team_a_primary_source_id,
                :team_b_primary_source_id, :team_a_name, :team_b_name, :team_a_fifa_code,
                :team_b_fifa_code, :stadium_source_id, :stadium_name, :stadium_city,
                :stadium_country, :home_score, :away_score, :status, :source_status,
                :is_completed, :canonical_run_id, :updated_at_utc
            )
            """,
            matches,
        )


def _run_reconciliation(
    conn: sqlite3.Connection,
    canonical_run_id: str,
    as_of_utc: str,
    primary_source_name: str,
    canonical_matches: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    reconciliation_run_id = f"reconcile_{_compact_timestamp(as_of_utc)}_{uuid.uuid4().hex[:8]}"
    issues = _build_reconciliation_issues(conn, reconciliation_run_id, canonical_matches, primary_source_name)
    with conn:
        conn.execute(
            """
            INSERT INTO reconciliation_runs (
                reconciliation_run_id, canonical_run_id, as_of_utc, created_at_utc,
                primary_source_name, sources_checked, issues_found, status, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reconciliation_run_id,
                canonical_run_id,
                as_of_utc,
                _utc_now(),
                primary_source_name,
                _count_sources(conn),
                len(issues),
                "completed",
                "initial source reconciliation by counts, team signatures and kickoff UTC",
            ),
        )
        conn.executemany(
            """
            INSERT INTO reconciliation_issues (
                reconciliation_run_id, issue_id, severity, source_name, issue_type,
                canonical_match_id, source_match_id, message, payload_json, created_at_utc
            )
            VALUES (
                :reconciliation_run_id, :issue_id, :severity, :source_name, :issue_type,
                :canonical_match_id, :source_match_id, :message, :payload_json, :created_at_utc
            )
            """,
            issues,
        )
    return reconciliation_run_id, issues


def _build_reconciliation_issues(
    conn: sqlite3.Connection,
    reconciliation_run_id: str,
    canonical_matches: list[dict[str, Any]],
    primary_source_name: str,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    source_counts = conn.execute(
        "SELECT source_name, COUNT(*) AS n FROM matches GROUP BY source_name ORDER BY source_name"
    ).fetchall()
    for row in source_counts:
        expected = 104 if row["source_name"] != "rezarahiminia_static_csv" else 72
        if row["n"] != expected:
            issues.append(
                _issue(
                    reconciliation_run_id,
                    len(issues) + 1,
                    "warning",
                    row["source_name"],
                    "match_count_mismatch",
                    None,
                    None,
                    f"Fuente tiene {row['n']} partidos; esperado operativo {expected}.",
                    {"count": row["n"], "expected": expected},
                )
            )

    canonical_by_signature = {
        _match_signature(row["team_a_name"], row["team_b_name"], row["kickoff_utc"]): row
        for row in canonical_matches
        if row["team_a_canonical_id"] and row["team_b_canonical_id"] and row["kickoff_utc"]
    }

    for source_name in _source_names(conn):
        if source_name == primary_source_name:
            continue
        rows = conn.execute(
            """
            SELECT source_match_id, team_a_name, team_b_name, kickoff_local, status
            FROM matches
            WHERE source_name = ?
            """,
            (source_name,),
        ).fetchall()
        matched = 0
        for row in rows:
            source_utc = _parse_source_kickoff_utc(row["kickoff_local"])
            if not source_utc or not row["team_a_name"] or not row["team_b_name"]:
                continue
            signature = _match_signature(row["team_a_name"], row["team_b_name"], source_utc)
            canonical = canonical_by_signature.get(signature)
            if canonical is None:
                if _looks_like_group_match(row["team_a_name"], row["team_b_name"]):
                    issues.append(
                        _issue(
                            reconciliation_run_id,
                            len(issues) + 1,
                            "info",
                            source_name,
                            "unmatched_source_match",
                            None,
                            row["source_match_id"],
                            "No se encontro match canonico por equipos y fecha UTC.",
                            {
                                "team_a_name": row["team_a_name"],
                                "team_b_name": row["team_b_name"],
                                "kickoff_local": row["kickoff_local"],
                            },
                        )
                    )
                continue
            matched += 1
            canonical_utc = _parse_iso_utc(canonical["kickoff_utc"])
            if canonical_utc and abs((source_utc - canonical_utc).total_seconds()) > 60:
                issues.append(
                    _issue(
                        reconciliation_run_id,
                        len(issues) + 1,
                        "warning",
                        source_name,
                        "kickoff_time_mismatch",
                        canonical["canonical_match_id"],
                        row["source_match_id"],
                        "El kickoff UTC no coincide entre fuente secundaria y canonica.",
                        {
                            "canonical_utc": canonical["kickoff_utc"],
                            "source_kickoff_local": row["kickoff_local"],
                            "source_utc": source_utc.isoformat().replace("+00:00", "Z"),
                        },
                    )
                )
        if rows and matched == 0:
            severity = "info" if source_name.endswith("_csv") else "warning"
            issues.append(
                _issue(
                    reconciliation_run_id,
                    len(issues) + 1,
                    severity,
                    source_name,
                    "no_matches_reconciled",
                    None,
                    None,
                    "No se pudo reconciliar ningun partido de esta fuente con el canon actual.",
                    {"rows": len(rows)},
                )
            )
    return issues


def _issue(
    reconciliation_run_id: str,
    issue_number: int,
    severity: str,
    source_name: str,
    issue_type: str,
    canonical_match_id: str | None,
    source_match_id: str | None,
    message: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "reconciliation_run_id": reconciliation_run_id,
        "issue_id": f"issue_{issue_number:04d}",
        "severity": severity,
        "source_name": source_name,
        "issue_type": issue_type,
        "canonical_match_id": canonical_match_id,
        "source_match_id": source_match_id,
        "message": message,
        "payload_json": json.dumps(payload, ensure_ascii=False, sort_keys=True),
        "created_at_utc": _utc_now(),
    }


def _normalize_worldcup26_kickoff(
    kickoff_local: str | None,
    stadium_source_id: str,
    timezone_config: dict[str, dict[str, str]],
    primary_source_name: str,
) -> dict[str, str | None]:
    if not kickoff_local:
        return {
            "kickoff_local_iso": None,
            "kickoff_utc": None,
            "kickoff_timezone": None,
            "kickoff_guatemala": None,
        }
    tz_name = timezone_config.get(primary_source_name, {}).get(stadium_source_id)
    if not tz_name:
        raise RuntimeError(f"No hay timezone configurado para stadium_source_id={stadium_source_id}.")
    local_naive = datetime.strptime(kickoff_local, "%m/%d/%Y %H:%M")
    local_dt = local_naive.replace(tzinfo=ZoneInfo(tz_name))
    utc_dt = local_dt.astimezone(timezone.utc)
    gt_dt = local_dt.astimezone(GUATEMALA_TZ)
    return {
        "kickoff_local_iso": local_dt.isoformat(),
        "kickoff_utc": utc_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "kickoff_timezone": tz_name,
        "kickoff_guatemala": gt_dt.replace(microsecond=0).isoformat(),
    }


def _parse_source_kickoff_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    openfootball_match = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})\s+UTC([+-]\d{1,2})$", value)
    if openfootball_match:
        date_text, time_text, offset_text = openfootball_match.groups()
        local_naive = datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M")
        offset = timezone(timedelta(hours=int(offset_text)))
        return local_naive.replace(tzinfo=offset).astimezone(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _match_signature(team_a: str | None, team_b: str | None, kickoff_utc: str | datetime | None) -> tuple[str, str, str] | None:
    if not team_a or not team_b or not kickoff_utc:
        return None
    if isinstance(kickoff_utc, str):
        parsed = _parse_iso_utc(kickoff_utc)
    else:
        parsed = kickoff_utc.astimezone(timezone.utc)
    if parsed is None:
        return None
    teams = sorted((_normalize_team_name(team_a), _normalize_team_name(team_b)))
    return teams[0], teams[1], parsed.date().isoformat()


def _normalize_team_name(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    ascii_text = ascii_text.lower().replace("&", " and ")
    ascii_text = re.sub(r"[^a-z0-9]+", " ", ascii_text).strip()
    ascii_text = re.sub(r"\s+", " ", ascii_text)
    return TEAM_ALIASES.get(ascii_text, ascii_text)


def _slug(value: str | None) -> str:
    normalized = _normalize_team_name(value)
    return re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")


def _is_completed(status: Any, finished: Any, home_score: Any, away_score: Any) -> bool:
    if finished == 1:
        return True
    text = str(status or "").strip().lower()
    if text in {"scheduled", "not_started", "not started", "upcoming", "fixture", "pending"}:
        return False
    if text in {"finished", "completed", "played", "ft", "full_time", "final"}:
        return True
    return home_score is not None and away_score is not None


def _enrich_results_from_secondary_sources(
    conn: sqlite3.Connection,
    matches: list[dict[str, Any]],
    as_of_utc: str,
) -> None:
    """Patch result data into canonical match dicts for games still without results.

    The primary source (worldcup26_ir) uses numeric team IDs and its match_number
    ordering may differ from secondary sources (openfootball). Secondary sources
    use team names as source_team_id. We match by normalizing both team names and
    comparing pairs regardless of position in the match list.

    Only secondary rows with kickoff_local <= as_of_utc are used to prevent
    incorrect future-match results from contaminating the canonical dataset.
    """
    # Collect secondary-source rows that have a result (finished=1, scores not null)
    # and whose kickoff is not in the future relative to the ingestion cutoff.
    rows = conn.execute(
        """
        SELECT
            source_name,
            team_a_source_id,
            team_b_source_id,
            home_score,
            away_score,
            status,
            finished,
            kickoff_local
        FROM matches
        WHERE source_name != 'worldcup26_ir'
          AND finished = 1
          AND home_score IS NOT NULL
          AND away_score IS NOT NULL
          AND (
              kickoff_local IS NULL
              OR substr(kickoff_local, 1, 10) <= ?
          )
        ORDER BY source_name, match_number
        """,
        (as_of_utc[:10],),  # compare YYYY-MM-DD prefix; as_of_utc is ISO-8601
    ).fetchall()

    # Build lookup: normalized sorted team pair -> (home_score, away_score, status, orig_a_name)
    # orig_a_name lets us restore correct home/away orientation.
    secondary: dict[tuple[str, str], tuple[int, int, str, str]] = {}
    for row in rows:
        na = _normalize_team_name(str(row["team_a_source_id"] or ""))
        nb = _normalize_team_name(str(row["team_b_source_id"] or ""))
        if not na or not nb:
            continue
        key = (na, nb) if na <= nb else (nb, na)
        if key not in secondary:
            secondary[key] = (
                int(row["home_score"]),
                int(row["away_score"]),
                str(row["status"] or "finished"),
                na,  # original a-side normalized name
            )

    enriched = 0
    for m in matches:
        if m.get("is_completed"):
            continue
        na = _normalize_team_name(str(m.get("team_a_name") or m.get("team_a_primary_source_id") or ""))
        nb = _normalize_team_name(str(m.get("team_b_name") or m.get("team_b_primary_source_id") or ""))
        if not na or not nb:
            continue

        key = (na, nb) if na <= nb else (nb, na)
        result = secondary.get(key)
        if result is None:
            continue

        h_score, a_score, src_status, orig_na = result
        # Restore home/away orientation relative to canonical team_a
        if orig_na == na:
            m["home_score"] = h_score
            m["away_score"] = a_score
        else:
            m["home_score"] = a_score
            m["away_score"] = h_score

        m["status"] = "completed"
        m["source_status"] = src_status
        m["is_completed"] = 1
        enriched += 1

    if enriched:
        print(f"[enrich] Patched {enriched} matches with secondary-source results.")


def _source_names(conn: sqlite3.Connection) -> list[str]:
    return [row["source_name"] for row in conn.execute("SELECT DISTINCT source_name FROM matches ORDER BY source_name")]


def _count_sources(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(DISTINCT source_name) AS n FROM matches").fetchone()["n"]


def _looks_like_group_match(team_a: str | None, team_b: str | None) -> bool:
    text = f"{team_a or ''} {team_b or ''}".lower()
    placeholder_terms = ("winner", "loser", "runner-up", "runner up", "third", "match ")
    if any(term in text for term in placeholder_terms):
        return False
    tokens = [str(team_a or "").strip().lower(), str(team_b or "").strip().lower()]
    if any(re.match(r"^[wl]\d+$", token) for token in tokens):
        return False
    return not any(re.match(r"^[123][a-l](?:/[a-l]|/[123]?[a-l])*$", token) for token in tokens)


def _compact_timestamp(value: str) -> str:
    return value.replace("-", "").replace(":", "").replace(".", "").replace("+00:00", "Z")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
