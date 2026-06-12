"""
Bot 001 - Descarga automatica del Informe General del TMS
Flujo:
  1. Login > Gestion de Informes > Avanzada > Informe General
  2. Descargar CSV del dia anterior (guias nuevas)
  3. INSERTAR nuevas guias en PostgreSQL / ACTUALIZAR las que ya existen
  4. Consultar guias pendientes y descargar historico para actualizarlas
"""

import json
import sys
import io
import os
import time
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Fix encoding para Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import pandas as pd
import psycopg2
from psycopg2.extras import execute_batch

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    ElementClickInterceptedException,
)

# ─── Configuracion de logging ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("Bot001")


# ─── Cargar configuracion ────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config_tms.json"


def cargar_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ═════════════════════════════════════════════════════════════════════════════
#  MAPEO DE COLUMNAS
# ═════════════════════════════════════════════════════════════════════════════
COLUMNAS = {
    "Guia":                              "guia",
    " Nombre  Cliente ":                 "nombre_cliente",
    " Ciudad  Destino ":                 "ciudad_destino",
    "Destinatario":                      "destinatario",
    "Servicio":                          "servicio",
    "Piezas":                            "piezas",
    "Kilos":                             "kilos",
    "Estado":                            "estado",
    "Novedad":                           "novedad",
    " Fecha  Emision ":                  "fecha_emision",
    " Fecha  Preferente ":               "fecha_preferente",
    " Fecha  Entrega ":                  "fecha_entrega",
    " Fecha  Digitalizacion ":           "fecha_digitalizacion",
    "Planilla":                          "planilla",
    " Enlace  Imagen  Digitalizada ":    "enlace_imagen",
    "Descarga":                          "descarga",
}

COLUMNAS_FECHA = {"fecha_emision", "fecha_preferente", "fecha_entrega", "fecha_digitalizacion"}

# Campos que se actualizan cuando una guia pendiente cambia de estado
CAMPOS_ACTUALIZAR = ["estado", "novedad", "fecha_entrega", "enlace_imagen"]

# Maximo de dias hacia atras para consultar pendientes (para no demorar demasiado)
MAX_DIAS_HISTORICO = 20

# Zona horaria Colombia (UTC-5)
TZ_COLOMBIA = timezone(timedelta(hours=-5))


def ahora_colombia():
    """Retorna la fecha/hora actual en zona horaria de Colombia."""
    return datetime.now(TZ_COLOMBIA)


# ═════════════════════════════════════════════════════════════════════════════
#  FUNCIONES DE SELENIUM (NAVEGACION)
# ═════════════════════════════════════════════════════════════════════════════
def clic_elemento_por_texto(driver, texto, tag="*", timeout=10):
    wait = WebDriverWait(driver, timeout)
    texto_escaped = texto.replace("'", "\\'")
    xpath = (
        f"//{tag}[contains(translate(text(),"
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),"
        f"'{texto_escaped.lower()}')]"
    )
    log.info(f"Buscando: '{texto}'")
    elem = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
    driver.execute_script("arguments[0].scrollIntoView(true);", elem)
    time.sleep(0.3)
    try:
        elem.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", elem)
    log.info(f"OK -> '{texto}'")
    return elem


def crear_navegador(carpeta_descarga):
    opts = ChromeOptions()

    # Modo headless si se ejecuta en la nube (sin pantalla)
    if os.environ.get("HEADLESS") == "true":
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1920,1080")

    # Proxy: local (pproxy) o extensión de Chrome
    proxy_local = os.environ.get("PROXY_LOCAL", "")
    proxy_ext = os.environ.get("PROXY_EXTENSION", "")
    if proxy_local:
        opts.add_argument(f"--proxy-server={proxy_local}")
        log.info(f"Usando proxy local: {proxy_local}")
    elif proxy_ext and os.path.isdir(proxy_ext):
        opts.add_argument(f"--load-extension={proxy_ext}")
        log.info(f"Usando proxy via extension Chrome")
    else:
        opts.add_argument("--start-maximized")

    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    prefs = {
        "download.default_directory": str(carpeta_descarga),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
    }
    opts.add_experimental_option("prefs", prefs)
    driver = webdriver.Chrome(options=opts)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    # Timeout para carga de pagina (60 segundos)
    driver.set_page_load_timeout(60)
    return driver


