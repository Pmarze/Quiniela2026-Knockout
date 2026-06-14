# Workflow Diario y Actualizacion Durante el Mundial

## Objetivo

El proyecto no debe producir un unico pronostico estatico. Debe poder ejecutarse todos los dias antes de los partidos y ajustar las predicciones usando la informacion ya conocida del torneo.

Cada corrida diaria debe tener un corte temporal explicito:

```text
as_of_utc = momento exacto hasta el cual se permite usar informacion
```

Esto evita fuga de informacion futura y permite reproducir cualquier pronostico.

## Principio Operativo

La prediccion de un partido solo puede usar:

- Partidos jugados antes de `as_of_utc`.
- Resultados oficiales o confirmados antes de `as_of_utc`.
- Mercados, odds, rankings, noticias y clima disponibles antes de `as_of_utc`.
- Estado del torneo reconstruido con esos datos.

No puede usar:

- Resultados de partidos jugados despues del corte.
- Standings que incluyan partidos posteriores al corte.
- Precios de mercado capturados despues del kickoff del partido a pronosticar.

## Pipeline Diario

```text
1. Crear run_id y as_of_utc
2. Descargar snapshots de fuentes externas
3. Canonicalizar equipos, partidos y horarios
4. Reconciliar resultados entre fuentes
5. Ingerir resultados completados
6. Reconstruir estado del torneo
7. Actualizar historicos de entrenamiento si aplica
8. Actualizar ratings y features
9. Descargar/normalizar mercados actuales
10. Ejecutar modelos activos
11. Calibrar y combinar modelos
12. Elegir marcadores por puntos esperados
13. Exportar quiniela diaria
14. Regenerar dashboard local
15. Guardar metadata de reproducibilidad
```

## Estados del Torneo

El sistema debe mantener un estado derivado, nunca editado a mano como fuente primaria.

Artefactos sugeridos:

```text
data/state/{state_id}/matches.csv
data/state/{state_id}/group_tables.csv
data/state/{state_id}/team_form.csv
data/state/{state_id}/metadata.json
```

La fuente primaria son los snapshots crudos. El estado se recalcula desde cero en cada corrida para que sea auditable.

El estado operativo actual tambien queda disponible en SQLite:

```text
v_latest_tournament_state
v_latest_state_matches
v_latest_state_group_tables
v_latest_state_team_form
```

Comando:

```powershell
python scripts\run_daily.py
```

## Historicos de Entrenamiento

La capa historica se actualiza con:

```powershell
python scripts\build_history.py
```

No necesita correr necesariamente varias veces al dia. Para el arranque de modelos basta correrla antes de entrenar/backtestear; durante el Mundial puede correrse diariamente antes de modelos para capturar actualizaciones recientes publicadas por `martj42`.

Vistas disponibles:

```text
v_latest_history_run
v_model_training_matches
v_team_rating_inputs
```

## Modelos Activos

Los modelos activos se ejecutan con:

```powershell
python scripts\run_model.py
```

Este comando:

- Lee `configs/models.yaml`.
- Usa `v_latest_history_run` y `v_latest_tournament_state`.
- Escribe artefactos en `data/predictions/{prediction_run_id}/`.
- Registra predicciones en SQLite.
- Actualiza `data/ui/prediction_overrides.json` para el dashboard.

## Snapshots

Cada descarga externa se guarda con fecha y fuente:

```text
data/raw/snapshots/{source_name}/{as_of_utc}/payload.json
data/raw/snapshots/{source_name}/{as_of_utc}/metadata.json
```

Metadata minima:

```json
{
  "source_name": "worldcup26_ir",
  "source_url": "https://worldcup26.ir",
  "downloaded_at_utc": "2026-06-14T12:00:00Z",
  "as_of_utc": "2026-06-14T12:00:00Z",
  "http_status": 200,
  "content_hash": "sha256:...",
  "notes": "Snapshot before Matchday 4"
}
```

## Reconciliacion de Resultados

Para resultados del Mundial, usar una estrategia con prioridad:

1. Fuente oficial o fuente primaria configurada.
2. API secundaria.
3. Fuente open-data / mirror.
4. Correccion manual versionada, si las fuentes discrepan.

Si dos fuentes discrepan:

- Marcar el partido como `needs_review`.
- No actualizar ratings ni standings para ese partido hasta resolverlo.
- Guardar ambas versiones en el reporte de calidad.

## Actualizacion de Ratings

Despues de cada partido completado:

- Actualizar Elo/rating propio.
- Actualizar forma reciente.
- Actualizar goles a favor/en contra recientes.
- Actualizar descanso acumulado y dias hasta el siguiente partido.
- Actualizar tabla de grupo y escenarios de clasificacion.

Los modelos deben recibir el estado actualizado, no recalcularlo cada uno por separado.

## Predicciones por Corte

Cada prediccion debe guardar:

```text
run_id
as_of_utc
model_id
match_id
kickoff_utc
input_snapshot_id
tournament_state_id
prediction_created_at_utc
```

Asi podremos comparar:

- Lo que el modelo creia antes del torneo.
- Lo que creia despues de cada jornada.
- Lo que creia justo antes de cada partido.

## Modos de Ejecucion

### Pre-torneo

No hay resultados del Mundial 2026 completados.

El estado usa:

- Historicos.
- Rankings.
- Fixtures.
- Mercados pre-torneo.
- Features de sedes, clima anticipado y plantillas si existen.

### Durante fase de grupos

