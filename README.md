# Quiniela2026-Knockout

Proyecto para estimar pronosticos de quiniela del Mundial 2026 con modelos modulares de goles, backtesting historico, simulacion Monte Carlo, redes neuronales y dashboards publicos/locales. Este repositorio extiende el proyecto base con una capa de eliminatorias (knockout) que ajusta modelos para fases de mata-mata, incluyendo tiempo extra y penaltis.

El contexto general vive en [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md). La documentacion tecnica vive en [docs/](docs/), y las decisiones incrementales en [docs/knowledge/000_index.md](docs/knowledge/000_index.md).

Para trabajar con Codex desde otro equipo, usar primero [AGENTS.md](AGENTS.md) y [docs/collaborator_onboarding.md](docs/collaborator_onboarding.md).

## Estado Actual

Implementado:

- Ingesta y canonicalizacion de datos.
- Capa historica para entrenamiento y backtesting.
- Estado del torneo con cortes diarios.
- Modelos Poisson/Elo, empates, Monte Carlo, Opta externo y redes neuronales.
- Ensambles ponderados con optimizacion por backtest 2018/2022.
- Dashboard publico de seguimiento del torneo, incluyendo comparacion de quinielas de amigos.
- Dashboard local de validacion historica.
- Capa de knockout: ajustes de goles, inflacion de empates, resolucion de tiempo extra y penaltis.
- Cuadro eliminatorio circular interactivo con banderas (R32 → Final).
- Perfiles de puntuacion alternativos (5-3-1 clasica, 3-1-0 simple).

Modelo de quiniela activo por defecto:

```text
weighted_points_ensemble
```

## Runtime

Crear el entorno de Anaconda:

```powershell
conda env create -f environment.yml
conda activate quiniela2026
git lfs pull
```

Desde la carpeta del proyecto, con el entorno ya activado:

```powershell
python scripts\bootstrap_data.py --preset base
python scripts\build_history.py
python scripts\run_backtest.py
python scripts\optimize_ensemble_weights.py --iterations 8000
python scripts\run_model.py
python scripts\generate_dashboard.py
python scripts\generate_validation_dashboard.py
```

## Dashboards

Dashboard publico:

```text
https://pmarze.github.io/Quiniela2026-Knockout/
```

El dashboard publico se genera en:

```text
docs/index.html
```

Los HTML locales de apoyo se guardan en:

```text
outputs/dashboard/index.html
outputs/validation_dashboard/index.html
```

Por defecto, `python scripts\generate_dashboard.py` incluye `data/ui/friends_quinielas.json` si existe,
para que la pagina en linea y la local se vean igual. Para generar una version sin amigos:

```powershell
python scripts\generate_dashboard.py --exclude-friends
```

## Datos y Artefactos

El repositorio versiona codigo, configuracion, documentacion y modelos finales publicados. Los datos descargables, corridas de entrenamiento, backtests, predicciones y dashboards generados se quedan locales y se reconstruyen con scripts.

Se comparte:

- `model_registry/`: modelos finales publicados y metricas asociadas.
- `configs/data_artifacts.json`: manifiesto de artefactos reconstruibles.
- `scripts/bootstrap_data.py`: reconstruccion de datos locales.
- `scripts/publish_model.py`: publicacion controlada de modelos entrenados.

No se comparte `data/` ni `outputs/` salvo excepciones explicitas como `data/ui/prediction_overrides.json`
y `data/ui/friends_quinielas.json`. El enlace/ID de Google Sheets se guarda fuera del repo en
`configs/friends_sheet.local.json` o variables de entorno. Ver [docs/repository_setup.md](docs/repository_setup.md).

## Documentos Principales

- [Instrucciones para Codex](AGENTS.md)
- [Guia para colaborar desde cero](docs/collaborator_onboarding.md)
- [Preparacion del repositorio publico](docs/repository_setup.md)
- [Plan de implementacion](docs/implementation_plan.md)
- [Arquitectura](docs/architecture.md)
- [Workflow diario y actualizaciones](docs/daily_update_workflow.md)
- [Automatizacion diaria y GitHub Pages](docs/knowledge/041_pages_y_automatizacion_diaria.md)
- [Almacenamiento de datos](docs/data_storage.md)
- [Canonicalizacion y reconciliacion](docs/canonicalization.md)
- [Capa historica para modelos](docs/history_layer.md)
- [Runtime Python](docs/runtime.md)
- [Conocimientos incrementales](docs/knowledge/000_index.md)
- [Estado del torneo](docs/tournament_state.md)
- [Dashboard local](docs/ui_dashboard.md)
- [Contrato de modelos](docs/model_contract.md)
- [Fuentes de datos](docs/data_sources.md)
- [Metricas de evaluacion](docs/evaluation_metrics.md)
- [Workflow de notebooks](docs/notebook_workflow.md)