def hacer_login(driver, url, usuario, clave):
    log.info("Iniciando sesion en el TMS...")

    # Reintentar carga de pagina si falla
    for intento in range(3):
        try:
            driver.get(url)
            break
        except Exception as e:
            log.warning(f"Error cargando pagina (intento {intento + 1}/3): {e}")
            if intento == 2:
                raise
            time.sleep(5)

    time.sleep(5)

    # Screenshot y HTML para diagnosticar en la nube
    if os.environ.get("HEADLESS") == "true":
        ss_path = "/tmp/tms_downloads/debug_login.png"
        driver.save_screenshot(ss_path)
        log.info(f"Screenshot login: {ss_path}")
        # Guardar HTML de la pagina
        html_path = "/tmp/tms_downloads/debug_login.html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        log.info(f"HTML guardado: {html_path}")
        # Listar iframes
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        log.info(f"Iframes encontrados: {len(iframes)}")
        for i, iframe in enumerate(iframes):
            src = iframe.get_attribute("src") or ""
            log.info(f"  iframe[{i}]: src='{src}'")

    # Dump de inputs para diagnosticar
    todos_inputs = driver.find_elements(By.TAG_NAME, "input")
    log.info(f"Inputs encontrados en la pagina: {len(todos_inputs)}")
    for i, inp in enumerate(todos_inputs):
        tipo = inp.get_attribute("type") or ""
        nombre = inp.get_attribute("name") or ""
        iid = inp.get_attribute("id") or ""
        placeholder = inp.get_attribute("placeholder") or ""
        log.info(f"  input[{i}]: type='{tipo}' name='{nombre}' id='{iid}' placeholder='{placeholder}'")

    wait = WebDriverWait(driver, 15)

    # Intentar encontrar campo de usuario con multiples estrategias
    campo_usuario = None

    # Estrategia 1: Buscar por atributos comunes
    try:
        campo_usuario = wait.until(
            EC.presence_of_element_located((
                By.XPATH,
                "//input[@type='text' or @name='usuario' or @name='username' "
                "or @id='usuario' or @id='username' "
                "or contains(@placeholder,'usuario') or contains(@placeholder,'Usuario')]"
            ))
        )
    except TimeoutException:
        pass

    # Estrategia 2: Buscar primer input visible que no sea password/hidden/submit
    if not campo_usuario:
        for inp in todos_inputs:
            tipo = (inp.get_attribute("type") or "").lower()
            if tipo not in ("password", "hidden", "submit", "button", "checkbox", "radio"):
                campo_usuario = inp
                log.info(f"Campo usuario encontrado por estrategia 2: type='{tipo}'")
                break

    # Estrategia 3: Cualquier input
    if not campo_usuario and todos_inputs:
        campo_usuario = todos_inputs[0]
        log.info("Campo usuario encontrado por estrategia 3 (primer input)")

    if not campo_usuario:
        raise Exception("No se encontro el campo de usuario")

    campo_usuario.clear()
    campo_usuario.send_keys(usuario)

    try:
        campo_clave = driver.find_element(By.XPATH, "//input[@type='password']")
    except NoSuchElementException:
        raise Exception("No se encontro el campo de clave")

    campo_clave.clear()
    campo_clave.send_keys(clave)

    time.sleep(0.5)
    botones_login = [
        "//button[@type='submit']",
        "//input[@type='submit']",
        "//button[contains(text(),'Iniciar') or contains(text(),'Login') or contains(text(),'Entrar')]",
        "//a[contains(text(),'Iniciar') or contains(text(),'Login') or contains(text(),'Entrar')]",
    ]
    for xpath_btn in botones_login:
        try:
            driver.find_element(By.XPATH, xpath_btn).click()
            break
        except (NoSuchElementException, ElementClickInterceptedException):
            continue
    else:
        from selenium.webdriver.common.keys import Keys
        campo_clave.send_keys(Keys.ENTER)

    time.sleep(3)
    log.info("Sesion iniciada")


def navegar_a_informe(driver):
    log.info("Navegando al informe general...")
    clic_elemento_por_texto(driver, "GESTION DE INFORMES", tag="*")
    time.sleep(2)
    clic_elemento_por_texto(driver, "Avanzada", tag="*", timeout=10)
    time.sleep(2)
    try:
        clic_elemento_por_texto(driver, "Informe General", tag="*", timeout=10)
    except TimeoutException:
        clic_elemento_por_texto(driver, "Informe General", timeout=10)
    time.sleep(3)
    log.info("Ventana de Informe General abierta")