El estado incorpora:

- Resultados ya jugados.
- Tablas de grupo.
- Necesidad competitiva de cada equipo.
- Rotacion probable si un equipo ya clasifico o quedo eliminado.

### Durante eliminatorias

El estado incorpora:

- Camino real del bracket.
- Fatiga y descanso.
- Tiempo extra/penales previos.
- Suspensiones por tarjetas si la fuente lo permite.

## Salidas Diarias

```text
outputs/{run_id}/quiniela_recommendations.csv
outputs/{run_id}/quiniela_recommendations.md
outputs/{run_id}/model_probabilities.parquet
outputs/{run_id}/run_report.md
outputs/dashboard/index.html
```

El archivo de quiniela debe incluir:

```text
match_id
kickoff_local
team_a
team_b
recommended_score
expected_points
p_exact
p_margin_or_draw
p_winner
confidence_tier
models_used
notes
```

## Criterio de Exito

Debe ser posible ejecutar el pipeline el 14 de junio y luego el 15 de junio con resultados nuevos, sin modificar notebooks ni codigo de modelos. Solo cambian los snapshots y el `as_of_utc`.

## Publicacion Automatica

Para el repositorio publico, `docs/index.html` se genera con quinielas de amigos si existe
`data/ui/friends_quinielas.json`. El enlace/ID de Google Sheets se queda fuera del repo.

Comando local publico:

```powershell
conda activate quiniela2026
python scripts\daily_update.py --skip-git
python scripts\check_public_dashboard.py docs\index.html
```

Actualizar amigos localmente:

```powershell
python scripts\build_friends_quinielas.py
python scripts\generate_dashboard.py
```

GitHub Actions:

- `.github/workflows/update-dashboard.yml` reconstruye datos/modelos y commitea `docs/index.html`, `data/ui/prediction_overrides.json` y `data/ui/friends_quinielas.json`.
- `.github/workflows/deploy-pages.yml` despliega `docs/` a GitHub Pages cuando cambia `docs/**` o el propio workflow en `main`.
- Los horarios de `schedule` corren en UTC y pueden retrasarse; para actualizar despues de cada partido se usa una frecuencia de 30 minutos durante junio/julio.

## Estado Actual De Publicacion

GitHub Pages esta habilitado para el repo con `build_type=workflow`.

URL publica:

```text
https://pmarze.github.io/Quiniela2026/
```

Ultima verificacion de publicacion:

- commit publicado en `main` y `development`: `26d76ea`
- workflow: `Deploy Dashboard to GitHub Pages`
- resultado del deploy manual: `success`
- verificacion HTTP publica: `status=200`
- `docs/index.html`: 104 partidos, 5 amigos

Si el deploy falla con:

```text
Get Pages site failed
```

o:

```text
Create Pages site failed. Resource not accessible by integration
```

revisar que Pages este habilitado como `workflow`. Con GitHub CLI autenticado y permisos de admin:

```powershell
gh api -X POST repos/Pmarze/Quiniela2026/pages -f build_type=workflow
```

Si ya existe el sitio, GitHub puede responder conflicto; en ese caso solo revisar/actualizar desde Settings -> Pages o con API.

## Runbook Para Claude Code

Claude Code debe leer primero `CLAUDE.md`. Para una actualizacion diaria normal, trabajar en `development`:

```powershell
git checkout development
git pull --rebase
git lfs pull
conda activate quiniela2026
python scripts\daily_update.py --skip-git
python scripts\check_public_dashboard.py docs\index.html
python scripts\security_scan_publish.py
```

Si se necesita refrescar amigos desde Google Sheets y existen secrets/local config:

```powershell
python scripts\build_friends_quinielas.py
python scripts\generate_dashboard.py
python scripts\check_public_dashboard.py docs\index.html
```

Archivos publicables esperados despues del daily:

```text
docs/index.html
data/ui/prediction_overrides.json
data/ui/friends_quinielas.json
```

Hacer commit y push a `development`:

```powershell
git add docs/index.html data/ui/prediction_overrides.json data/ui/friends_quinielas.json
git commit -m "Daily public dashboard update"
git push origin development
```

Solo si el usuario pide que la pagina quede en vivo, promover a `main`:

```powershell
git checkout main
git pull --ff-only origin main
git merge --ff-only development
git push origin main
git checkout development
git merge --ff-only main
git push origin development
```

Despues del push a `main`, confirmar deploy:

```powershell
gh run list --workflow "Deploy Dashboard to GitHub Pages" --branch main --limit 3
gh run watch <RUN_ID> --exit-status
$response = Invoke-WebRequest -Uri 'https://pmarze.github.io/Quiniela2026/' -UseBasicParsing
$response.StatusCode
```

Esperado:

```text
success
200
```

## Secrets Para Automatizacion

Para que GitHub Actions pueda actualizar amigos desde Google Sheets sin exponer el link en el repo, configurar estos GitHub Secrets:

```text
QUINIELA_FRIENDS_SHEET_ID
QUINIELA_FRIENDS_SHEET_GID
QUINIELA_FRIENDS_SHEET_NAME
```

Solo `QUINIELA_FRIENDS_SHEET_ID` es obligatorio. Si no existe, `.github/workflows/update-dashboard.yml` usa el archivo versionado `data/ui/friends_quinielas.json`.

No commitear:

```text
configs/friends_sheet.local.json
.env
.env.*
.claude/settings.local.json
curated_inputs/quiniela_template_mundial2026.xlsx
```
