# Simulacion AutoStore — Forus S.A. (Grupo 12)

Simulador funcional del sistema de almacenamiento automatizado AutoStore.
Repositorio de trabajo: `https://github.com/ManuelsAguilera/Simulacion_AutoStore.git`
(fork del proyecto de `samirabsaa/Simulacion_AutoStore`).

---

## Requisitos previos

Para ejecutar y guardar varias configuraciones desde la interfaz, usa
**Comparar configuraciones de ola**: [guía de comparación por lotes](docs/comparar_olas.md).

La ampliación v2 incorpora listas de 500–1000, cinco clases de urgencia obtenidas
de una puntuación normal y preparación física secuencial mediante excavación y
reubicación. Uso, límites y reproducción: [guía v2](docs/preparacion_fisica_v2.md).
Verificación: 102 pruebas específicas y compilación de producción; piloto de
72 corridas completas, con trazas auditadas. Los modelos anteriores siguen disponibles.

La primera etapa de experimentos permite generar y recargar una instancia
sintética con 12 solicitudes, tres SKU y tres pedidos urgentes. Uso y límites:
[docs/experimentos_etapa1.md](docs/experimentos_etapa1.md).
La segunda etapa agrega olas sucesivas, preparación configurable y la regla
«urgentes primero»: [docs/experimentos_etapa2.md](docs/experimentos_etapa2.md).
La tercera etapa conserva resultados por corrida y verifica medidas de excavación:
[docs/experimentos_etapa3.md](docs/experimentos_etapa3.md).
Configuración, monitor e historial de experimentos desde el navegador:
[docs/experimentos_etapa4.md](docs/experimentos_etapa4.md).
Piloto por lotes, regla variable y análisis reproducible:
[docs/experimentos_etapa5.md](docs/experimentos_etapa5.md).

### Inicio con Docker (Windows)

Instala y abre Docker Desktop con el motor Linux. Después haz doble clic en
`iniciar-simulador.cmd`. El archivo construye las imágenes, inicia la API y la
interfaz en segundo plano, y abre `http://localhost:8100`. La primera vez puede
tardar varios minutos mientras descarga e instala las dependencias. No hace falta
activar el entorno virtual ni abrir dos terminales.

Para detener ambos servicios, haz doble clic en `detener-simulador.cmd`. También
puedes usar una sola terminal desde la raíz del repositorio:

```powershell
docker compose up -d --build
docker compose ps
docker compose logs -f
docker compose down
```

Los cambios en el código Python y Angular se reflejan durante el desarrollo.
Los resultados quedan en `output/` del equipo. Si cambias `requirements.txt` o
`m1/package-lock.json`, ejecuta de nuevo `docker compose up -d --build`.
La interfaz usa `localhost:8100` y la API `localhost:8000`; esta configuración
está pensada para acceder desde el mismo equipo.

### Inicio sin Docker

| Herramienta | Version minima | Verificar con |
|---|---|---|
| Python | 3.9+ | `python --version` o `python3 --version` |
| pip | (incluido con Python) | `pip --version` |
| Node.js | 18+ | `node --version` |
| npm | 9+ | `npm --version` |
| Git | cualquiera | `git --version` |

> Node.js y npm solo son necesarios si vas a levantar el frontend (M1).
> Para usar solo el motor o la API basta con Python.

---

## 1. Clonar el repositorio

```bash
git clone https://github.com/ManuelsAguilera/Simulacion_AutoStore.git
cd Simulacion_AutoStore
```

---

## 2. Backend (Python — motor + API)

### 2.1 Crear entorno virtual

**Linux / macOS:**

```bash
python3 -m venv venv
source venv/bin/activate
```

**Windows (CMD):**

```cmd
python -m venv venv
venv\Scripts\activate.bat
```

**Windows (PowerShell):**

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
```

> Si PowerShell bloquea la activacion, ejecuta primero:
> `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

### 2.2 Instalar dependencias

```bash
pip install -r requirements.txt
```

### 2.3 Levantar el servidor API

```bash
uvicorn api.server:app --reload --port 8000
```