def setear_fechas_y_buscar(driver, fecha_inicio, fecha_fin):
    """Setea las fechas en el formulario y hace clic en Buscar."""
    handles = driver.window_handles
    if len(handles) > 1:
        driver.switch_to.window(handles[-1])
        time.sleep(2)

    log.info(f"Seteando fechas: {fecha_inicio} a {fecha_fin}")
    try:
        campo_inicio = driver.find_element(By.CSS_SELECTOR, "input[name='fecha9']")
        campo_fin = driver.find_element(By.CSS_SELECTOR, "input[name='fecha10']")
        for campo, valor in [(campo_inicio, fecha_inicio), (campo_fin, fecha_fin)]:
            driver.execute_script("""
                arguments[0].value = arguments[1];
                arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
            """, campo, valor)
    except NoSuchElementException:
        campos_fecha = driver.find_elements(By.CSS_SELECTOR, "input[type='date']")
        for i, campo in enumerate(campos_fecha):
            valor = fecha_inicio if i == 0 else fecha_fin
            driver.execute_script("""
                arguments[0].value = arguments[1];
                arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
            """, campo, valor)

    time.sleep(1)

    # Click Buscar
    try:
        driver.find_element(By.CSS_SELECTOR, "input[type='submit'][value='Buscar....']").click()
    except NoSuchElementException:
        try:
            driver.find_element(By.XPATH, "//input[@type='submit' and contains(@value,'Buscar')]").click()
        except NoSuchElementException:
            driver.find_element(By.CSS_SELECTOR, "input[type='submit']").click()

    # Esperar mas tiempo si el rango es mayor
    dias = (datetime.strptime(fecha_fin, "%Y-%m-%d") - datetime.strptime(fecha_inicio, "%Y-%m-%d")).days + 1
    espera = min(dias * 3 + 3, 120)  # 3 seg por dia, max 120 seg
    log.info(f"Esperando {espera} segundos ({dias} dias de rango)...")
    time.sleep(espera)


def descargar_csv(driver):
    """Busca y hace clic en el boton de descarga CSV."""
    log.info("Buscando boton de descarga...")
    textos_buscar = [
        "DESCARGAR INFORME FORMATO",
        "Descargar Informe",
        "DESCARGAR",
        "Exportar",
        "CSV",
        "FORMATO",
    ]
    for texto in textos_buscar:
        try:
            elem = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    f"//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), '{texto.lower()}')]"
                ))
            )
            driver.execute_script("arguments[0].scrollIntoView(true);", elem)
            time.sleep(0.5)
            try:
                elem.click()
            except ElementClickInterceptedException:
                driver.execute_script("arguments[0].click();", elem)
            log.info("Descarga iniciada")
            return
        except TimeoutException:
            continue

    # Ultimo recurso: buscar por href
    links = driver.find_elements(
        By.XPATH,
        "//a[contains(@href,'.csv') or contains(@href,'download') "
        "or contains(@href,'export') or contains(@href,'descarg')]"
    )
    if links:
        links[0].click()
        log.info("Descarga iniciada via link")
    else:
        raise Exception("No se encontro el boton de descarga")


def limpiar_previos(carpeta):
    for f in Path(carpeta).glob("Informe_General*.csv"):
        try:
            f.unlink()
        except Exception:
            pass
    for f in Path(carpeta).glob("*.crdownload"):
        try:
            f.unlink()
        except Exception:
            pass


def esperar_descarga(carpeta_descarga, timeout=120):
    log.info(f"Esperando descarga...")
    inicio = time.time()
    while time.time() - inicio < timeout:
        archivos_csv = list(Path(carpeta_descarga).glob("Informe_General*.csv"))
        archivos_pendientes = list(Path(carpeta_descarga).glob("*.crdownload"))
        if archivos_csv and not archivos_pendientes:
            archivo = max(archivos_csv, key=lambda f: f.stat().st_mtime)
            log.info(f"Descargado: {archivo.name}")
            return archivo
        time.sleep(1)
    raise TimeoutException("La descarga no se completo en el tiempo esperado")


