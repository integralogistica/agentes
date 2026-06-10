# Bot 001 — Informe General TMS + PostgreSQL

## Qué hace

Este bot entra automáticamente al sistema TMS (Appsis Core), descarga el Informe General y lo guarda en una base de datos PostgreSQL en Render. Corre solo todos los días a las 3:00 AM.

## Flujo automático diario

### PASO 1 — Descarga del día (rápido, ~30 segundos)

1. Abre Chrome y navega al TMS
2. Inicia sesión con las credenciales del archivo `config_tms.json`
3. Navega: **Gestión de Informes → Avanzada → Informe General**
4. Selecciona la fecha del día anterior
5. Descarga el CSV (DESCARGAR INFORME FORMATO)
6. Lee el CSV y filtra solo los campos que nos interesan
7. **UPSERT en PostgreSQL**: inserta guías nuevas y actualiza las que ya existen
8. Cierra el navegador

### PASO 2 — Actualización de pendientes (si aplica)

1. Consulta la DB: ¿hay guías pendientes de días anteriores?
2. Si hay, descarga el rango de fechas necesario (hasta 20 días atrás)
3. Actualiza el estado, novedad, fecha de entrega e imagen de esas guías
4. Las guías con estado ENTREGADO o CON NOVEDAD no se vuelven a tocar

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

### Modo automático (cada día a las 3:00 AM)

Corre solo. No hay que hacer nada. La tarea programada de Windows se encarga.

Para verificar la tarea: `Windows + R` → escribir `taskschd.msc` → Enter

### Modo manual (para cargar un día específico)

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

### Ejecutar ahora mismo

Desde VS Code (`Ctrl + `` para abrir terminal):

```bash
cd "C:\Users\ASUS\OneDrive - Integra Logistica\Desarrollos\AGENTE IA\Bot 001"
python descargar_reporte_tms.py
```

## Cómo cambiar la configuración

Editar el archivo `config_tms.json`:

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

- **tms.usuario/tms.clave**: Si cambias tu contraseña del TMS
- **descarga.carpeta_destino**: Dónde se guarda el CSV descargado
- **postgresql.***: Datos de conexión a la base de datos en Render

## Base de datos PostgreSQL

- **Host**: Render (Ohio, EE.UU.)
- **Tabla**: `informe_guias_tms`
- **Identificador único**: campo `guia` (no se duplican)
- **Las guías se insertan nuevas o se actualizan si ya existen (UPSERT)**

## Comportamiento con guías pendientes

- Una guía se considera **finalizada** cuando su estado es ENTREGADO o CON NOVEDAD
- Las guías pendientes se re-consultan automáticamente hasta 20 días atrás
- Si una guía lleva más de 20 días pendiente, usar el modo manual con el rango de fechas
- Las fechas vacías o con `0000-00-00` se guardan como NULL en la base

## Requisitos

- Python 3.13 o superior
- Selenium (`pip install selenium`)
- Psycopg2 (`pip install psycopg2-binary`)
- Pandas (`pip install pandas`)
- Google Chrome instalado
- Conexión a internet
- Computadora encendida a las 3:00 AM (para la ejecución automática)
