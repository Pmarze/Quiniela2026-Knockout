# 043 - Estado de clasificacion al cierre de fase de grupos

Fecha: 2026-06-27

## Contexto

La fase de grupos del Mundial 2026 termina hoy 27 de junio. Los grupos A-I estan cerrados (3 jornadas completas, 54 partidos). Los grupos J, K y L cierran hoy con 6 partidos pendientes.

## Como se extrajo

Estado leido desde `data/quiniela.db` del proyecto original `D:\Quiniela2026`, state_id `state_20260627T053455Z_57ca16c5` (66 de 104 partidos completados, 38 pendientes).

Consultas usadas:

```sql
-- Tablas de grupo
SELECT group_name, rank_sort, team_name, points, goal_difference, goals_for, played
FROM v_latest_state_group_tables
ORDER BY group_name, rank_sort

-- Partidos de eliminatoria
SELECT match_number, stage, team_a_name, team_b_name, status
FROM v_latest_state_matches
WHERE LOWER(COALESCE(stage, '')) NOT IN ('group', 'groups', 'group_stage', 'group stage')
ORDER BY match_number

-- Partidos pendientes hoy
SELECT match_number, group_name, team_a_name, team_b_name, kickoff_utc
FROM v_latest_state_matches
WHERE LOWER(COALESCE(stage,'')) IN ('group','groups','group_stage','group stage')
  AND LOWER(COALESCE(status,'')) != 'completed'
```

## Formato del torneo

48 equipos en 12 grupos de 4. Clasifican a knockout:

- 1ros de cada grupo (12 equipos)
- 2dos de cada grupo (12 equipos)
- 8 mejores 3ros de 12 (8 equipos)
- Total: 32 equipos en fase eliminatoria

Criterio de ranking de terceros: puntos > diferencia de goles > goles a favor.

## Clasificados confirmados al momento (27 equipos)

### 1ros y 2dos de grupos cerrados (A-I) — 18 equipos

| Grp | 1ro | Pts | 2do | Pts |
|-----|-----|-----|-----|-----|
| A | Mexico | 9 | South Africa | 4 |
| B | Switzerland | 7 | Canada | 4 |
| C | Brazil | 7 | Morocco | 7 |
| D | United States | 6 | Australia | 4 |
| E | Germany | 6 | Ivory Coast | 6 |
| F | Netherlands | 7 | Japan | 5 |
| G | Belgium | 5 | Egypt | 5 |
| H | Spain | 7 | Cape Verde | 3 |
| I | France | 9 | Norway | 6 |

### 3ros confirmados de grupos cerrados — 6 equipos

| # | Grp | Equipo | Pts | DG | GF |
|---|-----|--------|-----|----|----|
| 1 | F | Sweden | 4 | +0 | 7 |
| 2 | E | Ecuador | 4 | +0 | 2 |
| 3 | B | Bosnia and Herzegovina | 4 | -1 | 5 |
| 4 | D | Paraguay | 4 | -2 | 2 |
| 5 | I | Senegal | 3 | +2 | 8 |
| 6 | G | Iran | 3 | +0 | 3 |

### Casi confirmados de grupos pendientes — 3 equipos

- Argentina (1ro Grupo J, 6 pts con 2 partidos — practicamente seguro)
- Colombia (1ro Grupo K, 6 pts — practicamente seguro)
- Portugal (2do Grupo K, 4 pts — practicamente seguro)

## Pendientes (5 posiciones)

Dependen de los 6 partidos de hoy:

| Grupo | Partidos pendientes | Hora UTC |
|-------|---------------------|----------|
| L | Panama vs England, Croatia vs Ghana | 21:00 |
| K | Colombia vs Portugal, DR Congo vs Uzbekistan | 23:30 |
| J | Algeria vs Austria, Jordan vs Argentina | 02:00 (28 jun) |

Posiciones por definir:

1. 1ro Grupo L (England, Ghana o Croatia)
2. 2do Grupo L (England, Ghana o Croatia)
3. 2do Grupo J (Austria o Algeria)
4. 3ro Grupo L (probablemente Croatia si no clasifica 1ro/2do)
5. 3ro Grupo J (Algeria con 3 pts, -2 DG — en la burbuja del corte)

South Korea (3ro A, 3 pts, -1 DG, 2 GF) esta actualmente en la posicion 8 del ranking de terceros. Podria ser desplazado si un tercero de J, K o L termina con mejor registro.

## Eliminados confirmados

- Scotland (3ro C, 3 pts, -3 DG, 1 GF)
- Uruguay (3ro H, 2 pts, -1 DG)
- DR Congo (3ro K, 1 pt — muy probable eliminado salvo resultado extraordinario)

## Cuadro de R32

Partidos con equipos asignados (10 de 16):

```
M73: South Africa vs Canada
M74: Germany vs Paraguay
M75: Netherlands vs Morocco
M76: Brazil vs Japan
M78: Ivory Coast vs Norway
M81: United States vs Bosnia and Herzegovina
M84: Spain vs [2do Grupo J]
M86: [Argentina*] vs Cape Verde
M88: Australia vs Egypt
```

Partidos con placeholders de 3ros (6 de 16):

```
M77: France vs [3ro de C/D/F/G/H]
M79: Mexico vs [3ro de C/E/F/H/I]
M80: Belgium vs [3ro de E/H/I/J/K]
M82: [1ro Grupo G?] vs [3ro de A/E/H/I/J]
M83: [2do Grupo K] vs [2do Grupo L]
M85: Switzerland vs [3ro de E/F/G/I/J]
M87: [1ro Grupo K] vs [3ro de D/E/I/J/L]
```

Nota: M82 muestra "Winner Group G" como placeholder a pesar de que el Grupo G esta cerrado con Belgium 1ro. Posible inconsistencia en los datos fuente de FIFA que requiere verificacion al actualizar.

## Rondas posteriores

Todas con placeholder ("Winner Match X vs Winner Match Y"):

- R16: M89-M96 (8 partidos)
- QF: M97-M100 (4 partidos)
- SF: M101-M102 (2 partidos)
- 3er puesto: M103
- Final: M104

## Que actualizar al finalizar la jornada de hoy

1. Correr daily update en `D:\Quiniela2026`:

```powershell
conda activate quiniela2026
python scripts\daily_update.py --skip-git
```

2. Verificar que los 72 partidos de grupo esten completados.

3. Extraer la tabla definitiva de 3ros para confirmar los 8 clasificados.

4. Verificar que los 16 partidos de R32 tengan equipos reales asignados (no placeholders).

5. Si M82 sigue con "Winner Group G" en vez de "Belgium", investigar y corregir el dato fuente.

6. Generar el listado oficial de 32 clasificados con emparejamientos R32 completos.

7. Actualizar esta nota (043) con el estado final.

---

## Estado

Activo — pendiente de actualizacion al cierre de hoy.