# ═════════════════════════════════════════════════════════════════════════════
#  FUNCIONES DE POSTGRESQL
# ═════════════════════════════════════════════════════════════════════════════
def leer_csv(archivo_csv, excluir_servicios=None):
    """Lee el CSV, filtra columnas, limpia espacios y fechas. Excluye servicios indicados."""
    df = pd.read_csv(archivo_csv, sep=";", dtype=str, keep_default_na=False)
    columnas_csv = list(COLUMNAS.keys())
    df = df[columnas_csv]
    df.columns = list(COLUMNAS.values())

    # Limpiar espacios y dobles espacios en TODOS los campos de texto
    for col in df.columns:
        if col not in COLUMNAS_FECHA:
            df[col] = df[col].apply(lambda v: " ".join(v.split()))

    # Filtrar servicios excluidos
    if excluir_servicios:
        antes = len(df)
        excluir_upper = [s.upper().strip() for s in excluir_servicios]
        df = df[~df["servicio"].str.upper().str.strip().isin(excluir_upper)]
        excluidas = antes - len(df)
        if excluidas > 0:
            log.info(f"Filtradas {excluidas} guías de servicios excluidos: {excluir_servicios}")

    # Limpiar fechas: 0000-00-00, vacios, NaN, NaT -> None
    for col in COLUMNAS_FECHA:
        df[col] = df[col].apply(
            lambda v: None if (not isinstance(v, str) or v.strip() == "" or v.strip() == "0000-00-00" or v.strip().lower() == "nan") else v.strip()
        )
    return df


def conectar_db(config_db):
    """Crea conexion a PostgreSQL."""
    host = config_db.get("host", "")
    port = config_db.get("port", "5432")
    database = config_db.get("database", "")
    log.info(f"Conectando a PostgreSQL: host='{host}' port='{port}' database='{database}'")

    if not host:
        raise Exception(f"Host de PostgreSQL vacio. Config recibida: {config_db}")

    return psycopg2.connect(
        host=host,
        port=int(port) if isinstance(port, str) else port,
        database=database,
        user=config_db["usuario"],
        password=config_db["clave"],
        sslmode="require",
        connect_timeout=30,
    )


def crear_tabla_si_no_existe(cur):
    """Crea la tabla con guia como UNIQUE para permitir UPSERT."""
    # Definir tipos de cada columna
    tipos = {}
    for col_pg in COLUMNAS.values():
        tipos[col_pg] = "DATE" if col_pg in COLUMNAS_FECHA else "TEXT"

    columnas_sql = ",\n    ".join([f'"{col}" {tipos[col]}' for col in COLUMNAS.values()])

    crear_tabla = f"""
    CREATE TABLE IF NOT EXISTS informe_guias_tms (
        id SERIAL PRIMARY KEY,
        importado_el TIMESTAMP DEFAULT NOW(),
        {columnas_sql},
        UNIQUE (guia)
    );
    """
    cur.execute(crear_tabla)
    log.info("Tabla informe_guias_tms lista")


def upsert_csv(archivo_csv, config_db, excluir_servicios=None):
    """Inserta guías nuevas y actualiza las que ya existen (UPSERT) - OPTIMIZADO CON BATCH."""
    df = leer_csv(archivo_csv, excluir_servicios=excluir_servicios)
    filas_total = len(df)
    log.info(f"Procesando {filas_total} filas del CSV")

    conn = conectar_db(config_db)
    conn.autocommit = False
    cur = conn.cursor()
    crear_tabla_si_no_existe(cur)
    conn.commit()  # Confirmar CREATE TABLE para que rollback no la deshaga

    # Construir UPSERT: INSERT ... ON CONFLICT (guia) DO UPDATE
    cols = '", "'.join(df.columns)
    placeholders = ", ".join(["%s"] * len(df.columns))

    # Solo actualizar los campos que cambian
    update_cols = [c for c in CAMPOS_ACTUALIZAR if c in df.columns]
    update_set = ", ".join([f'"{c}" = EXCLUDED."{c}"' for c in update_cols])

    upsert_sql = f"""
    INSERT INTO informe_guias_tms ("{cols}")
    VALUES ({placeholders})
    ON CONFLICT (guia) DO UPDATE SET {update_set}
    """

    # Preparar datos: limpiar NaN/float antes del batch
    datos = []
    for _, fila in df.iterrows():
        valores = [None if (isinstance(v, float) and v != v) else v for v in fila.values]
        datos.append(tuple(valores))

    # Ejecutar en lotes de 1000 filas (MUCHO MÁS RÁPIDO)
    BATCH_SIZE = 1000
    lotes = [datos[i:i + BATCH_SIZE] for i in range(0, len(datos), BATCH_SIZE)]

    log.info(f"Enviando {len(datos)} filas en {len(lotes)} lotes de {BATCH_SIZE}...")

    for i, lote in enumerate(lotes, 1):
        try:
            execute_batch(cur, upsert_sql, lote, page_size=BATCH_SIZE)
            conn.commit()  # Commit después de cada lote exitoso
            log.info(f"Lote {i}/{len(lotes)} completado ({len(lote)} filas)")
        except Exception as e:
            log.warning(f"Error en lote {i}: {e}")
            conn.rollback()  # Solo deshace este lote
            continue

    # Contar resultados
    cur.execute("SELECT COUNT(*) FROM informe_guias_tms")
    total = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM informe_guias_tms WHERE estado NOT IN ('ENTREGADO', 'CON NOVEDAD')")
    pendientes = cur.fetchone()[0]

    log.info(f"Procesadas {filas_total} filas en {len(lotes)} lotes")
    log.info(f"Total en tabla: {total} | Pendientes de actualizar: {pendientes}")

    cur.close()
    conn.close()
    return pendientes


