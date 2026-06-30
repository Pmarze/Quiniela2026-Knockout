# 041 - GitHub Pages y automatizacion diaria

Fecha de actualizacion: 2026-06-13.

## Resumen

El repositorio ya es publico y la pagina de GitHub Pages quedo habilitada y verificada.

URL publica:

```text
https://pmarze.github.io/Quiniela2026-Knockout/
```

Estado verificado:

- commit publicado en `main` y `development`: `26d76ea`
- deploy workflow: `Deploy Dashboard to GitHub Pages`
- deploy manual: `success`
- verificacion HTTP: `200`
- dashboard publicado: `docs/index.html`
- contenido validado: 104 partidos, 5 amigos

## Que se corrigio para que Pages funcionara

El primer push a `main` disparo el workflow, pero fallo en `Setup Pages` porque GitHub Pages todavia no estaba habilitado para el repo.

Primer error observado:

```text
Get Pages site failed. Please verify that the repository has Pages enabled and configured to build using GitHub Actions
```

Se intento permitir `enablement: true` en `actions/configure-pages@v5`, pero el `GITHUB_TOKEN` del workflow no tenia permiso para crear el sitio.

Segundo error observado:

```text
Create Pages site failed. Resource not accessible by integration
```

Se habilito Pages con GitHub CLI autenticado:

```powershell
gh api -X POST repos/Pmarze/Quiniela2026-Knockout/pages -f build_type=workflow
```

Despues se lanzo el deploy manual:

```powershell
gh workflow run deploy-pages.yml --ref main
gh run watch <RUN_ID> --exit-status
```

Resultado: `success`.

## Workflows actuales

Deploy publico:

```text
.github/workflows/deploy-pages.yml
```

Se dispara con push a `main` cuando cambia:

- `docs/**`
- `.github/workflows/deploy-pages.yml`

Publica el directorio:

```text
docs/
```

Actualizacion automatica:

```text
.github/workflows/update-dashboard.yml
```

Corre:

- manualmente con `workflow_dispatch`
- por horario cada 30 minutos durante junio y julio

Hace:

1. checkout con Git LFS
2. setup Conda desde `environment.yml`
3. `python scripts/bootstrap_data.py --preset base`
4. `python scripts/run_model.py`
5. opcionalmente `python scripts/build_friends_quinielas.py` si existen secrets `QUINIELA_FRIENDS_SHEET_*`
6. `python scripts/generate_dashboard.py`
7. `python scripts/check_public_dashboard.py docs/index.html`
8. commit de artefactos publicables si cambiaron

El push automatico a `main` dispara el deploy de Pages.

## Runbook para Claude Code

Claude Code debe empezar en `development`:

```powershell
git checkout development
git pull --rebase
git lfs pull
conda activate quiniela2026
```

Daily local:

```powershell
python scripts\daily_update.py --skip-git
python scripts\check_public_dashboard.py docs\index.html
python scripts\security_scan_publish.py
```

Si cambiaron quinielas de amigos y existe configuracion local privada:

```powershell
python scripts\build_friends_quinielas.py
python scripts\generate_dashboard.py
python scripts\check_public_dashboard.py docs\index.html
```

Commit normal a `development`:

```powershell
git add docs/index.html data/ui/prediction_overrides.json data/ui/friends_quinielas.json
git commit -m "Daily public dashboard update"
git push origin development
```

Promover a `main` solo si el usuario lo pide:

```powershell
git checkout main
git pull --ff-only origin main
git merge --ff-only development
git push origin main
git checkout development
git merge --ff-only main
git push origin development
```

Validar deploy:

```powershell
gh run list --workflow "Deploy Dashboard to GitHub Pages" --branch main --limit 3
gh run watch <RUN_ID> --exit-status
$response = Invoke-WebRequest -Uri 'https://pmarze.github.io/Quiniela2026-Knockout/' -UseBasicParsing
$response.StatusCode
```

Esperado:

```text
success
200
```

## Secrets y privacidad

El link/ID de Google Sheets no se versiona.

Config local permitida:

```text
configs/friends_sheet.local.json
```

GitHub Secrets recomendados:

```text
QUINIELA_FRIENDS_SHEET_ID
QUINIELA_FRIENDS_SHEET_GID
QUINIELA_FRIENDS_SHEET_NAME
```

Si los secrets no existen, el workflow usa el JSON versionado:

```text
data/ui/friends_quinielas.json
```

Antes de publicar, correr:

```powershell
python scripts\check_public_dashboard.py docs\index.html
python scripts\security_scan_publish.py
```

No commitear:

```text
configs/friends_sheet.local.json
.env
.env.*
.claude/settings.local.json
curated_inputs/quiniela_template_mundial2026.xlsx
```
