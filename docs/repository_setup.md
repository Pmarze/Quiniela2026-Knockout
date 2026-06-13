# Preparacion del repositorio publico

## Politica de versionamiento

El repositorio es publico y esta pensado para guardar codigo, configuracion, documentacion, dashboard publicado y modelos finales publicados.

Se versiona:

- `src/`: paquete Python del proyecto.
- `scripts/`: comandos operativos.
- `configs/`: configuracion de modelos, scoring, backtest, entrenamiento y artefactos.
- `curated_inputs/`: inputs curados pequenos que no se descargan automaticamente.
- `docs/`: documentacion tecnica y conocimientos incrementales.
- `memory/`: estado resumido del proyecto para continuidad entre sesiones.
- `notebooks/`: cuadernos versionables.
- `model_registry/`: modelos finales publicados y metricas asociadas.
- `AGENTS.md`, `PROJECT_CONTEXT.md`, `README.md`, `pyproject.toml`.

No se versiona:

- `data/quiniela.db`
- `data/raw/`
- `data/state/`
- `data/backtests/`
- `data/predictions/`
- `data/models/`
- `data/models_local/`
- `data/external/`
- `outputs/`
- `.env`
- `.claude/settings.local.json`

La estructura de `data/` y `outputs/` se conserva con `.gitkeep`.

## Datos reconstruibles

Los artefactos reconstruibles estan listados en `configs/data_artifacts.json`.

La guia completa para un colaborador nuevo esta en `docs/collaborator_onboarding.md`.

Comando base para preparar una computadora nueva:

```powershell
python scripts\bootstrap_data.py --preset base
```

Comando completo para regenerar modelado y dashboards:

```powershell
python scripts\bootstrap_data.py --preset all
```

El preset `all` ejecuta:

- `scripts/download_data.py`
- `scripts/build_history.py`
- `scripts/run_daily.py --skip-download --skip-dashboard`
- `scripts/run_backtest.py`
- `scripts/run_model.py`
- `scripts/generate_dashboard.py`
- `scripts/generate_validation_dashboard.py`

## Modelos compartidos

Cada persona entrena en su computadora. Los checkpoints, folds, tuning y logs completos se quedan locales en `data/models/` o `data/models_local/`.

Solo se publica un modelo cuando se decide compartirlo:

```powershell
python scripts\publish_model.py --model-id neural_hybrid_v2 --version vYYYY-MM-DD --source-dir data\models_local\neural_hybrid_v2\latest
```

El modelo publicado queda en:

```text
model_registry/<model_id>/<version>/
```

El registro incluye:

- `model.pt`
- `metadata.json`
- `metrics.json`
- `metrics_live.json`
- `training_log.csv`
- `training_summary.json`, si existe junto al entrenamiento
- `manifest.json`
- `README.md`

## Git LFS

El repositorio usa Git LFS solo para pesos publicados:

```text
model_registry/**/*.pt
model_registry/**/*.pth
model_registry/**/*.ckpt
```

Cada colaborador debe ejecutar una vez:

```powershell
git lfs install
```

Despues de clonar:

```powershell
git lfs pull
```

## Crear el repositorio desde cero

Estado actual: el repositorio ya existe como publico en GitHub.

```text
https://github.com/Pmarze/Quiniela2026
```

La pagina publicada vive en:

```text
https://pmarze.github.io/Quiniela2026/
```

La seccion siguiente queda como referencia historica si se recrea el repo desde cero.

Si usas GitHub CLI:

```powershell
gh repo create quiniela2026 --public --source . --remote origin --push
```

Si prefieres crearlo desde la web:

```powershell
git remote add origin https://github.com/TU_USUARIO/quiniela2026.git
git branch -M main
git push -u origin main
```
