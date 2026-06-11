# Bot 001 — Informe General TMS + PostgreSQL

## Qué hace

Este bot entra automáticamente al sistema TMS (Appsis Core), descarga el Informe General y lo guarda en una base de datos PostgreSQL en Render. Corre solo todos los días a las 3:00 AM (hora Colombia) desde GitHub Actions (no depende de tu PC).

## Dónde corre

| Entorno | Cómo | Estado |
|---|---|---|
| **GitHub Actions** (nube) | Todos los días a las 3:00 AM automáticamente | ✅ Principal |
| **Tu PC (Windows)** | Tarea programada o manual desde VS Code | ✅ Alternativa / respaldo |

GitHub Actions usa un proxy con IP fija de Digital Ocean para que el TMS permita el acceso desde la nube.

## Flujo automático diario

### PASO 1 — Descarga del día (rápido, ~30 segundos)

1. Abre Chrome (headless) y navega al TMS vía proxy Digital Ocean
2. Inicia sesión con las credenciales
3. Navega: **Gestión de Informes → Avanzada → Informe General**
4. Selecciona la fecha del día anterior (usa **zona horaria de Colombia UTC-5**)
5. Descarga el CSV (DESCARGAR INFORME FORMATO)
6. Lee el CSV y filtra solo los campos que nos interesan
7. **UPSERT en PostgreSQL**: inserta guías nuevas y actualiza las que ya existen
8. Cierra el navegador

### PASO 2 — Actualización de pendientes (si aplica)

1. Consulta la DB: ¿hay guías pendientes de días anteriores?
2. Si hay, descarga el rango de fechas necesario (hasta 20 días atrás)
3. Actualiza el estado, novedad, fecha de entrega e imagen de esas guías
4. Las guías con estado ENTREGADO o CON NOVEDAD no se vuelven a tocar

## Infraestructura

```
GitHub Actions (EE.UU.)
    ↓ proxy local (localhost:8888, sin auth)
    proxy_forwarder.py (agrega autenticación)
    ↓
Droplet DigitalOcean (vulcano-proxy: 64.227.95.70:3128)
    ↓ Squid proxy con autenticación Basic
TMS Appsis Core (integra.appsiscore.com)
    ↓ descarga CSV
PostgreSQL en Render (Ohio, EE.UU.)
```

El proxy local (`proxy_forwarder.py`) se ejecuta dentro del runner de GitHub Actions y reenvía las conexiones HTTPS (CONNECT) al Squid proxy de DigitalOcean con autenticación. Chrome se conecta al proxy local sin necesidad de autenticarse.

## Campos que se guardan en PostgreSQL

| Campo | Tipo | Descripción |
|---|---|---|
| guia | TEXT (único) | Número de guía |
| nombre_cliente | TEXT | Nombre del cliente |
| ciudad_destino | TEXT | Ciudad de destino |
| destinatario | TEXT | Nombre del destinatario |
| servicio | TEXT | Tipo de servicio |
| piezas | TEXT | Cantidad de piezas |
| kilos | TEXT | Peso en kilos |
| estado | TEXT | Estado actual de la guía |
| novedad | TEXT | Última novedad registrada |
| fecha_emision | DATE | Fecha de emisión |
| fecha_preferente | DATE | Fecha preferente de entrega |
| fecha_entrega | DATE | Fecha real de entrega |
| fecha_digitalizacion | DATE | Fecha de digitalización |
| planilla | TEXT | Número de planilla |
| enlace_imagen | TEXT | URL de la imagen de entrega |
| descarga | TEXT | Tipo de descarga |

### Campos que se actualizan automáticamente

Cuando una guía ya existe en la base y no está ENTREGADA ni CON NOVEDAD, se actualizan estos 4 campos:

- **estado**
- **novedad**
- **fecha_entrega**
- **enlace_imagen**

## Archivos de esta carpeta

| Archivo | Para qué sirve |
|---|---|
| `descargar_reporte_tms.py` | Script principal con toda la lógica |
| `config_tms.json` | Configuración: credenciales TMS, carpeta destino, conexión PostgreSQL |
| `ejecutar_silencioso.vbs` | Lanzador silencioso para la tarea programada de Windows |
| `README.md` | Este archivo |