El servidor queda disponible en `http://localhost:8000`.
Para detenerlo presiona `Ctrl + C`.

Para desactivar el entorno virtual despues de terminar:

```bash
deactivate
```

---

## 3. Frontend (M1 — Angular)

### 3.1 Instalar dependencias (primera vez)

```bash
cd m1
npm install
```

### 3.2 Levantar el servidor de desarrollo

```bash
cd m1
npm start
```

Se abre automaticamente en `http://localhost:8100` y se conecta al backend en `localhost:8000`.
Para detenerlo presiona `Ctrl + C`.

> El frontend puede arrancar sin el backend — las llamadas HTTP/WS fallan en silencio
> hasta que el backend este disponible.

---

## 4. Orden recomendado

| Paso | Accion | Terminal |
|------|--------|----------|
| 1 | Activar venv e iniciar backend | Terminal 1 |
| 2 | Iniciar frontend (`cd m1 && npm start`) | Terminal 2 |
| 3 | Abrir `http://localhost:8100` en el navegador | — |
| 4 | Detener frontend con `Ctrl + C` | Terminal 2 |
| 5 | Detener backend con `Ctrl + C`, luego `deactivate` | Terminal 1 |

---

## 5. Ejecutar simulacion standalone (sin frontend)

No requiere Node.js, GPU ni Omniverse. Solo Python con las dependencias instaladas.

```bash
# FIFO (predeterminado)
python -m motor.run --policy fifo --ticks 100

# Prioridad por posicion
python -m motor.run --policy prioridad_posicion --ticks 200 --seed 42

# Demo comparativa P09 (FIFO vs Prioridad + reporte)
python -m motor.run --compare --ticks 100

# Modo silencioso
python -m motor.run --policy fifo --ticks 50 --quiet
```

---

## 6. Ejecutar tests

```bash
# Todos (motor + bus)
python -m pytest tests/ bus_persistencia/tests/ -v

# Solo motor M2
python -m pytest tests/ -v

# Solo bus + persistencia
python -m pytest bus_persistencia/tests/ -v

# Solo demo P09
python -m pytest tests/test_p09_demo.py -v
```

### Demo de integracion (mock M1/M2/M3)

```bash
python -m bus_persistencia.integration
```

---

## Estructura del proyecto

```
Simulacion_AutoStore/
├── api/                  # Bridge FastAPI — endpoints REST + WebSocket
├── bus_persistencia/     # Bus de Estado + Persistencia
├── motor/                # Motor de Simulacion (M2)
│   ├── simulador.py      # Orquestador central
│   ├── grilla.py         # Grilla 3D de almacenamiento
│   ├── despachador.py    # Despachador de robots
│   ├── politicas.py      # Politicas FIFO y Prioridad
│   ├── kpis.py           # Calculo de los 7 KPIs
│   ├── modos.py          # Turno diurno y nocturno
│   └── run.py            # Runner standalone (CLI)
├── m1/                   # Frontend Angular
├── tests/                # Tests del motor M2
├── data/                 # config.json, ola.csv, reposicion.csv
├── docs/                 # Documentacion tecnica
└── output/               # Archivos generados en runtime
```

---

## Documentacion adicional

- [docs/guia_uso_m2.md](docs/guia_uso_m2.md) — guia completa de uso y tests
- [docs/bus_api.md](docs/bus_api.md) — contrato del bus para M1, M2, M3
- [docs/integracion_grupo12.md](docs/integracion_grupo12.md) — diagrama de integracion

---

## Problemas comunes

| Problema | Solucion |
|----------|----------|
| `python` no encontrado en Linux | Usar `python3` en su lugar |
| PowerShell bloquea `Activate.ps1` | `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned` |
| `npm start` falla en m1 | Verificar Node.js 18+ y ejecutar `npm install` primero |
| Puerto 8000 u 8100 ocupado | Cerrar el proceso que usa el puerto o elegir otro con `--port` |
| `ModuleNotFoundError` al correr el motor | Asegurarse de estar en la raiz del repo con el venv activo |
