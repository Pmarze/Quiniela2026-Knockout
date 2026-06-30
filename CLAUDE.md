# CLAUDE.md - Instrucciones para agentes IA

Este archivo es leido automaticamente por Claude Code y cualquier agente IA que trabaje en este repositorio.
Todas las reglas aqui son obligatorias.

## Rama de trabajo

Regla critica: todo trabajo de IA se hace en `development`. No trabajar directo en `main`.

```text
Rama de desarrollo : development
Rama de produccion : main
```

Al iniciar una sesion:

```powershell
git branch --show-current
git checkout development
git pull --rebase
git lfs pull
```

Todos los commits normales van a `development`.

Solo se puede empujar a `main` cuando el usuario lo pida explicitamente con frases como:

- `haz push a main`
- `merge a main`
- `promover a produccion`
- `publicar en main`
- `deja la pagina en vivo`

Promocion segura a produccion:

```powershell
git checkout main
git pull --ff-only origin main
git merge --ff-only development
git push origin main
git checkout development
git merge --ff-only main
git push origin development
```

Si `--ff-only` falla, detenerse, inspeccionar el conflicto y pedir confirmacion antes de resolver.

## Entorno Python

Usar siempre el entorno Conda. En sesiones interactivas de terminal funciona `conda activate quiniela2026`. Pero desde Claude Code (Bash tool), `conda` no esta en el PATH, por lo que hay que invocar el interprete directamente:

```text
C:\Users\pablo\.conda\envs\quiniela2026\python.exe
```

Ejemplo de uso desde Bash tool:

```bash
"/c/Users/pablo/.conda/envs/quiniela2026/python.exe" scripts/daily_update.py --skip-git
```

No usar `python ...` a secas desde el Bash tool: resuelve al Python del sistema (`C:\Users\pablo\AppData\Local\Programs\Python\Python312\python.exe`) que no tiene `torch` ni otras dependencias del proyecto.

## Dashboard publico

La pagina publica vive en:

```text
https://pmarze.github.io/Quiniela2026-Knockout/
```

El HTML publicado esta en:

```text
docs/index.html
```

No editar `docs/index.html` a mano. Editar las fuentes:

- `src/quiniela/ui/dashboard.py`
- `src/quiniela/ui/dashboard_template.html`

Luego regenerar:

```powershell
python scripts\generate_dashboard.py
python scripts\check_public_dashboard.py docs\index.html
```

Por defecto el dashboard incluye amigos si existe:

```text
data/ui/friends_quinielas.json
```

Version sin amigos solo si el usuario lo pide explicitamente:

```powershell
python scripts\generate_dashboard.py --exclude-friends
```

## Actualizacion diaria local

Cuando el usuario pida actualizar despues de partidos o al cierre del dia:

```powershell
conda activate quiniela2026
python scripts\daily_update.py --skip-git
python scripts\check_public_dashboard.py docs\index.html
python scripts\security_scan_publish.py
```

Si tambien cambiaron quinielas de amigos en Google Sheets y el entorno local tiene `configs/friends_sheet.local.json` o variables `QUINIELA_FRIENDS_SHEET_*`:

```powershell
python scripts\build_friends_quinielas.py
python scripts\generate_dashboard.py
python scripts\check_public_dashboard.py docs\index.html
```

Commit normal:

```powershell
git status
git add docs/index.html data/ui/prediction_overrides.json data/ui/friends_quinielas.json
git commit -m "Daily public dashboard update"
git push origin development
```

Si el usuario pidio publicar en vivo, promover despues a `main` con el flujo seguro de la seccion de ramas.

## GitHub Pages y Actions

GitHub Pages ya esta habilitado en el repositorio con `build_type=workflow`.

Workflow de deploy:

```text
.github/workflows/deploy-pages.yml
```

Se dispara con push a `main` cuando cambia:

- `docs/**`
- `.github/workflows/deploy-pages.yml`

Workflow de actualizacion automatica:

```text
.github/workflows/update-dashboard.yml
```

Corre manualmente con `workflow_dispatch` y por horario cada 30 minutos durante junio y julio. Reconstruye datos, corre modelos, regenera `docs/index.html`, valida y commitea cambios publicables.

Para lanzarlo manualmente:

```powershell
gh workflow run update-dashboard.yml --ref main
```

Para revisar deploy:

```powershell
gh run list --workflow "Deploy Dashboard to GitHub Pages" --branch main --limit 3
gh run watch <RUN_ID> --exit-status
```

Verificacion publica:

```powershell
$response = Invoke-WebRequest -Uri 'https://pmarze.github.io/Quiniela2026-Knockout/' -UseBasicParsing
$response.StatusCode
```

Esperado: `200`.

## Secrets y datos privados

Permitido publicar:

- `docs/index.html`
- `data/ui/prediction_overrides.json`
- `data/ui/friends_quinielas.json`
- modelos finales en `model_registry/`

No publicar:

- URL o ID de Google Sheets
- `configs/friends_sheet.local.json`
- `.env` o `.env.*`
- `.claude/settings.local.json`
- tokens, API keys, llaves privadas
- rutas personales de Windows
- archivos locales no solicitados, por ejemplo `curated_inputs/quiniela_template_mundial2026.xlsx`

Secrets utiles para GitHub Actions:

- `QUINIELA_FRIENDS_SHEET_ID`
- `QUINIELA_FRIENDS_SHEET_GID` opcional
- `QUINIELA_FRIENDS_SHEET_NAME` opcional

Si esos secrets no existen, el workflow usa el JSON versionado de amigos.

Antes de publicar:

```powershell
python scripts\check_public_dashboard.py docs\index.html
python scripts\security_scan_publish.py
```

## Objetivo del sistema

El objetivo no es solo acertar 1X2. El objetivo principal es maximizar puntos de quiniela:

- marcador exacto
- empate o mismo margen/diferencia
- ganador correcto

El modelo operativo por defecto sigue siendo:

```text
weighted_points_ensemble
```