def obtener_fechas_pendientes(config_db):
    """Consulta la DB y devuelve la fecha minima de las guias pendientes."""
    conn = conectar_db(config_db)
    cur = conn.cursor()

    cur.execute("""
        SELECT MIN(fecha_emision), MAX(fecha_emision), COUNT(*)
        FROM informe_guias_tms
        WHERE estado NOT IN ('ENTREGADO', 'CON NOVEDAD')
          AND fecha_emision IS NOT NULL
    """)
    resultado = cur.fetchone()

    cur.close()
    conn.close()
    return resultado  # (fecha_min, fecha_max, cantidad)


def actualizar_pendientes(driver, carpeta_destino, config_db, excluir_servicios=None):
    """Descarga el historico de guias pendientes y las actualiza en la DB."""
    # Consultar que fechas tienen guias pendientes
    fecha_min, fecha_max, cantidad = obtener_fechas_pendientes(config_db)

    if cantidad == 0:
        log.info("No hay guias pendientes de actualizar")
        return

    log.info(f"Guias pendientes: {cantidad} (desde {fecha_min} hasta {fecha_max})")

    # Calcular rango de fechas a consultar (limitado a MAX_DIAS_HISTORICO)
    ayer = (ahora_colombia() - timedelta(days=1)).strftime("%Y-%m-%d")
    fecha_inicio = str(fecha_min)

    # Limitar el rango
    limite = (ahora_colombia() - timedelta(days=MAX_DIAS_HISTORICO)).strftime("%Y-%m-%d")
    if fecha_inicio < limite:
        fecha_inicio = limite
        log.info(f"Rango limitado a {MAX_DIAS_HISTORICO} dias: desde {fecha_inicio}")

    # Si el rango es solo ayer, no necesita descarga extra (ya se hizo)
    if fecha_inicio >= ayer:
        log.info("Las guias pendientes ya estan cubiertas en la descarga diaria")
        return

    # Descargar el historico
    log.info(f"Descargando historico: {fecha_inicio} a {ayer}")
    limpiar_previos(carpeta_destino)

    navegar_a_informe(driver)
    setear_fechas_y_buscar(driver, fecha_inicio, ayer)
    descargar_csv(driver)
    archivo = esperar_descarga(carpeta_destino, timeout=300)

    # Leer CSV y hacer UPSERT (OPTIMIZADO CON BATCH)
    df = leer_csv(archivo, excluir_servicios=excluir_servicios)
    log.info(f"Historico: {len(df)} filas leidas")

    conn = conectar_db(config_db)
    conn.autocommit = False
    cur = conn.cursor()

    cols = '", "'.join(df.columns)
    placeholders = ", ".join(["%s"] * len(df.columns))
    update_set = ", ".join([f'"{c}" = EXCLUDED."{c}"' for c in CAMPOS_ACTUALIZAR])
    upsert_sql = f"""
    INSERT INTO informe_guias_tms ("{cols}")
    VALUES ({placeholders})
    ON CONFLICT (guia) DO UPDATE SET {update_set}
    """

    # Preparar datos en lotes
    datos = [tuple(fila.values) for _, fila in df.iterrows()]

    # Ejecutar en lotes de 1000 filas
    BATCH_SIZE = 1000
    lotes = [datos[i:i + BATCH_SIZE] for i in range(0, len(datos), BATCH_SIZE)]

    log.info(f"Enviando {len(datos)} filas en {len(lotes)} lotes...")

    actualizados = 0
    for i, lote in enumerate(lotes, 1):
        try:
            execute_batch(cur, upsert_sql, lote, page_size=BATCH_SIZE)
            conn.commit()  # Commit después de cada lote exitoso
            actualizados += len(lote)
            log.info(f"Lote {i}/{len(lotes)} completado ({len(lote)} filas)")
        except Exception as e:
            log.warning(f"Error en lote {i}: {e}")
            conn.rollback()  # Solo deshace este lote
            continue

    # Contar pendientes restantes
    cur.execute("SELECT COUNT(*) FROM informe_guias_tms WHERE estado NOT IN ('ENTREGADO', 'CON NOVEDAD')")
    pendientes_restantes = cur.fetchone()[0]

    log.info(f"Historico procesado: {actualizados} filas")
    log.info(f"Pendientes restantes: {pendientes_restantes}")

    cur.close()
    conn.close()


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═════════════════════════════════════════════════════════════════════════════
def main():
    import argparse

    parser = argparse.ArgumentParser(description="Bot 001 - Informe General TMS")
    parser.add_argument("fecha_inicio", nargs="?", help="Fecha inicio YYYY-MM-DD (por defecto: ayer)")
    parser.add_argument("fecha_fin", nargs="?", help="Fecha fin YYYY-MM-DD (por defecto: igual a inicio)")
    args = parser.parse_args()

    # Determinar rango de fechas
    ayer = (ahora_colombia() - timedelta(days=1)).strftime("%Y-%m-%d")

    if args.fecha_inicio:
        fecha_inicio = args.fecha_inicio
        fecha_fin = args.fecha_fin if args.fecha_fin else args.fecha_inicio
        modo = "MANUAL"
    else:
        fecha_inicio = ayer
        fecha_fin = ayer
        modo = "AUTOMATICO"

    log.info("=" * 50)
    log.info(f"Bot 001 - Informe General TMS [{modo}]")
    log.info(f"Rango: {fecha_inicio} a {fecha_fin}")
    log.info("=" * 50)

    config = cargar_config()
    carpeta_destino = Path(config["descarga"]["carpeta_destino"])
    carpeta_destino.mkdir(parents=True, exist_ok=True)

    # Servicios excluidos: combinar config.json + variable de entorno (para GitHub Actions)
    excluir_servicios = config.get("excluir_servicios", [])
    env_excluir = os.environ.get("EXCLUIR_SERVICIOS", "")
    if env_excluir:
        excluir_servicios.extend([s.strip() for s in env_excluir.split(",") if s.strip()])
    if excluir_servicios:
        log.info(f"Servicios excluidos: {excluir_servicios}")

    driver = None
    archivo = None

    # ══ PASO 1: Descargar CSV ══════════════════════════════════════════════
    try:
        driver = crear_navegador(carpeta_destino)
        hacer_login(driver, config["tms"]["url"], config["tms"]["usuario"], config["tms"]["clave"])
        navegar_a_informe(driver)
        limpiar_previos(carpeta_destino)
        setear_fechas_y_buscar(driver, fecha_inicio, fecha_fin)
        descargar_csv(driver)
        archivo = esperar_descarga(carpeta_destino, timeout=300)

    except Exception as e:
        log.error(f"Error en descarga: {e}")
        if driver:
            driver.quit()
        raise

    # Cerrar navegador
    if driver:
        time.sleep(3)
        driver.quit()
        log.info("Navegador cerrado")

    # ══ PASO 2: Guardar en PostgreSQL (UPSERT) ══════════════════════════════
    pendientes = 0
    if archivo and "postgresql" in config:
        log.info("Guardando en PostgreSQL")
        log.info("=" * 50)
        try:
            pendientes = upsert_csv(archivo, config["postgresql"], excluir_servicios=excluir_servicios)
        except Exception as e:
            log.error(f"Error en PostgreSQL: {e}")

    # ══ PASO 3: Actualizar guias pendientes (solo en modo automatico) ═════
    if modo == "AUTOMATICO" and pendientes > 0 and "postgresql" in config:
        log.info("=" * 50)
        log.info("Actualizando guias pendientes de dias anteriores")
        log.info("=" * 50)
        try:
            driver = crear_navegador(carpeta_destino)
            hacer_login(driver, config["tms"]["url"], config["tms"]["usuario"], config["tms"]["clave"])
            actualizar_pendientes(driver, carpeta_destino, config["postgresql"], excluir_servicios=excluir_servicios)
        except Exception as e:
            log.error(f"Error actualizando pendientes: {e}")
        finally:
            if driver:
                time.sleep(3)
                driver.quit()
                log.info("Navegador cerrado")

    log.info("=" * 50)
    log.info("Proceso completado")
    log.info("=" * 50)


if __name__ == "__main__":
    main()