## Cómo usarlo

### Modo automático (GitHub Actions — nube)

Corre solo a las 3:00 AM hora Colombia en los servidores de GitHub. No necesitas hacer nada.

- Ver ejecuciones: [github.com/integralogistica/agentes/actions](https://github.com/integralogistica/agentes/actions)
- Ejecutar manualmente: Actions → Bot 001 → Run workflow
- Descargar artefactos (CSV, screenshots): clic en la ejecución → sección "Artifacts" abajo

### Modo manual (desde tu PC)

Desde VS Code o una terminal:

```bash
# Ayer (mismo que el automático)
python descargar_reporte_tms.py

# Un día específico
python descargar_reporte_tms.py 2026-06-05

# Un rango de días
python descargar_reporte_tms.py 2026-06-01 2026-06-05
```

El formato de fecha es siempre **AÑO-MES-DÍA**: `2026-06-05`

### Modo manual vs automático

| Característica | Manual (con fechas) | Automático (sin fechas) |
|---|---|---|
| **Descarga fechas específicas** | ✅ Sí | ❌ No (solo ayer) |
| **Actualiza guías pendientes** | ❌ No | ✅ Sí (hasta 20 días atrás) |
| **Uso típico** | Cargar histórico / correcciones | Ejecución diaria programada |

**Importante**: Si necesitas actualizar guías pendientes de días anteriores, ejecuta en modo automático (sin parámetros) después de cargar el histórico.

### Ejecutar ahora mismo

Desde VS Code (`Ctrl + `` para abrir terminal):

```bash
cd "C:\Users\ASUS\OneDrive - Integra Logistica\Desarrollos\AGENTE IA\Bot 001"
python descargar_reporte_tms.py
```

## Configuración

### En tu PC (archivo config_tms.json)

Editar `config_tms.json`:

```json
{
    "tms": {
        "url": "https://integra.appsiscore.com/app/index.php",
        "usuario": "ezarate01",
        "clave": "*******"
    },
    "descarga": {
        "carpeta_destino": "C:\\Users\\ASUS\\Downloads"
    },
    "postgresql": {
        "host": "dpg-xxxxx.render.com",
        "port": 5432,
        "database": "integra_db_vzhg",
        "usuario": "integra_db_vzhg_user",
        "clave": "*******"
    }
}
```

### En GitHub Actions (secrets)

Las credenciales se almacenan como **secrets** en GitHub (no visibles en el código). El workflow limpia automáticamente espacios en blanco de los secrets antes de usarlos.

| Secret | Descripción | Valor actual |
|---|---|---|
| `TMS_URL` | URL del TMS | `https://integra.appsiscore.com/app/index.php` |
| `TMS_USUARIO` | Usuario del TMS | |
| `TMS_CLAVE` | Contraseña del TMS | |
| `PG_HOST` | Host de PostgreSQL en Render | |
| `PG_PORT` | Puerto de PostgreSQL | `5432` |
| `PG_DATABASE` | Nombre de la base de datos | |
| `PG_USUARIO` | Usuario de PostgreSQL | |
| `PG_CLAVE` | Contraseña de PostgreSQL | |
| `PROXY_HOST` | IP del proxy Digital Ocean | `64.227.95.70` |
| `PROXY_PORT` | Puerto del proxy | `3128` |
| `PROXY_USER` | Usuario del proxy Squid | `integra` |
| `PROXY_PASS` | Contraseña del proxy | |

Para editar: [github.com/integralogistica/agentes/settings/secrets/actions](https://github.com/integralogistica/agentes/settings/secrets/actions)

⚠️ **Importante**: Al guardar los secrets, NO dejar espacios ni saltos de línea al final. El workflow los limpia automáticamente, pero es mejor evitarlos.

## Proxy Digital Ocean

- **Droplet**: `vulcano-proxy`
- **IP Pública**: `64.227.95.70`
- **Software**: Squid en puerto `3128`
- **Autenticación**: Basic Auth (usuario `integra`)
- **Configuración**: `/etc/squid/squid.conf`
- **Credenciales proxy**: `/etc/squid/passwords`
- **Logs**: `/var/log/squid/access.log`

### Comandos útiles en el droplet

```bash
# Ver estado del proxy
systemctl status squid

# Ver conexiones activas
tail -20 /var/log/squid/access.log

# Reiniciar proxy
systemctl restart squid

# Cambiar contraseña del usuario integra
htpasswd /etc/squid/passwords integra

# Ver puertos abiertos
ss -tlnp
```

### Probar proxy desde tu PC

```powershell
curl.exe -x "http://integra:TU_CLAVE@64.227.95.70:3128" -s -o NUL -w "Status: %{http_code}" --max-time 30 "https://integra.appsiscore.com/app/index.php"
```

## Base de datos PostgreSQL

- **Host**: Render (Ohio, EE.UU.)
- **Tabla**: `informe_guias_tms`
- **Identificador único**: campo `guia` (no se duplican)
- **Las guías se insertan nuevas o se actualizan si ya existen (UPSERT)**
- **Optimizado con batch processing**: lotes de 1,000 filas para máxima velocidad (~60 seg para 12k filas)

## Comportamiento con guías pendientes

- Una guía se considera **finalizada** cuando su estado es ENTREGADO o CON NOVEDAD
- Las guías pendientes se re-consultan automáticamente hasta 20 días atrás
- Si una guía lleva más de 20 días pendiente, usar el modo manual con el rango de fechas
- Las fechas vacías, `0000-00-00`, `NaN` o `NaT` se guardan como NULL en la base

## Requisitos (para ejecutar en PC local)

- Python 3.13 o superior
- Selenium (`pip install selenium`)
- Psycopg2 (`pip install psycopg2-binary`)
- Pandas (`pip install pandas`)
- Google Chrome instalado
- Conexión a internet

## Requisitos (para GitHub Actions)

- Repositorio GitHub: `integralogistica/agentes`
- Secrets configurados en el repositorio
- Proxy Digital Ocean con IP autorizada por el TMS
- Workflow con diagnóstico automático: valida proxy y detiene el proceso si no funciona

## Rendimiento y optimización

El script está optimizado con **batch processing** para máxima velocidad:

| Filas | Tiempo anterior (uno por uno) | Tiempo actual (batch de 1000) | Mejora |
|-------|------------------------------|-------------------------------|--------|
| 12,000 | ~62 min | ~30-60 seg | **~60x más rápido** 🚀 |
| 100,000 | ~8-10 horas | ~8-10 min | **~60x más rápido** 🚀 |

### Cómo funciona

- Usa `execute_batch()` de psycopg2
- Envía 1,000 filas por consulta en lugar de 1 fila por consulta
- Reduce roundtrips a la base de datos de 12,000 a 12
- Mantiene la misma lógica de UPSERT y actualización de campos

## Historial de problemas conocidos y soluciones

| Problema | Causa | Solución |
|---|---|---|
| Host de PostgreSQL vacío | Secret `PG_HOST` no configurado | Configurar todos los secrets en GitHub |
| Proxy no conecta | IP del droplet cambió | Actualizar `PROXY_HOST` con IP actual (`64.227.95.70`) |
| `NaN` en fecha_entrega | CSV tiene NaN como valor | Se filtra automáticamente: NaN → NULL |
| Tabla desaparece tras error | Rollback deshacía el CREATE TABLE | Commit separado después de crear tabla + savepoints por fila |
| Zona horaria incorrecta | `datetime.now()` usa UTC en GitHub | Se usa `ahora_colombia()` (UTC-5) para calcular "ayer" |
| Secrets con espacios | Espacios/saltos de línea al final | Workflow limpia automáticamente con `tr -d '[:space:]'` |
| Timeout del proxy | Squid con timeout corto | Timeouts aumentados en Squid: connect 60s, read/write/request 300s |
| Chrome extension no funciona | Extensiones MV2/MV3 no cargan en headless | Proxy local `proxy_forwarder.py` con `--proxy-server` directo |
| Secrets con espacios rompen URLs | Espacios causan "URL malformed" en curl | Paso "Preparar secrets limpios" hace trim automático |
| **Subida a PostgreSQL muy lenta** | **Loop uno por uno (12k consultas)** | **Optimizado con `execute_batch()` (lotes de 1000) - 60x más rápido** 🚀 |
