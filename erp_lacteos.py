"""
ERP de inventarios para planta de lácteos
=========================================
Módulos: catálogos (productos, proveedores, operadores), recepción de leche,
pasteurización, producción (con suero y mermas), pedidos/despachos, pedidos devueltos,
almacén de producto terminado, programación diaria, trazabilidad por lote,
destinos (tiendas), resumen por semana/mes/año, Kardex
transaccional, inventario físico e indicadores.

Requisitos:   pip install "streamlit>=1.50" pandas
Ejecución:    streamlit run erp_lacteos.py

Todo se guarda en un archivo SQLite local (erp_lacteos.db).
Regla central: NO se edita el stock a mano; el stock siempre es la suma de
los movimientos del Kardex (ENTRADA - SALIDA).
"""
import sqlite3
from calendar import monthrange
from datetime import date, timedelta

import pandas as pd
import streamlit as st

DB = "erp_lacteos.db"

TIPOS_PRODUCTO = ["Materia prima", "Insumo", "Producto terminado", "Subproducto"]
UNIDADES = ["kg", "L", "empaque", "unidad"]
MOTIVOS_DEVOLUCION = ["Producto en mal estado", "Error en el pedido", "Rechazo del cliente",
                      "Producto vencido", "Empaque dañado", "Otro"]
DESTINO_REINGRESO = "Reingresa al almacén"
DESTINO_MERMA = "Merma (ya no se puede vender)"
MESES = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO",
         "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
VERSION_CATALOGO = 2
INSUMOS_PROCESO = ["Cloruro de calcio", "Cultivo", "Conservante", "Cuajo"]  # cuadro de insumos del control de proceso
TIENDAS_INICIALES = ["Tienda principal", "Tienda en Baños", "Tienda de la Plaza de Armas",
                     "Tienda de San Martín"]
RESULTADO_MASTITIS = ["Negativo", "Positivo"]
RESULTADO_ANTIBIOTICOS = ["Ausente", "Presente"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS productos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo TEXT UNIQUE NOT NULL,
    nombre TEXT NOT NULL,
    tipo TEXT NOT NULL,
    unidad TEXT NOT NULL,
    stock_minimo REAL DEFAULT 0,
    activo INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS proveedores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    ruta TEXT
);
CREATE TABLE IF NOT EXISTS operadores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    cargo TEXT,
    activo INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS meta (
    clave TEXT PRIMARY KEY,
    valor TEXT
);
CREATE TABLE IF NOT EXISTS pasteurizacion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    lote TEXT NOT NULL,
    litros REAL NOT NULL,
    temperatura REAL NOT NULL,
    tiempo_min REAL NOT NULL,
    hora_inicio TEXT,
    operador_id INTEGER NOT NULL REFERENCES operadores(id),
    conforme INTEGER NOT NULL,
    observacion TEXT
);
CREATE TABLE IF NOT EXISTS movimientos (          -- KARDEX
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    tipo TEXT NOT NULL CHECK (tipo IN ('ENTRADA','SALIDA')),
    motivo TEXT NOT NULL,
    cantidad REAL NOT NULL CHECK (cantidad > 0),
    documento TEXT,
    observacion TEXT
);
CREATE TABLE IF NOT EXISTS recepcion_leche (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    proveedor_id INTEGER REFERENCES proveedores(id),
    litros REAL NOT NULL,
    grasa REAL, acidez REAL, densidad REAL, temperatura REAL,
    aprobada INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS produccion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    lote TEXT NOT NULL,
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    litros_leche REAL NOT NULL,
    cantidad_obtenida REAL NOT NULL,
    suero_obtenido REAL DEFAULT 0,
    suero_vendido REAL DEFAULT 0,
    suero_usado REAL DEFAULT 0,
    operador_id INTEGER REFERENCES operadores(id)
);
CREATE TABLE IF NOT EXISTS pedidos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    cliente TEXT NOT NULL,
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    cant_pedida REAL NOT NULL,
    cant_despachada REAL NOT NULL,
    documento TEXT
);
CREATE TABLE IF NOT EXISTS devoluciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    cliente TEXT NOT NULL,
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    cantidad REAL NOT NULL,
    motivo TEXT NOT NULL,
    destino TEXT NOT NULL,
    despacho_id INTEGER REFERENCES pedidos(id),
    documento TEXT,
    observacion TEXT,
    anulado INTEGER NOT NULL DEFAULT 0,
    motivo_anulacion TEXT
);
CREATE TABLE IF NOT EXISTS destinos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    tipo TEXT NOT NULL DEFAULT 'Tienda',
    activo INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS programacion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    litros_programados REAL NOT NULL,
    responsable_id INTEGER REFERENCES operadores(id),
    observacion TEXT,
    anulado INTEGER NOT NULL DEFAULT 0,
    motivo_anulacion TEXT
);
CREATE TABLE IF NOT EXISTS produccion_insumos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    produccion_id INTEGER NOT NULL REFERENCES produccion(id),
    insumo TEXT NOT NULL,
    cantidad_g REAL,
    marca TEXT,
    lote TEXT,
    fecha_prod TEXT,
    fecha_venc TEXT
);
CREATE TABLE IF NOT EXISTS trazabilidad (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    produccion_id INTEGER REFERENCES produccion(id),
    lote TEXT,
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    responsable_id INTEGER REFERENCES operadores(id),
    fecha_produccion TEXT NOT NULL,
    fecha_empaque TEXT,
    fecha_envasado TEXT,
    fecha_maduracion TEXT,
    fecha_salida TEXT,
    destino_id INTEGER REFERENCES destinos(id),
    observacion TEXT,
    anulado INTEGER NOT NULL DEFAULT 0,
    motivo_anulacion TEXT
);
CREATE TABLE IF NOT EXISTS conteos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    stock_sistema REAL NOT NULL,
    stock_fisico REAL NOT NULL,
    diferencia REAL NOT NULL
);
"""

SEED_PRODUCTOS = [
    # Materias primas e insumos
    ("MP-LECHE", "Leche cruda", "Materia prima", "L", 0),
    ("MP-CUAJO", "Cuajo", "Materia prima", "kg", 0),
    ("IN-SAL", "Sal", "Insumo", "kg", 0),
    ("IN-CULT", "Cultivo lácteo", "Insumo", "empaque", 0),
    # Productos terminados (25)
    ("PT-MOZ", "Mozzarella", "Producto terminado", "kg", 0),
    ("PT-MANT", "Mantecoso", "Producto terminado", "kg", 0),
    ("PT-MANTEQ", "Mantequilla", "Producto terminado", "kg", 0),
    ("PT-QFRESCO", "Queso fresco", "Producto terminado", "kg", 0),
    ("PT-QUESILLO", "Quesillo", "Producto terminado", "kg", 0),
    ("PT-PARMESANO", "Parmesano", "Producto terminado", "kg", 0),
    ("PT-EDAM", "Edam", "Producto terminado", "kg", 0),
    ("PT-ANDINO", "Andino", "Producto terminado", "kg", 0),
    ("PT-PARIA", "Paria", "Producto terminado", "kg", 0),
    ("PT-SUIZO", "Suizo", "Producto terminado", "kg", 0),
    ("PT-PECANAS", "Pecanas", "Producto terminado", "kg", 0),
    ("PT-GOUDA", "Gouda", "Producto terminado", "kg", 0),
    ("PT-OREGANO", "Orégano", "Producto terminado", "kg", 0),
    ("PT-FINASHIERBAS", "Finas hierbas", "Producto terminado", "kg", 0),
    ("PT-DAMBO", "Dambo", "Producto terminado", "kg", 0),
    ("PT-ACEITUNA", "Aceituna", "Producto terminado", "kg", 0),
    ("PT-BABYSUIZO", "Baby suizo", "Producto terminado", "kg", 0),
    ("PT-TILSIT", "Tilsit", "Producto terminado", "kg", 0),
    ("PT-YOG", "Yogurt", "Producto terminado", "L", 0),
    ("PT-NATILLA", "Natilla", "Producto terminado", "kg", 0),
    ("PT-MANJAR", "Manjar", "Producto terminado", "kg", 0),
    ("PT-RIC", "Ricota", "Producto terminado", "kg", 0),
    ("PT-PROVOLONE", "Provolone", "Producto terminado", "kg", 0),
    ("PT-MERMELADA", "Mermelada", "Producto terminado", "kg", 0),
    ("PT-ROCOTO", "Queso con rocoto", "Producto terminado", "kg", 0),
    # Subproducto
    ("SB-SUERO", "Suero de leche", "Subproducto", "L", 0),
]


# ─────────────────────────── Capa de datos ───────────────────────────
def get_conn():
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = ON")
    return con


def q(sql, params=()):
    """SELECT -> DataFrame."""
    con = get_conn()
    try:
        return pd.read_sql_query(sql, con, params=params)
    finally:
        con.close()


def get_meta(con, clave, default=None):
    row = con.execute("SELECT valor FROM meta WHERE clave=?", (clave,)).fetchone()
    return row[0] if row else default


def set_meta(con, clave, valor):
    con.execute("INSERT INTO meta (clave, valor) VALUES (?,?) "
                "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (clave, str(valor)))


def migrar(con):
    """Actualiza bases de datos creadas con versiones anteriores del ERP (una sola vez)."""
    cols = [r[1] for r in con.execute("PRAGMA table_info(produccion)")]
    if "operador_id" not in cols:
        con.execute("ALTER TABLE produccion ADD COLUMN operador_id INTEGER REFERENCES operadores(id)")
    extra = {  # columnas agregadas en versiones nuevas: (tabla, columna, definición)
        "anulado": "INTEGER NOT NULL DEFAULT 0",
        "motivo_anulacion": "TEXT",
    }
    for tabla in ("recepcion_leche", "produccion", "pedidos", "conteos", "pasteurizacion"):
        cols_t = [r[1] for r in con.execute(f"PRAGMA table_info({tabla})")]
        for col, definicion in extra.items():
            if col not in cols_t:
                con.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {definicion}")
    if "activo" not in [r[1] for r in con.execute("PRAGMA table_info(proveedores)")]:
        con.execute("ALTER TABLE proveedores ADD COLUMN activo INTEGER NOT NULL DEFAULT 1")
    for tabla, col in (("conteos", "ajustado"), ("produccion", "merma_en_kardex")):
        cols_t = [r[1] for r in con.execute(f"PRAGMA table_info({tabla})")]
        if col not in cols_t:
            con.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
    nuevas = [  # columnas agregadas en esta versión
        ("recepcion_leche", "hora", "TEXT"), ("recepcion_leche", "n_tanques", "INTEGER"),
        ("recepcion_leche", "n_porongos", "INTEGER"), ("recepcion_leche", "procedencia", "TEXT"),
        ("recepcion_leche", "ph", "REAL"), ("recepcion_leche", "lactosa", "REAL"),
        ("recepcion_leche", "proteina", "REAL"), ("recepcion_leche", "sng", "REAL"),
        ("recepcion_leche", "mastitis", "TEXT"), ("recepcion_leche", "antibioticos", "TEXT"),
        ("recepcion_leche", "agua_l", "REAL"), ("recepcion_leche", "litros_aceptados", "REAL"),
        ("recepcion_leche", "responsable_id", "INTEGER REFERENCES operadores(id)"),
        ("pasteurizacion", "producto_id", "INTEGER REFERENCES productos(id)"),
        ("pedidos", "destino_id", "INTEGER REFERENCES destinos(id)"),
        ("pedidos", "responsable_envio_id", "INTEGER REFERENCES operadores(id)"),
        ("devoluciones", "destino_id", "INTEGER REFERENCES destinos(id)"),
        ("devoluciones", "responsable_id", "INTEGER REFERENCES operadores(id)"),
    ]
    for tabla, col, definicion in nuevas:
        if col not in [r[1] for r in con.execute(f"PRAGMA table_info({tabla})")]:
            con.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {definicion}")
    if int(get_meta(con, "catalogo_version", 0)) < VERSION_CATALOGO:
        con.execute("UPDATE productos SET codigo='MP-CUAJO', tipo='Materia prima' WHERE codigo='IN-CUAJO'")
        con.execute("UPDATE productos SET unidad='empaque' WHERE codigo='IN-CULT'")
        for cod, nom in [("PT-MOZ", "Mozzarella"), ("PT-MANT", "Mantecoso"), ("PT-RIC", "Ricota")]:
            con.execute("UPDATE productos SET nombre=? WHERE codigo=?", (nom, cod))
        # los mínimos de la versión anterior eran de ejemplo: se dejan en 0 (sin alerta)
        con.execute("UPDATE productos SET stock_minimo=0 WHERE codigo IN "
                    "('MP-LECHE','MP-CUAJO','IN-SAL','IN-CULT','PT-MOZ','PT-MANT','PT-MANTEQ',"
                    "'PT-RIC','PT-YOG','SB-SUERO')")
        con.executemany(
            "INSERT OR IGNORE INTO productos (codigo, nombre, tipo, unidad, stock_minimo) "
            "VALUES (?,?,?,?,?)", SEED_PRODUCTOS)
        set_meta(con, "catalogo_version", VERSION_CATALOGO)
    if get_meta(con, "destinos_iniciales") is None:  # se cargan una sola vez; después se administran en Catálogos
        for nombre in TIENDAS_INICIALES:
            if not con.execute("SELECT 1 FROM destinos WHERE nombre = ? COLLATE NOCASE", (nombre,)).fetchone():
                con.execute("INSERT INTO destinos (nombre, tipo) VALUES (?, 'Tienda')", (nombre,))
        set_meta(con, "destinos_iniciales", 1)
    if get_meta(con, "past_temp_min") is None:
        set_meta(con, "past_temp_min", 63)      # °C  (ajustable en el módulo Pasteurización)
        set_meta(con, "past_tiempo_min", 30)    # minutos


def init_db():
    con = get_conn()
    con.executescript(SCHEMA)
    migrar(con)
    con.commit()
    con.close()


def producto_id(con, codigo):
    row = con.execute("SELECT id FROM productos WHERE codigo=?", (codigo,)).fetchone()
    if row is None:
        raise ValueError(f"No existe el producto con código {codigo}")
    return row[0]


def stock_de(con, pid):
    return con.execute(
        "SELECT COALESCE(SUM(CASE tipo WHEN 'ENTRADA' THEN cantidad ELSE -cantidad END),0) "
        "FROM movimientos WHERE producto_id=?", (pid,)).fetchone()[0]


def mover(con, fecha, pid, tipo, motivo, cantidad, documento=None, obs=None):
    """Único punto de entrada al Kardex. Impide stock negativo."""
    if cantidad <= 0:
        raise ValueError("La cantidad debe ser mayor que cero.")
    if tipo == "SALIDA":
        disponible = stock_de(con, pid)
        if cantidad > disponible + 1e-9:
            nombre = con.execute("SELECT nombre FROM productos WHERE id=?", (pid,)).fetchone()[0]
            raise ValueError(f"Stock insuficiente de {nombre}: hay {disponible:.2f}, "
                             f"se intenta sacar {cantidad:.2f}.")
    con.execute(
        "INSERT INTO movimientos (fecha, producto_id, tipo, motivo, cantidad, documento, observacion) "
        "VALUES (?,?,?,?,?,?,?)",
        (str(fecha), pid, tipo, motivo, cantidad, documento, obs))


def transaccion(fn):
    """Ejecuta fn(con) y hace commit; si algo falla, revierte todo."""
    con = get_conn()
    try:
        fn(con)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ── Operaciones de negocio ──
def registrar_recepcion(fecha, proveedor_id, litros, grasa, acidez, densidad, temp, aprobada, *,
                        hora=None, n_tanques=0, n_porongos=0, procedencia=None, ph=None, lactosa=None,
                        proteina=None, sng=None, mastitis=None, antibioticos=None, agua_l=0.0,
                        aceptada=None, responsable_id=None):
    """Registra la llegada y el análisis de la leche. Al inventario entra solo la leche ACEPTADA."""
    if litros <= 0:
        raise ValueError("Los litros recibidos deben ser mayores que cero.")
    agua_l = agua_l or 0.0
    if agua_l < 0 or agua_l > litros + 1e-9:
        raise ValueError("El agua detectada no puede ser negativa ni mayor que los litros recibidos.")
    if aprobada and antibioticos == "Presente":
        raise ValueError("Leche con residuos de antibióticos no debe aprobarse: "
                         "desmarca «Aprobada» o corrige el análisis.")
    if aprobada:
        acept = aceptada if aceptada and aceptada > 0 else litros - agua_l
        if acept <= 0:
            raise ValueError("No queda leche aceptada para ingresar al inventario.")
        if acept > litros + 1e-9:
            raise ValueError("La leche aceptada no puede ser mayor que la recibida.")
    else:
        acept = 0.0

    def _op(con):
        con.execute(
            "INSERT INTO recepcion_leche (fecha, proveedor_id, litros, grasa, acidez, densidad, "
            "temperatura, aprobada, hora, n_tanques, n_porongos, procedencia, ph, lactosa, proteina, sng, "
            "mastitis, antibioticos, agua_l, litros_aceptados, responsable_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(fecha), proveedor_id, litros, grasa, acidez, densidad, temp, int(aprobada), hora,
             n_tanques, n_porongos, procedencia, ph, lactosa, proteina, sng, mastitis, antibioticos,
             agua_l, acept, responsable_id))
        if aprobada:  # la leche rechazada no ingresa al inventario
            mover(con, fecha, producto_id(con, "MP-LECHE"), "ENTRADA",
                  "Recepción de leche", acept, documento=f"REC-{fecha}")
    transaccion(_op)


def registrar_produccion(fecha, lote, pid, litros, obtenida, suero, s_vendido, s_usado,
                         operador_id=None, insumos=None):
    """Registra la producción. También abre su registro de trazabilidad y guarda los insumos usados
    (los insumos se registran solo como dato del lote: su almacén no forma parte del ERP)."""
    if obtenida <= 0:
        raise ValueError("La cantidad obtenida debe ser mayor que cero.")
    if s_vendido + s_usado > suero + 1e-9:
        raise ValueError("Suero vendido + usado no puede superar el suero obtenido.")

    def _op(con):
        con.execute(
            "INSERT INTO produccion (fecha, lote, producto_id, litros_leche, cantidad_obtenida, "
            "suero_obtenido, suero_vendido, suero_usado, operador_id, merma_en_kardex) "
            "VALUES (?,?,?,?,?,?,?,?,?,1)",
            (str(fecha), lote, pid, litros, obtenida, suero, s_vendido, s_usado, operador_id))
        prod_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.execute("INSERT INTO trazabilidad (produccion_id, lote, producto_id, responsable_id, "
                    "fecha_produccion) VALUES (?,?,?,?,?)", (prod_id, lote, pid, operador_id, str(fecha)))
        for it in (insumos or []):
            if it.get("cantidad_g") or (it.get("marca") or "").strip() or (it.get("lote") or "").strip():
                con.execute(
                    "INSERT INTO produccion_insumos (produccion_id, insumo, cantidad_g, marca, lote, "
                    "fecha_prod, fecha_venc) VALUES (?,?,?,?,?,?,?)",
                    (prod_id, it["insumo"], it.get("cantidad_g") or None, (it.get("marca") or "").strip() or None,
                     (it.get("lote") or "").strip() or None, (it.get("fecha_prod") or "").strip() or None,
                     (it.get("fecha_venc") or "").strip() or None))
        leche, suero_id = producto_id(con, "MP-LECHE"), producto_id(con, "SB-SUERO")
        if litros > 0:  # productos sin leche (p. ej. mermelada) no descuentan leche
            mover(con, fecha, leche, "SALIDA", "Consumo en producción", litros, lote)
        mover(con, fecha, pid, "ENTRADA", "Producción terminada", obtenida, lote)
        if suero > 0:
            mover(con, fecha, suero_id, "ENTRADA", "Suero de producción", suero, lote)
        if s_vendido > 0:
            mover(con, fecha, suero_id, "SALIDA", "Venta de suero", s_vendido, lote)
        if s_usado > 0:
            mover(con, fecha, suero_id, "SALIDA", "Uso interno de suero", s_usado, lote)
        merma = suero - s_vendido - s_usado
        if merma > 1e-9:  # el suero perdido NO debe quedar como stock
            mover(con, fecha, suero_id, "SALIDA", "Merma de suero", merma, lote)
    transaccion(_op)


def registrar_pasteurizacion(fecha, lote, litros, temperatura, tiempo_min, hora_inicio,
                             operador_id, observacion=None, producto_id_=None):
    """Registra una pasteurización y devuelve True si cumplió los parámetros vigentes."""
    if litros <= 0:
        raise ValueError("Los litros pasteurizados deben ser mayores que cero.")
    if operador_id is None:
        raise ValueError("Selecciona el operador responsable.")
    resultado = {}

    def _op(con):
        t_min = float(get_meta(con, "past_temp_min", 63))
        m_min = float(get_meta(con, "past_tiempo_min", 30))
        conforme = temperatura >= t_min and tiempo_min >= m_min
        con.execute(
            "INSERT INTO pasteurizacion (fecha, lote, litros, temperatura, tiempo_min, hora_inicio, "
            "operador_id, conforme, observacion, producto_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (str(fecha), lote, litros, temperatura, tiempo_min, hora_inicio, operador_id,
             int(conforme), observacion, producto_id_))
        resultado["conforme"] = conforme
    transaccion(_op)
    return resultado["conforme"]


def _marcar_anulado(con, tabla, rid, motivo):
    if not motivo or not motivo.strip():
        raise ValueError("El motivo de la anulación es obligatorio.")
    fila = con.execute(f"SELECT anulado FROM {tabla} WHERE id=?", (rid,)).fetchone()
    if fila is None:
        raise ValueError("El registro no existe.")
    if fila[0]:
        raise ValueError("Ese registro ya está anulado.")
    con.execute(f"UPDATE {tabla} SET anulado=1, motivo_anulacion=? WHERE id=?", (motivo.strip(), rid))


def _transaccion_anulacion(fn):
    try:
        transaccion(fn)
    except ValueError as e:
        if str(e).startswith("Stock insuficiente"):
            raise ValueError(f"No se puede anular: {e} Probablemente ese producto ya se despachó o "
                             "se usó; anula primero ese otro registro.") from None
        raise


def anular_recepcion(rid, motivo):
    def _op(con):
        _marcar_anulado(con, "recepcion_leche", rid, motivo)
        litros, aprobada = con.execute(
            "SELECT COALESCE(litros_aceptados, litros), aprobada FROM recepcion_leche WHERE id=?",
            (rid,)).fetchone()
        if aprobada and litros > 0:
            mover(con, date.today(), producto_id(con, "MP-LECHE"), "SALIDA",
                  "Anulación de recepción", litros, f"ANUL-REC-{rid}", motivo)
    _transaccion_anulacion(_op)


def anular_produccion(rid, motivo):
    def _op(con):
        _marcar_anulado(con, "produccion", rid, motivo)
        con.execute("UPDATE trazabilidad SET anulado=1, motivo_anulacion=? WHERE produccion_id=?",
                    (motivo, rid))
        pid, litros, obtenida, suero, vendido, usado, con_merma = con.execute(
            "SELECT producto_id, litros_leche, cantidad_obtenida, suero_obtenido, suero_vendido, "
            "suero_usado, merma_en_kardex FROM produccion WHERE id=?", (rid,)).fetchone()
        hoy, doc, mot = date.today(), f"ANUL-PROD-{rid}", "Anulación de producción"
        leche, suero_id = producto_id(con, "MP-LECHE"), producto_id(con, "SB-SUERO")
        # 1) primero se devuelve lo que salió; 2) luego se retira lo que había entrado
        if litros > 0:
            mover(con, hoy, leche, "ENTRADA", mot, litros, doc, motivo)
        if vendido > 0:
            mover(con, hoy, suero_id, "ENTRADA", mot, vendido, doc, motivo)
        if usado > 0:
            mover(con, hoy, suero_id, "ENTRADA", mot, usado, doc, motivo)
        merma = suero - vendido - usado
        if con_merma and merma > 1e-9:
            mover(con, hoy, suero_id, "ENTRADA", mot, merma, doc, motivo)
        if suero > 0:
            mover(con, hoy, suero_id, "SALIDA", mot, suero, doc, motivo)
        mover(con, hoy, pid, "SALIDA", mot, obtenida, doc, motivo)
    _transaccion_anulacion(_op)


def anular_devolucion(rid, motivo):
    def _op(con):
        _marcar_anulado(con, "devoluciones", rid, motivo)
        pid, cantidad, destino = con.execute(
            "SELECT producto_id, cantidad, destino FROM devoluciones WHERE id=?", (rid,)).fetchone()
        if destino == DESTINO_REINGRESO:  # lo que había reingresado al stock se retira
            mover(con, date.today(), pid, "SALIDA", "Anulación de devolución", cantidad,
                  f"ANUL-DEV-{rid}", motivo)
    _transaccion_anulacion(_op)


def anular_despacho(rid, motivo):
    def _op(con):
        if con.execute("SELECT COUNT(*) FROM devoluciones WHERE despacho_id=? AND anulado=0",
                       (rid,)).fetchone()[0]:
            raise ValueError("Ese despacho tiene devoluciones registradas: anúlalas primero.")
        _marcar_anulado(con, "pedidos", rid, motivo)
        pid, despachada = con.execute(
            "SELECT producto_id, cant_despachada FROM pedidos WHERE id=?", (rid,)).fetchone()
        if despachada > 0:
            mover(con, date.today(), pid, "ENTRADA", "Anulación de despacho", despachada,
                  f"ANUL-DESP-{rid}", motivo)
    _transaccion_anulacion(_op)


def anular_conteo(rid, motivo):
    def _op(con):
        _marcar_anulado(con, "conteos", rid, motivo)
        pid, dif, ajustado = con.execute(
            "SELECT producto_id, diferencia, ajustado FROM conteos WHERE id=?", (rid,)).fetchone()
        if ajustado and abs(dif) > 1e-9:  # deshace el ajuste que se hizo al Kardex
            mover(con, date.today(), pid, "SALIDA" if dif > 0 else "ENTRADA",
                  "Anulación de ajuste por conteo", abs(dif), f"ANUL-CONTEO-{rid}", motivo)
    _transaccion_anulacion(_op)


def anular_pasteurizacion(rid, motivo):
    transaccion(lambda con: _marcar_anulado(con, "pasteurizacion", rid, motivo))


def registrar_programacion(fecha, pid, litros, responsable_id=None, observacion=None):
    if litros <= 0:
        raise ValueError("Los litros programados deben ser mayores que cero.")
    transaccion(lambda con: con.execute(
        "INSERT INTO programacion (fecha, producto_id, litros_programados, responsable_id, observacion) "
        "VALUES (?,?,?,?,?)", (str(fecha), pid, litros, responsable_id, observacion)))


def anular_programacion(rid, motivo):
    transaccion(lambda con: _marcar_anulado(con, "programacion", rid, motivo))


def actualizar_trazabilidad(rid, fecha_empaque=None, fecha_envasado=None, fecha_maduracion=None,
                            fecha_salida=None, destino_id=None, observacion=None):
    """Completa las etapas de un lote a medida que ocurren; lo que va vacío no se toca."""
    campos = {"fecha_empaque": fecha_empaque, "fecha_envasado": fecha_envasado,
              "fecha_maduracion": fecha_maduracion, "fecha_salida": fecha_salida,
              "destino_id": destino_id, "observacion": observacion}
    campos = {k: (str(v) if k.startswith("fecha") else v) for k, v in campos.items() if v not in (None, "")}
    if not campos:
        raise ValueError("No llenaste ninguna fecha ni el destino.")

    def _op(con):
        fila = con.execute("SELECT fecha_produccion, anulado FROM trazabilidad WHERE id=?", (rid,)).fetchone()
        if fila is None or fila[1]:
            raise ValueError("Ese registro no existe o está anulado.")
        for k, v in campos.items():
            if k.startswith("fecha") and v < fila[0]:
                raise ValueError("Ninguna etapa puede tener fecha anterior a la de producción "
                                 f"({fila[0]}).")
        sets = ", ".join(f"{k}=?" for k in campos)
        con.execute(f"UPDATE trazabilidad SET {sets} WHERE id=?", (*campos.values(), rid))
    transaccion(_op)


def dar_de_baja_destino(did):
    transaccion(lambda con: con.execute("UPDATE destinos SET activo=0 WHERE id=?", (did,)))


def reactivar_destino(did):
    transaccion(lambda con: con.execute("UPDATE destinos SET activo=1 WHERE id=?", (did,)))


def eliminar_destino(did):
    try:
        transaccion(lambda con: con.execute("DELETE FROM destinos WHERE id=?", (did,)))
    except sqlite3.IntegrityError:
        raise ValueError("Ese destino ya tiene despachos, devoluciones o trazabilidad registrados, "
                         "así que no se puede eliminar. Usa «Dar de baja».") from None


def dar_de_baja_producto(pid):
    con = get_conn()
    try:
        stock = stock_de(con, pid)
        if abs(stock) > 1e-9:
            raise ValueError(f"No se puede dar de baja: aún tiene stock ({stock:.2f}). "
                             "Déjalo en 0 antes (por despacho, producción o inventario físico).")
        con.execute("UPDATE productos SET activo=0 WHERE id=?", (pid,))
        con.commit()
    finally:
        con.close()


def reactivar_producto(pid):
    con = get_conn()
    try:
        con.execute("UPDATE productos SET activo=1 WHERE id=?", (pid,))
        con.commit()
    finally:
        con.close()


def eliminar_proveedor(pid):
    con = get_conn()
    try:
        con.execute("DELETE FROM proveedores WHERE id=?", (pid,))
        con.commit()
    except sqlite3.IntegrityError:
        raise ValueError("Ese proveedor ya tiene recepciones de leche registradas, "
                         "así que no se puede eliminar.")
    finally:
        con.close()


def dar_de_baja_proveedor(pid):
    """El proveedor deja de aparecer al registrar leche, pero conserva su historial."""
    con = get_conn()
    try:
        con.execute("UPDATE proveedores SET activo=0 WHERE id=?", (pid,))
        con.commit()
    finally:
        con.close()


def reactivar_proveedor(pid):
    con = get_conn()
    try:
        con.execute("UPDATE proveedores SET activo=1 WHERE id=?", (pid,))
        con.commit()
    finally:
        con.close()


def reactivar_operador(oid):
    con = get_conn()
    try:
        con.execute("UPDATE operadores SET activo=1 WHERE id=?", (oid,))
        con.commit()
    finally:
        con.close()


def eliminar_operador(oid):
    con = get_conn()
    try:
        con.execute("DELETE FROM operadores WHERE id=?", (oid,))
        con.commit()
    except sqlite3.IntegrityError:
        raise ValueError("Ese operador ya tiene registros de pasteurización o producción, "
                         "así que no se puede eliminar. Usa «Dar de baja».")
    finally:
        con.close()


def dar_de_baja_operador(oid):
    """El operador deja de aparecer en las listas, pero conserva su historial."""
    con = get_conn()
    try:
        con.execute("UPDATE operadores SET activo=0 WHERE id=?", (oid,))
        con.commit()
    finally:
        con.close()


def actualizar_producto(pid, nombre, tipo, unidad, minimo):
    if not nombre.strip():
        raise ValueError("El nombre es obligatorio.")
    con = get_conn()
    try:
        con.execute("UPDATE productos SET nombre=?, tipo=?, unidad=?, stock_minimo=? WHERE id=?",
                    (nombre.strip(), tipo, unidad, minimo, pid))
        con.commit()
    finally:
        con.close()


def _nombre_destino(con, destino_id):
    fila = con.execute("SELECT nombre FROM destinos WHERE id=?", (destino_id,)).fetchone()
    if fila is None:
        raise ValueError("El destino elegido no existe.")
    return fila[0]


def registrar_despacho(fecha, cliente, pid, pedida, despachada, documento, destino_id=None,
                       responsable_id=None):
    if despachada > pedida:
        raise ValueError("No se puede despachar más de lo pedido.")
    if destino_id is None and not (cliente or "").strip():
        raise ValueError("Elige la tienda de destino o escribe el cliente.")

    def _op(con):
        cli = _nombre_destino(con, destino_id) if destino_id is not None else cliente
        con.execute(
            "INSERT INTO pedidos (fecha, cliente, producto_id, cant_pedida, cant_despachada, documento, "
            "destino_id, responsable_envio_id) VALUES (?,?,?,?,?,?,?,?)",
            (str(fecha), cli, pid, pedida, despachada, documento, destino_id, responsable_id))
        if despachada > 0:
            mover(con, fecha, pid, "SALIDA", "Despacho a cliente", despachada, documento, cli)
    transaccion(_op)


def registrar_devolucion(fecha, cliente, pid, cantidad, motivo, destino, despacho_id=None,
                         documento=None, observacion=None, destino_id=None, responsable_id=None):
    if cantidad <= 0:
        raise ValueError("La cantidad devuelta debe ser mayor que cero.")
    if despacho_id is None and destino_id is None and not (cliente or "").strip():
        raise ValueError("Indica la tienda o el cliente, o elige el despacho de origen.")

    def _op(con):
        cli, prod = cliente, pid
        dest_id = destino_id
        if despacho_id is None and destino_id is not None:
            cli = _nombre_destino(con, destino_id)
        if despacho_id is not None:
            fila = con.execute("SELECT cliente, producto_id, cant_despachada, anulado "
                               "FROM pedidos WHERE id=?", (despacho_id,)).fetchone()
            if fila is None or fila[3]:
                raise ValueError("El despacho elegido no existe o está anulado.")
            cli, prod, despachada = fila[0], fila[1], fila[2]
            dest_id = con.execute("SELECT destino_id FROM pedidos WHERE id=?", (despacho_id,)).fetchone()[0]
            ya = con.execute("SELECT COALESCE(SUM(cantidad), 0) FROM devoluciones "
                             "WHERE despacho_id=? AND anulado=0", (despacho_id,)).fetchone()[0]
            if cantidad + ya > despachada + 1e-9:
                raise ValueError(f"Ese despacho fue de {despachada:g} y ya se devolvieron {ya:g}; "
                                 f"no se pueden devolver {cantidad:g} más.")
        con.execute(
            "INSERT INTO devoluciones (fecha, cliente, producto_id, cantidad, motivo, destino, "
            "despacho_id, documento, observacion, destino_id, responsable_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (str(fecha), cli, prod, cantidad, motivo, destino, despacho_id, documento, observacion,
             dest_id, responsable_id))
        if destino == DESTINO_REINGRESO:
            mover(con, fecha, prod, "ENTRADA", "Devolución de cliente", cantidad, documento, cli)
    transaccion(_op)


def registrar_conteo(fecha, pid, fisico, ajustar):
    def _op(con):
        sistema = stock_de(con, pid)
        dif = fisico - sistema
        con.execute(
            "INSERT INTO conteos (fecha, producto_id, stock_sistema, stock_fisico, diferencia, ajustado) "
            "VALUES (?,?,?,?,?,?)",
            (str(fecha), pid, sistema, fisico, dif, int(ajustar and abs(dif) > 1e-9)))
        if ajustar and abs(dif) > 1e-9:
            mover(con, fecha, pid, "ENTRADA" if dif > 0 else "SALIDA",
                  "Ajuste por inventario físico", abs(dif), f"CONTEO-{fecha}")
    transaccion(_op)


# ── Consultas ──
def stock_actual():
    return q("""
        SELECT p.id, p.codigo, p.nombre, p.tipo, p.unidad, p.stock_minimo,
               COALESCE(SUM(CASE m.tipo WHEN 'ENTRADA' THEN m.cantidad ELSE -m.cantidad END), 0) AS stock
        FROM productos p LEFT JOIN movimientos m ON m.producto_id = p.id
        WHERE p.activo = 1 GROUP BY p.id ORDER BY p.tipo, p.nombre""")


def kardex(producto=None):
    df = q("""
        SELECT m.id, m.fecha, p.nombre AS producto, p.unidad, m.tipo, m.motivo,
               m.cantidad, m.documento, m.observacion
        FROM movimientos m JOIN productos p ON p.id = m.producto_id
        ORDER BY p.nombre, m.fecha, m.id""")
    if df.empty:
        return df
    df["entrada"] = df.cantidad.where(df.tipo == "ENTRADA", 0.0)
    df["salida"] = df.cantidad.where(df.tipo == "SALIDA", 0.0)
    df["saldo"] = (df.entrada - df.salida).groupby(df["producto"]).cumsum()
    df = df.drop(columns=["cantidad", "tipo"])
    if producto:
        df = df[df["producto"] == producto]
    return df


def indicadores(desde, hasta, docs_fisicos=0):
    """Indicadores de la tesis: existencias, exactitud, fill rate y merma."""
    d, h = str(desde), str(hasta)
    out = {}

    # 1) Control de existencias: movimientos con documento / documentos físicos
    con_doc = q("SELECT COUNT(*) n FROM movimientos WHERE fecha BETWEEN ? AND ? "
                "AND documento IS NOT NULL AND motivo NOT LIKE 'Anulación%'", (d, h)).n[0]
    out["control_existencias"] = (con_doc / docs_fisicos * 100) if docs_fisicos else None

    # 2) Exactitud: conteos sin diferencia / conteos realizados
    c = q("SELECT diferencia FROM conteos WHERE fecha BETWEEN ? AND ? AND anulado = 0", (d, h))
    out["exactitud"] = ((c.diferencia.abs() < 1e-9).mean() * 100) if len(c) else None

    # 3) Fill rate: cantidad despachada / cantidad pedida
    p = q("SELECT SUM(cant_pedida) ped, SUM(cant_despachada) des FROM pedidos "
          "WHERE fecha BETWEEN ? AND ? AND anulado = 0", (d, h))
    out["fill_rate"] = (p.des[0] / p.ped[0] * 100) if p.ped[0] else None

    # 4) Coeficiente de recuperación de leche = producto obtenido / leche usada x 100
    pr = q("""SELECT p.nombre, SUM(litros_leche) leche, SUM(cantidad_obtenida) obtenido,
                     SUM(suero_obtenido) suero, SUM(suero_vendido) vendido, SUM(suero_usado) usado
              FROM produccion pr JOIN productos p ON p.id = pr.producto_id
              WHERE pr.fecha BETWEEN ? AND ? AND pr.litros_leche > 0 AND pr.anulado = 0 GROUP BY p.nombre""", (d, h))
    if len(pr):
        pr["recuperacion_%"] = pr.obtenido / pr.leche * 100
        out["recuperacion_global"] = pr.obtenido.sum() / pr.leche.sum() * 100
        out["suero_merma_l"] = (pr.suero - pr.vendido - pr.usado).sum()
        out["suero_merma_%"] = out["suero_merma_l"] / pr.suero.sum() * 100 if pr.suero.sum() else None
    else:
        out["recuperacion_global"] = out["suero_merma_l"] = out["suero_merma_%"] = None
    out["detalle_produccion"] = pr

    # 5) Pasteurizaciones conformes / pasteurizaciones realizadas
    pa = q("SELECT conforme FROM pasteurizacion WHERE fecha BETWEEN ? AND ? AND anulado = 0", (d, h))
    out["pasteurizacion_conforme"] = (pa.conforme.mean() * 100) if len(pa) else None
    out["pasteurizaciones"] = len(pa)

    # 6) Tasa de devoluciones = cantidad devuelta / cantidad despachada x 100
    dv = q("SELECT COALESCE(SUM(cantidad), 0) AS dev FROM devoluciones "
           "WHERE fecha BETWEEN ? AND ? AND anulado = 0", (d, h))
    despachado = p.des[0]
    out["devuelto"] = float(dv.dev[0])
    out["tasa_devoluciones"] = (dv.dev[0] / despachado * 100
                                if despachado is not None and pd.notna(despachado) and despachado > 0
                                else None)
    return out


# ─────────────────────────── Interfaz ───────────────────────────
def pick_producto(label, tipos=None, key=None):
    df = stock_actual()
    if tipos:
        df = df[df.tipo.isin(tipos)]
    opts = dict(zip(df.id.tolist(), (df.nombre + " (" + df.unidad + ")").tolist()))
    return st.selectbox(label, list(opts), format_func=opts.get, key=key)


def pick_proveedor():
    df = q("SELECT id, nombre FROM proveedores WHERE activo=1 ORDER BY nombre")
    if df.empty:
        st.info("Registra primero un proveedor en «Catálogos».")
        return None
    opts = dict(zip(df.id.tolist(), df.nombre.tolist()))
    return st.selectbox("Proveedor", list(opts), format_func=opts.get)


def pick_operador(label="Operador", obligatorio=True, key=None):
    df = q("SELECT id, nombre FROM operadores WHERE activo=1 ORDER BY nombre")
    if df.empty:
        if obligatorio:
            st.info("Registra primero a los operadores en «Catálogos» → «Operadores».")
        return None
    opts = dict(zip(df.id.tolist(), df.nombre.tolist()))
    ids = list(opts) if obligatorio else [None] + list(opts)
    return st.selectbox(label, ids, format_func=lambda i: opts.get(i, "(sin asignar)"), key=key)


def pick_producto_opcional(label, tipos=None, key=None):
    df = stock_actual()
    if tipos:
        df = df[df.tipo.isin(tipos)]
    opts = dict(zip(df.id.tolist(), (df.nombre + " (" + df.unidad + ")").tolist()))
    return st.selectbox(label, [None] + list(opts), format_func=lambda i: opts.get(i, "(sin especificar)"),
                        key=key)


def pick_destino(label="Tienda de destino", opcional=True, key=None):
    df = q("SELECT id, nombre FROM destinos WHERE activo=1 ORDER BY nombre")
    opts = dict(zip(df.id.tolist(), df.nombre.tolist()))
    ids = ([None] if opcional else []) + list(opts)
    if not ids:
        st.info("Registra primero las tiendas en «Catálogos» → «Destinos (tiendas)».")
        return None
    return st.selectbox(label, ids, format_func=lambda i: opts.get(i, "(otro cliente / no es tienda)"),
                        key=key)


def guardar(fn, *args, ok="Registrado correctamente."):
    """Ejecuta la operación y muestra el resultado. Devuelve True si se guardó."""
    try:
        fn(*args)
        st.success(ok)
        return True
    except (ValueError, sqlite3.Error) as e:
        st.error(str(e))
        return False


def pag_dashboard():
    st.header("Panel de inventario")
    df = stock_actual()

    def estado(r):
        if r.stock_minimo <= 0:
            return "—"
        return "⚠️ Bajo mínimo" if r.stock < r.stock_minimo else "OK"

    df["estado"] = df.apply(estado, axis=1)
    bajos = int(((df.stock_minimo > 0) & (df.stock < df.stock_minimo)).sum())
    c1, c2, c3 = st.columns(3)
    c1.metric("Productos activos", len(df))
    c2.metric("Bajo stock mínimo", bajos)
    c3.metric("Movimientos en Kardex", int(q("SELECT COUNT(*) n FROM movimientos").n[0]))
    st.caption("El estado solo se calcula si el producto tiene un stock mínimo mayor que 0 "
               "(se define en Catálogos → Productos → Editar).")
    st.dataframe(df[["codigo", "nombre", "tipo", "unidad", "stock", "stock_minimo", "estado"]],
                 width="stretch", hide_index=True)


def pag_catalogos():
    st.header("Catálogos")
    t1, t2, t3, t4 = st.tabs(["Productos", "Proveedores", "Operadores", "Destinos (tiendas)"])
    with t1:
        st.subheader("Agregar producto")
        with st.form("f_prod", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            cod = c1.text_input("Código")
            nom = c2.text_input("Nombre")
            tipo = c3.selectbox("Tipo", TIPOS_PRODUCTO)
            u1, u2 = st.columns(2)
            uni = u1.selectbox("Unidad", UNIDADES)
            minimo = u2.number_input("Stock mínimo (0 = sin alerta)", min_value=0.0, step=1.0)
            if st.form_submit_button("Agregar producto"):
                def _add():
                    if not cod.strip() or not nom.strip():
                        raise ValueError("Código y nombre son obligatorios.")
                    con = get_conn()
                    try:
                        con.execute("INSERT INTO productos (codigo, nombre, tipo, unidad, stock_minimo) "
                                    "VALUES (?,?,?,?,?)", (cod.strip(), nom.strip(), tipo, uni, minimo))
                        con.commit()
                    except sqlite3.IntegrityError:
                        raise ValueError("Ya existe un producto con ese código.")
                    finally:
                        con.close()
                guardar(_add)

        st.subheader("Editar producto")
        prods = q("SELECT id, codigo, nombre, tipo, unidad, stock_minimo FROM productos ORDER BY tipo, nombre")
        opts = dict(zip(prods.id.tolist(), (prods.nombre + " · " + prods.codigo).tolist()))
        sel = st.selectbox("Producto a editar", list(opts), format_func=opts.get)
        r = prods[prods.id == sel].iloc[0]
        with st.form("f_edit"):
            e1, e2 = st.columns(2)
            n_nom = e1.text_input("Nombre", r.nombre, key=f"ed_nom_{sel}")
            n_tipo = e2.selectbox("Tipo", TIPOS_PRODUCTO, index=TIPOS_PRODUCTO.index(r.tipo),
                                  key=f"ed_tipo_{sel}")
            e3, e4 = st.columns(2)
            unidades = UNIDADES if r.unidad in UNIDADES else UNIDADES + [r.unidad]
            n_uni = e3.selectbox("Unidad", unidades, index=unidades.index(r.unidad), key=f"ed_uni_{sel}")
            n_min = e4.number_input("Stock mínimo (0 = sin alerta)", min_value=0.0, step=1.0,
                                    value=float(r.stock_minimo), key=f"ed_min_{sel}")
            st.caption("Cambiar la unidad de un producto que ya tiene movimientos no convierte "
                       "las cantidades anteriores.")
            if st.form_submit_button("Guardar cambios"):
                guardar(actualizar_producto, sel, n_nom, n_tipo, n_uni, n_min, ok="Producto actualizado.")
        activos = q("SELECT id, nombre, codigo FROM productos WHERE activo=1 ORDER BY nombre")
        with st.expander("Dar de baja un producto (ya no se usa)"):
            o_a = dict(zip(activos.id.tolist(), (activos.nombre + " · " + activos.codigo).tolist()))
            with st.form("f_baja_prod"):
                sel_b = st.selectbox("Producto", list(o_a), format_func=o_a.get)
                ok_b = st.checkbox("Confirmo que quiero darlo de baja")
                if st.form_submit_button("Dar de baja"):
                    if not ok_b:
                        st.error("Marca la casilla de confirmación.")
                    else:
                        guardar(dar_de_baja_producto, sel_b,
                                ok="Producto dado de baja (su historial se conserva).")
        inactivos = q("SELECT id, nombre, codigo FROM productos WHERE activo=0 ORDER BY nombre")
        if not inactivos.empty:
            with st.expander("Productos dados de baja (reactivar)"):
                o_i = dict(zip(inactivos.id.tolist(), (inactivos.nombre + " · " + inactivos.codigo).tolist()))
                with st.form("f_react_prod"):
                    sel_r = st.selectbox("Producto", list(o_i), format_func=o_i.get)
                    if st.form_submit_button("Reactivar"):
                        guardar(reactivar_producto, sel_r, ok="Producto reactivado.")
        st.subheader("Lista de productos")
        st.dataframe(q("SELECT codigo, nombre, tipo, unidad, stock_minimo FROM productos "
                       "WHERE activo=1 ORDER BY tipo, nombre"), width="stretch", hide_index=True)
    with t2:
        with st.form("f_prov", clear_on_submit=True):
            n = st.text_input("Nombre del proveedor / ganadero")
            r_ = st.text_input("Ruta de acopio")
            if st.form_submit_button("Agregar proveedor"):
                def _addp():
                    if not n.strip():
                        raise ValueError("El nombre es obligatorio.")
                    con = get_conn()
                    con.execute("INSERT INTO proveedores (nombre, ruta) VALUES (?,?)", (n.strip(), r_.strip()))
                    con.commit()
                    con.close()
                guardar(_addp)

        sql_prov = """SELECT v.id, v.nombre, COALESCE(v.ruta, '') AS ruta, COUNT(r.id) AS recepciones
                      FROM proveedores v LEFT JOIN recepcion_leche r ON r.proveedor_id = v.id
                      WHERE v.activo = {activo} GROUP BY v.id ORDER BY v.nombre, v.id"""

        def etiquetas_prov(df):
            return dict(zip(df.id.tolist(), (df.nombre + " · ruta: " + df.ruta.replace("", "(sin ruta)")
                                             + " · " + df.recepciones.astype(str) + " recepciones").tolist()))

        prov = q(sql_prov.format(activo=1))
        if not prov.empty:
            with st.expander("Dar de baja a un proveedor (ya no entrega leche)"):
                st.caption("Deja de aparecer al registrar recepciones, pero su historial se conserva.")
                opts = etiquetas_prov(prov)
                with st.form("f_baja_prov"):
                    sel_b = st.selectbox("Proveedor", list(opts), format_func=opts.get)
                    ok_b = st.checkbox("Confirmo que quiero darlo de baja")
                    if st.form_submit_button("Dar de baja"):
                        if not ok_b:
                            st.error("Marca la casilla de confirmación.")
                        else:
                            guardar(dar_de_baja_proveedor, sel_b,
                                    ok="Proveedor dado de baja (su historial se conserva).")
            with st.expander("Eliminar definitivamente (solo si se registró por error)"):
                st.caption("Solo se puede eliminar si no tiene recepciones. Si ya entregó leche, usa «Dar de baja».")
                opts = etiquetas_prov(prov)
                with st.form("f_del_prov"):
                    sel = st.selectbox("Proveedor a eliminar", list(opts), format_func=opts.get)
                    ok_ = st.checkbox("Confirmo que quiero eliminarlo")
                    if st.form_submit_button("Eliminar proveedor"):
                        if not ok_:
                            st.error("Marca la casilla de confirmación.")
                        else:
                            guardar(eliminar_proveedor, sel, ok="Proveedor eliminado.")
        inactivos_p = q(sql_prov.format(activo=0))
        if not inactivos_p.empty:
            with st.expander("Proveedores dados de baja (reactivar)"):
                opts_i = etiquetas_prov(inactivos_p)
                with st.form("f_react_prov"):
                    sel_r = st.selectbox("Proveedor", list(opts_i), format_func=opts_i.get)
                    if st.form_submit_button("Reactivar"):
                        guardar(reactivar_proveedor, sel_r, ok="Proveedor reactivado.")
        prov = q(sql_prov.format(activo=1))   # se vuelve a consultar para mostrar la lista actualizada
        st.dataframe(prov.drop(columns="id"), width="stretch", hide_index=True)
    with t3:
        with st.form("f_oper", clear_on_submit=True):
            o1, o2 = st.columns(2)
            on = o1.text_input("Nombre del operador")
            oc = o2.text_input("Cargo (ej. Operador de pasteurización)")
            if st.form_submit_button("Agregar operador"):
                def _addo():
                    if not on.strip():
                        raise ValueError("El nombre es obligatorio.")
                    con = get_conn()
                    con.execute("INSERT INTO operadores (nombre, cargo) VALUES (?,?)", (on.strip(), oc.strip()))
                    con.commit()
                    con.close()
                guardar(_addo)

        sql_ops = """SELECT o.id, o.nombre, COALESCE(o.cargo, '') AS cargo,
                            (SELECT COUNT(*) FROM pasteurizacion p WHERE p.operador_id = o.id) +
                            (SELECT COUNT(*) FROM produccion pr WHERE pr.operador_id = o.id) AS registros
                     FROM operadores o WHERE o.activo = {activo} ORDER BY o.nombre, o.id"""

        def etiquetas_ops(df):
            return dict(zip(df.id.tolist(), (df.nombre + " · cargo: " + df.cargo.replace("", "(sin cargo)")
                                             + " · " + df.registros.astype(str) + " registros").tolist()))

        ops = q(sql_ops.format(activo=1))
        if not ops.empty:
            with st.expander("Dar de baja a un operador (ya no trabaja en la planta)"):
                st.caption("Deja de aparecer al registrar, pero su historial se conserva.")
                opts_o = etiquetas_ops(ops)
                with st.form("f_baja_oper"):
                    sel_bo = st.selectbox("Operador", list(opts_o), format_func=opts_o.get)
                    ok_bo = st.checkbox("Confirmo que quiero darlo de baja", key="ok_baja_oper")
                    if st.form_submit_button("Dar de baja al operador"):
                        if not ok_bo:
                            st.error("Marca la casilla de confirmación.")
                        else:
                            guardar(dar_de_baja_operador, sel_bo,
                                    ok="Operador dado de baja (su historial se conserva).")
            with st.expander("Eliminar definitivamente (solo si se registró por error)"):
                st.caption("Solo se puede eliminar si no tiene registros. Si ya trabajó en la planta, "
                           "usa «Dar de baja».")
                opts_e = etiquetas_ops(ops)
                with st.form("f_del_oper"):
                    sel_eo = st.selectbox("Operador a eliminar", list(opts_e), format_func=opts_e.get)
                    ok_eo = st.checkbox("Confirmo que quiero eliminarlo", key="ok_del_oper")
                    if st.form_submit_button("Eliminar operador"):
                        if not ok_eo:
                            st.error("Marca la casilla de confirmación.")
                        else:
                            guardar(eliminar_operador, sel_eo, ok="Operador eliminado.")
        ops_i = q(sql_ops.format(activo=0))
        if not ops_i.empty:
            with st.expander("Operadores dados de baja (reactivar)"):
                opts_i2 = etiquetas_ops(ops_i)
                with st.form("f_react_oper"):
                    sel_ro = st.selectbox("Operador", list(opts_i2), format_func=opts_i2.get)
                    if st.form_submit_button("Reactivar operador"):
                        guardar(reactivar_operador, sel_ro, ok="Operador reactivado.")
        ops = q(sql_ops.format(activo=1))   # se vuelve a consultar para mostrar la lista actualizada
        st.dataframe(ops.drop(columns="id"), width="stretch", hide_index=True)
    with t4:
        bloque_destinos()

def bloque_destinos():
    with st.form("f_dest", clear_on_submit=True):
        c1, c2 = st.columns(2)
        nombre = c1.text_input("Nombre de la tienda o destino")
        tipo = c2.selectbox("Tipo", ["Tienda", "Otro"])
        if st.form_submit_button("Agregar destino"):
            def _add():
                if not nombre.strip():
                    raise ValueError("El nombre es obligatorio.")
                transaccion(lambda con: con.execute("INSERT INTO destinos (nombre, tipo) VALUES (?,?)",
                                                    (nombre.strip(), tipo)))
            guardar(_add)

    sql = """SELECT d.id, d.nombre, d.tipo,
                    (SELECT COUNT(*) FROM pedidos p WHERE p.destino_id = d.id) +
                    (SELECT COUNT(*) FROM devoluciones v WHERE v.destino_id = d.id) +
                    (SELECT COUNT(*) FROM trazabilidad t WHERE t.destino_id = d.id) AS registros
             FROM destinos d WHERE d.activo = {activo} ORDER BY d.nombre"""

    def etiquetas(df):
        return dict(zip(df.id.tolist(), (df.nombre + " · " + df.tipo + " · " +
                                         df.registros.astype(str) + " registros").tolist()))

    activos = q(sql.format(activo=1))
    if not activos.empty:
        with st.expander("Dar de baja un destino (ya no se usa)"):
            st.caption("Deja de aparecer al registrar, pero su historial se conserva.")
            opts = etiquetas(activos)
            with st.form("f_baja_dest"):
                sel_b = st.selectbox("Destino", list(opts), format_func=opts.get)
                ok_b = st.checkbox("Confirmo que quiero darlo de baja", key="ok_baja_dest")
                if st.form_submit_button("Dar de baja el destino"):
                    if not ok_b:
                        st.error("Marca la casilla de confirmación.")
                    else:
                        guardar(dar_de_baja_destino, sel_b, ok="Destino dado de baja (su historial se conserva).")
        with st.expander("Eliminar definitivamente (solo si se registró por error)"):
            st.caption("Solo se puede eliminar si no tiene registros.")
            opts = etiquetas(activos)
            with st.form("f_del_dest"):
                sel_e = st.selectbox("Destino a eliminar", list(opts), format_func=opts.get)
                ok_e = st.checkbox("Confirmo que quiero eliminarlo", key="ok_del_dest")
                if st.form_submit_button("Eliminar destino"):
                    if not ok_e:
                        st.error("Marca la casilla de confirmación.")
                    else:
                        guardar(eliminar_destino, sel_e, ok="Destino eliminado.")
    inactivos = q(sql.format(activo=0))
    if not inactivos.empty:
        with st.expander("Destinos dados de baja (reactivar)"):
            opts = etiquetas(inactivos)
            with st.form("f_react_dest"):
                sel_r = st.selectbox("Destino", list(opts), format_func=opts.get)
                if st.form_submit_button("Reactivar destino"):
                    guardar(reactivar_destino, sel_r, ok="Destino reactivado.")
    st.dataframe(q(sql.format(activo=1)).drop(columns="id"), width="stretch", hide_index=True)


def form_anular(key, vigentes, fn):
    """Formulario genérico para anular un registro (corregir un error) sin borrar el historial."""
    if vigentes.empty:
        return
    with st.expander("Anular un registro (corregir un error)"):
        st.caption("El registro no se borra: queda marcado como ANULADO con su motivo y, si movió "
                   "inventario, el Kardex se corrige solo. Luego vuelve a registrarlo bien.")
        opts = dict(zip(vigentes.id.tolist(), vigentes.etiqueta.tolist()))
        with st.form(f"f_anular_{key}"):
            sel = st.selectbox("Registro a anular", list(opts), format_func=opts.get)
            motivo = st.text_input("Motivo (obligatorio)")
            ok_ = st.checkbox("Confirmo que quiero anularlo")
            if st.form_submit_button("Anular registro"):
                if not motivo.strip():
                    st.error("Escribe el motivo de la anulación.")
                elif not ok_:
                    st.error("Marca la casilla de confirmación.")
                else:
                    guardar(fn, sel, motivo.strip(), ok="Registro anulado.")


def pag_recepcion():
    st.header("Llegada y análisis de leche")
    with st.form("f_rec", clear_on_submit=True):
        prov = pick_proveedor()
        c1, c2, c3 = st.columns(3)
        f = c1.date_input("Fecha", date.today())
        hora = c2.time_input("Hora de llegada", value=None)
        proced = c3.text_input("Lugar de procedencia")
        c4, c5, c6 = st.columns(3)
        litros = c4.number_input("Litros recibidos", min_value=0.0, step=10.0)
        tanques = c5.number_input("N.º de tanques", min_value=0, step=1)
        porongos = c6.number_input("N.º de porongos", min_value=0, step=1)
        st.markdown("**Análisis de la materia prima**")
        a1, a2, a3, a4 = st.columns(4)
        grasa = a1.number_input("Grasa (%)", min_value=0.0, step=0.1)
        acidez = a2.number_input("Acidez (°D)", min_value=0.0, step=0.5)
        dens = a3.number_input("Densidad", min_value=0.0, step=0.001, format="%.3f")
        temp = a4.number_input("Temp. (°C)", min_value=0.0, step=0.5)
        b1, b2, b3, b4 = st.columns(4)
        ph = b1.number_input("pH", min_value=0.0, step=0.1)
        lactosa = b2.number_input("Lactosa (%)", min_value=0.0, step=0.1)
        proteina = b3.number_input("Proteína (%)", min_value=0.0, step=0.1)
        sng = b4.number_input("Sólidos no grasos (%)", min_value=0.0, step=0.1)
        d1, d2, d3, d4 = st.columns(4)
        mastitis = d1.selectbox("Detección de mastitis", RESULTADO_MASTITIS)
        antib = d2.selectbox("Residuos de antibióticos", RESULTADO_ANTIBIOTICOS)
        agua = d3.number_input("Agua detectada (L)", min_value=0.0, step=1.0)
        aceptada = d4.number_input("Total de leche aceptada (L)", min_value=0.0, step=10.0)
        st.caption("Leche aceptada: déjala en 0 para que se calcule sola (litros recibidos − agua). "
                   "Solo la leche aceptada entra al inventario.")
        aprobada = st.checkbox("Aprobada por control de calidad", value=True)
        resp = pick_operador("Responsable del análisis (opcional)", obligatorio=False)
        if st.form_submit_button("Registrar recepción"):
            if prov is None:
                st.error("Falta el proveedor: agrégalo primero en Catálogos → Proveedores.")
            else:
                extra = dict(hora=hora.strftime("%H:%M") if hora else None, n_tanques=int(tanques),
                             n_porongos=int(porongos), procedencia=proced.strip() or None,
                             ph=ph or None, lactosa=lactosa or None, proteina=proteina or None,
                             sng=sng or None, mastitis=mastitis, antibioticos=antib, agua_l=agua,
                             aceptada=aceptada or None, responsable_id=resp)
                registrada = guardar(lambda *a: registrar_recepcion(*a, **extra),
                                     f, prov, litros, grasa, acidez, dens, temp, aprobada)
                if registrada and mastitis == "Positivo" and aprobada:
                    st.warning("Ojo: la leche tiene mastitis positiva y quedó aprobada. "
                               "Confírmalo con control de calidad.")
    form_anular("rec", q("""SELECT r.id, 'N°' || r.id || ' · ' || r.fecha || ' · ' ||
                                   COALESCE(v.nombre, 's/proveedor') || ' · ' || r.litros || ' L' AS etiqueta
                            FROM recepcion_leche r LEFT JOIN proveedores v ON v.id = r.proveedor_id
                            WHERE r.anulado = 0 ORDER BY r.id DESC LIMIT 100"""), anular_recepcion)
    st.dataframe(q("""SELECT r.id AS n, r.fecha, r.hora, v.nombre AS proveedor, r.procedencia, r.litros,
                      r.n_tanques AS tanques, r.n_porongos AS porongos, r.grasa, r.acidez, r.densidad,
                      r.temperatura, r.ph, r.lactosa, r.proteina, r.sng, r.mastitis,
                      r.antibioticos, r.agua_l, r.litros_aceptados AS aceptada_L,
                      CASE r.aprobada WHEN 1 THEN 'Sí' ELSE 'No' END AS aprobada,
                      o.nombre AS responsable,
                      CASE r.anulado WHEN 1 THEN 'ANULADO' ELSE 'Vigente' END AS estado,
                      r.motivo_anulacion
                      FROM recepcion_leche r LEFT JOIN proveedores v ON v.id = r.proveedor_id
                      LEFT JOIN operadores o ON o.id = r.responsable_id
                      ORDER BY r.fecha DESC, r.id DESC"""),
                 width="stretch", hide_index=True)


def pag_pasteurizacion():
    st.header("Pasteurización")
    con = get_conn()
    t_min = float(get_meta(con, "past_temp_min", 63))
    m_min = float(get_meta(con, "past_tiempo_min", 30))
    con.close()

    with st.expander(f"Parámetros de conformidad (actual: ≥ {t_min:g} °C durante ≥ {m_min:g} min)"):
        with st.form("f_past_par"):
            p1, p2 = st.columns(2)
            nt = p1.number_input("Temperatura mínima (°C)", min_value=0.0, value=t_min, step=0.5)
            nm = p2.number_input("Tiempo mínimo de retención (min)", min_value=0.0, value=m_min, step=1.0)
            if st.form_submit_button("Guardar parámetros"):
                def _par():
                    c = get_conn()
                    set_meta(c, "past_temp_min", nt)
                    set_meta(c, "past_tiempo_min", nm)
                    c.commit()
                    c.close()
                guardar(_par, ok="Parámetros actualizados.")

    st.subheader("Registrar pasteurización")
    with st.form("f_past", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        f = c1.date_input("Fecha", date.today())
        lote = c2.text_input("Lote / tanque")
        hora = c3.time_input("Hora de inicio")
        c4, c5, c6 = st.columns(3)
        litros = c4.number_input("Litros pasteurizados", min_value=0.0, step=10.0)
        temp = c5.number_input("Temperatura alcanzada (°C)", min_value=0.0, step=0.5)
        tiempo = c6.number_input("Tiempo de retención (min)", min_value=0.0, step=1.0)
        oper = pick_operador("Operador responsable", obligatorio=True)
        prod_p = pick_producto_opcional("Producto que se elabora (opcional)", ["Producto terminado"])
        obs = st.text_input("Observaciones (opcional)")
        if st.form_submit_button("Registrar pasteurización"):
            if not lote.strip():
                st.error("El lote es obligatorio.")
            else:
                try:
                    ok = registrar_pasteurizacion(f, lote.strip(), litros, temp, tiempo,
                                                  hora.strftime("%H:%M"), oper, obs.strip() or None, prod_p)
                    if ok:
                        st.success("Registrado: pasteurización CONFORME.")
                    else:
                        st.warning("Registrado, pero NO CONFORME: no alcanzó la temperatura o el "
                                   "tiempo mínimos. Avisa al encargado de planta.")
                except (ValueError, sqlite3.Error) as e:
                    st.error(str(e))
    form_anular("past", q("""SELECT ps.id, 'N°' || ps.id || ' · ' || ps.fecha || ' · ' || ps.lote || ' · ' ||
                                    ps.litros || ' L' AS etiqueta
                             FROM pasteurizacion ps WHERE ps.anulado = 0
                             ORDER BY ps.id DESC LIMIT 100"""), anular_pasteurizacion)
    df = q("""SELECT ps.id AS n, ps.fecha, ps.hora_inicio, ps.lote, pp.nombre AS producto, ps.litros,
                     ps.temperatura,
                     ps.tiempo_min AS "tiempo_min", o.nombre AS operador,
                     CASE ps.conforme WHEN 1 THEN 'Conforme' ELSE 'NO conforme' END AS resultado,
                     CASE ps.anulado WHEN 1 THEN 'ANULADO' ELSE 'Vigente' END AS estado,
                     ps.motivo_anulacion, ps.observacion
              FROM pasteurizacion ps JOIN operadores o ON o.id = ps.operador_id
              LEFT JOIN productos pp ON pp.id = ps.producto_id
              ORDER BY ps.fecha DESC, ps.id DESC""")
    st.dataframe(df, width="stretch", hide_index=True)


def pag_produccion():
    st.header("Producción y mermas")
    with st.form("f_prod_reg", clear_on_submit=True):
        c1, c2 = st.columns(2)
        f = c1.date_input("Fecha", date.today())
        lote = c2.text_input("Lote")
        pid = pick_producto("Producto elaborado", ["Producto terminado"])
        oper = pick_operador("Operador (opcional)", obligatorio=False)
        c3, c4 = st.columns(2)
        litros = c3.number_input("Litros de leche usados (0 si el producto no lleva leche)",
                                 min_value=0.0, step=10.0)
        obtenida = c4.number_input("Cantidad obtenida (kg o L)", min_value=0.0, step=1.0)
        st.caption("Suero: recibido → vendido / usado → la diferencia es la merma.")
        s1, s2, s3 = st.columns(3)
        suero = s1.number_input("Suero obtenido (L)", min_value=0.0, step=10.0)
        vendido = s2.number_input("Suero vendido (L)", min_value=0.0, step=10.0)
        usado = s3.number_input("Suero usado en planta (L)", min_value=0.0, step=10.0)
        insumos = []
        with st.expander("Insumos usados en este lote (opcional)"):
            st.caption("Se guardan como dato del lote (cantidad, marca, lote y fechas). El almacén de "
                       "insumos no forma parte del ERP, por eso no descuentan stock.")
            anchos = [2, 1, 1.5, 1.5, 1.2, 1.2]
            for col, t in zip(st.columns(anchos), ["Insumo", "Cantidad (g)", "Marca", "Lote", "F.P.", "F.V."]):
                col.caption(t)
            for nombre in INSUMOS_PROCESO:
                k0, k1, k2, k3, k4, k5 = st.columns(anchos)
                k0.write(nombre)
                insumos.append({
                    "insumo": nombre,
                    "cantidad_g": k1.number_input(f"{nombre} cantidad", min_value=0.0, step=1.0,
                                                  key=f"ins_c_{nombre}", label_visibility="collapsed"),
                    "marca": k2.text_input(f"{nombre} marca", key=f"ins_m_{nombre}",
                                           label_visibility="collapsed"),
                    "lote": k3.text_input(f"{nombre} lote", key=f"ins_l_{nombre}",
                                          label_visibility="collapsed"),
                    "fecha_prod": k4.text_input(f"{nombre} F.P.", key=f"ins_p_{nombre}",
                                                label_visibility="collapsed", placeholder="mm/aa"),
                    "fecha_venc": k5.text_input(f"{nombre} F.V.", key=f"ins_v_{nombre}",
                                                label_visibility="collapsed", placeholder="mm/aa"),
                })
        if st.form_submit_button("Registrar producción"):
            if not lote.strip():
                st.error("El lote es obligatorio.")
            else:
                guardar(lambda *a: registrar_produccion(*a, insumos=insumos), f, lote.strip(), pid, litros,
                        obtenida, suero, vendido, usado, oper)
    form_anular("prod", q("""SELECT pr.id, 'N°' || pr.id || ' · ' || pr.fecha || ' · lote ' || pr.lote || ' · ' ||
                                    p.nombre || ' · ' || pr.cantidad_obtenida AS etiqueta
                             FROM produccion pr JOIN productos p ON p.id = pr.producto_id
                             WHERE pr.anulado = 0 ORDER BY pr.id DESC LIMIT 100"""), anular_produccion)
    df = q("""SELECT pr.id AS n, pr.fecha, pr.lote, p.nombre AS producto, o.nombre AS operador,
                     pr.litros_leche, pr.cantidad_obtenida,
                     ROUND(pr.cantidad_obtenida * 100.0 / pr.litros_leche, 2) AS "recuperación_%",
                     pr.suero_obtenido, pr.suero_vendido, pr.suero_usado,
                     pr.suero_obtenido - pr.suero_vendido - pr.suero_usado AS merma_suero_L,
                     CASE pr.anulado WHEN 1 THEN 'ANULADO' ELSE 'Vigente' END AS estado,
                     pr.motivo_anulacion
              FROM produccion pr JOIN productos p ON p.id = pr.producto_id
              LEFT JOIN operadores o ON o.id = pr.operador_id
              ORDER BY pr.fecha DESC, pr.id DESC""")
    st.dataframe(df, width="stretch", hide_index=True)
    with st.expander("Insumos usados por lote"):
        st.dataframe(q("""SELECT pr.fecha, pr.lote, p.nombre AS producto, i.insumo, i.cantidad_g,
                                 i.marca, i.lote AS lote_insumo, i.fecha_prod AS F_P, i.fecha_venc AS F_V
                          FROM produccion_insumos i JOIN produccion pr ON pr.id = i.produccion_id
                          JOIN productos p ON p.id = pr.producto_id
                          WHERE pr.anulado = 0 ORDER BY pr.fecha DESC, pr.id DESC, i.id"""),
                     width="stretch", hide_index=True)


def pag_despachos():
    st.header("Pedidos y despachos")
    with st.form("f_desp", clear_on_submit=True):
        c1, c2 = st.columns(2)
        f = c1.date_input("Fecha", date.today())
        destino = pick_destino("Tienda de destino")
        cliente = c2.text_input("Cliente (si no es una tienda)")
        pid = pick_producto("Producto", ["Producto terminado"])
        c3, c4, c5 = st.columns(3)
        pedida = c3.number_input("Cantidad pedida", min_value=0.0, step=1.0)
        desp = c4.number_input("Cantidad despachada", min_value=0.0, step=1.0)
        doc = c5.text_input("N° de guía")
        resp = pick_operador("Responsable del envío (opcional)", obligatorio=False)
        if st.form_submit_button("Registrar despacho"):
            if pedida <= 0:
                st.error("La cantidad pedida es obligatoria.")
            else:
                guardar(registrar_despacho, f, cliente.strip(), pid, pedida, desp, doc.strip() or None,
                        destino, resp)
    form_anular("desp", q("""SELECT d.id, 'N°' || d.id || ' · ' || d.fecha || ' · ' || d.cliente || ' · ' ||
                                    p.nombre || ' · ' || d.cant_despachada AS etiqueta
                             FROM pedidos d JOIN productos p ON p.id = d.producto_id
                             WHERE d.anulado = 0 ORDER BY d.id DESC LIMIT 100"""), anular_despacho)
    st.dataframe(q("""SELECT d.id AS n, d.fecha, d.cliente, p.nombre AS producto, d.cant_pedida,
                      d.cant_despachada, ROUND(d.cant_despachada * 100.0 / d.cant_pedida, 1) AS "fill_rate_%",
                      d.documento, o.nombre AS responsable_envio,
                      CASE d.anulado WHEN 1 THEN 'ANULADO' ELSE 'Vigente' END AS estado,
                      d.motivo_anulacion
                      FROM pedidos d JOIN productos p ON p.id = d.producto_id
                      LEFT JOIN operadores o ON o.id = d.responsable_envio_id
                      ORDER BY d.fecha DESC, d.id DESC"""),
                 width="stretch", hide_index=True)


def pag_devoluciones():
    st.header("Pedidos devueltos")
    desp = q("""SELECT d.id, 'N°' || d.id || ' · ' || d.fecha || ' · ' || d.cliente || ' · ' || p.nombre ||
                       ' · despachado ' || d.cant_despachada AS etiqueta
                FROM pedidos d JOIN productos p ON p.id = d.producto_id
                WHERE d.anulado = 0 AND d.cant_despachada > 0 ORDER BY d.id DESC LIMIT 100""")
    opts_d = dict(zip(desp.id.tolist(), desp.etiqueta.tolist()))
    with st.form("f_dev", clear_on_submit=True):
        origen = st.selectbox("Despacho de origen (opcional)", [None] + list(opts_d),
                              format_func=lambda i: opts_d.get(i, "(sin despacho de origen)"))
        st.caption("Si eliges un despacho, se usan su tienda o cliente y su producto, y no se puede devolver "
                   "más de lo que se despachó. Si no, indica la tienda o el cliente y el producto.")
        c1, c2 = st.columns(2)
        f = c1.date_input("Fecha de devolución", date.today())
        with c2:
            destino = pick_destino("Tienda que devuelve")
        cliente = st.text_input("Cliente (si no es una tienda ni elegiste despacho)")
        pid = pick_producto("Producto (si no elegiste despacho)", ["Producto terminado"])
        c3, c4 = st.columns(2)
        cant = c3.number_input("Cantidad devuelta", min_value=0.0, step=1.0)
        doc = c4.text_input("N° de guía o documento")
        c5, c6 = st.columns(2)
        motivo = c5.selectbox("Motivo de la devolución", MOTIVOS_DEVOLUCION)
        destino_prod = c6.selectbox("¿Qué pasa con el producto?", [DESTINO_REINGRESO, DESTINO_MERMA])
        st.caption("«Reingresa al almacén» suma la cantidad al stock. «Merma» queda registrada, "
                   "pero no vuelve al stock.")
        resp = pick_operador("Encargado / responsable (opcional)", obligatorio=False)
        obs = st.text_input("Observaciones (opcional)")
        if st.form_submit_button("Registrar devolución"):
            guardar(registrar_devolucion, f, cliente.strip(), pid, cant, motivo, destino_prod, origen,
                    doc.strip() or None, obs.strip() or None, destino, resp)
    form_anular("dev", q("""SELECT dv.id, 'N°' || dv.id || ' · ' || dv.fecha || ' · ' || dv.cliente || ' · ' ||
                                   p.nombre || ' · ' || dv.cantidad AS etiqueta
                            FROM devoluciones dv JOIN productos p ON p.id = dv.producto_id
                            WHERE dv.anulado = 0 ORDER BY dv.id DESC LIMIT 100"""), anular_devolucion)
    st.dataframe(q("""SELECT dv.id AS n, dv.fecha, dv.cliente, p.nombre AS producto, dv.cantidad,
                      dv.motivo, dv.destino, dv.despacho_id AS despacho_n, dv.documento,
                      o.nombre AS responsable, dv.observacion,
                      CASE dv.anulado WHEN 1 THEN 'ANULADO' ELSE 'Vigente' END AS estado,
                      dv.motivo_anulacion
                      FROM devoluciones dv JOIN productos p ON p.id = dv.producto_id
                      LEFT JOIN operadores o ON o.id = dv.responsable_id
                      ORDER BY dv.fecha DESC, dv.id DESC"""),
                 width="stretch", hide_index=True)


def pag_programacion():
    st.header("Programación diaria")
    with st.form("f_prog", clear_on_submit=True):
        c1, c2 = st.columns(2)
        f = c1.date_input("Fecha programada", date.today())
        litros = c2.number_input("Litros de leche programados", min_value=0.0, step=10.0)
        pid = pick_producto("Producto a elaborar", ["Producto terminado"])
        oper = pick_operador("Responsable (opcional)", obligatorio=False)
        obs = st.text_input("Observaciones (opcional)")
        if st.form_submit_button("Registrar programación"):
            guardar(registrar_programacion, f, pid, litros, oper, obs.strip() or None)
    form_anular("prog", q("""SELECT g.id, 'N°' || g.id || ' · ' || g.fecha || ' · ' || p.nombre || ' · ' ||
                                    g.litros_programados || ' L' AS etiqueta
                             FROM programacion g JOIN productos p ON p.id = g.producto_id
                             WHERE g.anulado = 0 ORDER BY g.id DESC LIMIT 100"""), anular_programacion)
    st.subheader("Programado vs producido")
    st.caption("«Litros usados» son los litros de leche de las producciones registradas ese día para ese producto.")
    st.dataframe(q("""SELECT g.id AS n, g.fecha, p.nombre AS producto, o.nombre AS responsable,
                      g.litros_programados, COALESCE(u.usados, 0) AS litros_usados,
                      ROUND(COALESCE(u.usados, 0) * 100.0 / g.litros_programados, 1) AS "cumplimiento_%",
                      g.observacion, CASE g.anulado WHEN 1 THEN 'ANULADO' ELSE 'Vigente' END AS estado,
                      g.motivo_anulacion
                      FROM programacion g JOIN productos p ON p.id = g.producto_id
                      LEFT JOIN operadores o ON o.id = g.responsable_id
                      LEFT JOIN (SELECT fecha, producto_id, SUM(litros_leche) AS usados FROM produccion
                                 WHERE anulado = 0 GROUP BY fecha, producto_id) u
                             ON u.fecha = g.fecha AND u.producto_id = g.producto_id
                      ORDER BY g.fecha DESC, g.id DESC"""),
                 width="stretch", hide_index=True)


def pag_trazabilidad():
    st.header("Trazabilidad de producto terminado")
    st.caption("Cada producción abre su registro solo (lote, producto, responsable y fecha de producción). "
               "Aquí completas las etapas a medida que ocurren.")
    vig = q("""SELECT t.id, 'N°' || t.id || ' · lote ' || COALESCE(t.lote, '-') || ' · ' || p.nombre ||
                      ' · producido ' || t.fecha_produccion AS etiqueta
               FROM trazabilidad t JOIN productos p ON p.id = t.producto_id
               WHERE t.anulado = 0 ORDER BY t.id DESC LIMIT 200""")
    if vig.empty:
        st.info("Aún no hay lotes: se crean automáticamente al registrar una producción.")
    else:
        with st.expander("Registrar etapa de un lote (empaque, envasado, maduración, salida)", expanded=True):
            opts = dict(zip(vig.id.tolist(), vig.etiqueta.tolist()))
            sel = st.selectbox("Lote", list(opts), format_func=opts.get)
            with st.form("f_traz", clear_on_submit=True):
                t1, t2 = st.columns(2)
                d_emp = t1.date_input("Ingreso al área de empaque", value=None)
                d_env = t2.date_input("Envasado", value=None)
                t3, t4 = st.columns(2)
                d_mad = t3.date_input("Ingreso a la cámara de maduración", value=None)
                d_sal = t4.date_input("Salida del producto", value=None)
                destino = pick_destino("Destino")
                obs = st.text_input("Observaciones (opcional)")
                if st.form_submit_button("Guardar etapas"):
                    guardar(actualizar_trazabilidad, sel, d_emp, d_env, d_mad, d_sal, destino,
                            obs.strip() or None, ok="Etapas guardadas.")
    solo_pend = st.checkbox("Solo lotes que aún no salieron")
    filtro = "WHERE t.anulado = 0 AND t.fecha_salida IS NULL" if solo_pend else ""
    st.dataframe(q(f"""SELECT t.id AS n, t.lote, p.nombre AS producto, o.nombre AS responsable,
                       t.fecha_produccion, t.fecha_empaque, t.fecha_envasado, t.fecha_maduracion,
                       t.fecha_salida, d.nombre AS destino, t.observacion,
                       CASE t.anulado WHEN 1 THEN 'ANULADO' ELSE 'Vigente' END AS estado
                       FROM trazabilidad t JOIN productos p ON p.id = t.producto_id
                       LEFT JOIN operadores o ON o.id = t.responsable_id
                       LEFT JOIN destinos d ON d.id = t.destino_id
                       {filtro} ORDER BY t.fecha_produccion DESC, t.id DESC"""),
                 width="stretch", hide_index=True)


def pag_almacen_pt():
    st.header("Almacén de producto terminado")
    c1, c2, c3 = st.columns(3)
    desde = c1.date_input("Desde", date(date.today().year, 1, 1), key="alm_desde")
    hasta = c2.date_input("Hasta", date.today(), key="alm_hasta")
    solo_stock = c3.checkbox("Solo productos con stock")
    d, h = str(desde), str(hasta)

    base = stock_actual()
    base = base[base.tipo == "Producto terminado"][["id", "nombre", "unidad", "stock", "stock_minimo"]].copy()
    flujos = {
        "producido": ("SELECT producto_id, SUM(cantidad_obtenida) AS total FROM produccion "
                      "WHERE anulado=0 AND fecha BETWEEN ? AND ? GROUP BY producto_id", (d, h)),
        "despachado": ("SELECT producto_id, SUM(cant_despachada) AS total FROM pedidos "
                       "WHERE anulado=0 AND fecha BETWEEN ? AND ? GROUP BY producto_id", (d, h)),
        "devuelto_reingresado": ("SELECT producto_id, SUM(cantidad) AS total FROM devoluciones "
                                 "WHERE anulado=0 AND destino=? AND fecha BETWEEN ? AND ? "
                                 "GROUP BY producto_id", (DESTINO_REINGRESO, d, h)),
        "devuelto_merma": ("SELECT producto_id, SUM(cantidad) AS total FROM devoluciones "
                           "WHERE anulado=0 AND destino=? AND fecha BETWEEN ? AND ? "
                           "GROUP BY producto_id", (DESTINO_MERMA, d, h)),
    }
    for col, (sql, params) in flujos.items():
        serie = q(sql, params).set_index("producto_id")["total"]
        base[col] = base["id"].map(serie).fillna(0.0)
    ultimo = q("SELECT producto_id, MAX(fecha) AS ultimo FROM movimientos GROUP BY producto_id")
    base["ultimo_movimiento"] = base["id"].map(ultimo.set_index("producto_id")["ultimo"])

    def estado(r):
        if r.stock <= 0:
            return "Sin stock"
        if r.stock_minimo > 0 and r.stock < r.stock_minimo:
            return "⚠️ Bajo mínimo"
        return "OK"

    base["estado"] = base.apply(estado, axis=1)
    m1, m2, m3 = st.columns(3)
    m1.metric("Productos con stock", int((base.stock > 0).sum()))
    m2.metric("Productos sin stock", int((base.stock <= 0).sum()))
    m3.metric("Bajo stock mínimo", int((base.estado == "⚠️ Bajo mínimo").sum()))
    if solo_stock:
        base = base[base.stock > 0]
    st.caption("Producido, despachado y devuelto corresponden al periodo elegido; el stock es el actual. "
               "El detalle de cada movimiento está en el Kardex.")
    st.dataframe(base[["nombre", "unidad", "stock", "estado", "producido", "despachado",
                       "devuelto_reingresado", "devuelto_merma", "stock_minimo", "ultimo_movimiento"]],
                 width="stretch", hide_index=True)


def pag_kardex():
    st.header("Kardex")
    st.caption("El Kardex no se edita a mano: refleja todos los movimientos. Si hubo un error, anula el "
               "registro de origen (recepción, producción, despacho o conteo) y aquí verás el "
               "movimiento que lo corrige.")
    nombres = ["(Todos)"] + q("SELECT nombre FROM productos ORDER BY nombre").nombre.tolist()
    sel = st.selectbox("Producto", nombres)
    df = kardex(None if sel == "(Todos)" else sel)
    st.dataframe(df, width="stretch", hide_index=True)
    if not df.empty:
        st.download_button("Descargar Kardex (CSV)", df.to_csv(index=False).encode("utf-8-sig"),
                           "kardex.csv", "text/csv")


def pag_conteo():
    st.header("Inventario físico")
    with st.form("f_conteo", clear_on_submit=True):
        f = st.date_input("Fecha del conteo", date.today())
        pid = pick_producto("Producto contado")
        fisico = st.number_input("Stock físico contado", min_value=0.0, step=1.0)
        ajustar = st.checkbox("Ajustar el Kardex con la diferencia")
        if st.form_submit_button("Registrar conteo"):
            guardar(registrar_conteo, f, pid, fisico, ajustar)
    form_anular("conteo", q("""SELECT c.id, 'N°' || c.id || ' · ' || c.fecha || ' · ' || p.nombre || ' · físico ' ||
                                      c.stock_fisico AS etiqueta
                               FROM conteos c JOIN productos p ON p.id = c.producto_id
                               WHERE c.anulado = 0 ORDER BY c.id DESC LIMIT 100"""), anular_conteo)
    st.dataframe(q("""SELECT c.id AS n, c.fecha, p.nombre AS producto, c.stock_sistema, c.stock_fisico,
                      c.diferencia, CASE c.ajustado WHEN 1 THEN 'Sí' ELSE 'No' END AS ajustó_kardex,
                      CASE c.anulado WHEN 1 THEN 'ANULADO' ELSE 'Vigente' END AS estado,
                      c.motivo_anulacion
                      FROM conteos c JOIN productos p ON p.id = c.producto_id
                      ORDER BY c.fecha DESC, c.id DESC"""),
                 width="stretch", hide_index=True)


def fmt(v, meta=None):
    return "s/d" if v is None else f"{v:.1f}%"


def pag_indicadores():
    st.header("Indicadores de gestión de inventarios")
    c1, c2, c3 = st.columns(3)
    desde = c1.date_input("Desde", date(date.today().year, 1, 1))
    hasta = c2.date_input("Hasta", date.today())
    docs = c3.number_input("Documentos físicos del periodo (guías, notas)", min_value=0, step=1)
    r = indicadores(desde, hasta, docs)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Control de existencias (meta ≥100%)", fmt(r["control_existencias"]))
    m2.metric("Exactitud del inventario (meta ≥95%)", fmt(r["exactitud"]))
    m3.metric("Fill rate (meta ≥80%)", fmt(r["fill_rate"]))
    m4.metric("Recuperación de leche", fmt(r["recuperacion_global"]))
    n1, n2, n3 = st.columns(3)
    n3.metric("Tasa de devoluciones", fmt(r["tasa_devoluciones"]))
    if r["suero_merma_l"] is not None:
        n1.metric("Merma de suero (L)", f"{r['suero_merma_l']:.1f}  ({fmt(r['suero_merma_%'])})")
    n2.metric(f"Pasteurizaciones conformes ({r['pasteurizaciones']} en el periodo)",
              fmt(r["pasteurizacion_conforme"]))
    if len(r["detalle_produccion"]):
        st.subheader("Recuperación por producto")
        st.dataframe(r["detalle_produccion"], width="stretch", hide_index=True)


# ─────────────────────────── Resumen por semana / mes / año ───────────────────────────
def _suma_por_unidad(tabla, campo, d, h):
    df = q(f"""SELECT p.unidad, SUM(t.{campo}) AS total FROM {tabla} t
               JOIN productos p ON p.id = t.producto_id
               WHERE t.anulado = 0 AND t.fecha BETWEEN ? AND ? GROUP BY p.unidad""", (d, h))
    return {r.unidad: float(r.total) for r in df.itertuples()}


def resumen_periodo(desde, hasta):
    """Todas las cifras de un periodo (los registros anulados no cuentan)."""
    d, h = str(desde), str(hasta)
    ind = indicadores(desde, hasta, 0)
    leche = q("SELECT COALESCE(SUM(litros), 0) AS v FROM recepcion_leche "
              "WHERE anulado=0 AND fecha BETWEEN ? AND ?", (d, h)).v[0]
    aceptada = q("SELECT COALESCE(SUM(COALESCE(litros_aceptados, litros)), 0) AS v FROM recepcion_leche "
                 "WHERE anulado=0 AND aprobada=1 AND fecha BETWEEN ? AND ?", (d, h)).v[0]
    usada = q("SELECT COALESCE(SUM(litros_leche), 0) AS v FROM produccion "
              "WHERE anulado=0 AND fecha BETWEEN ? AND ?", (d, h)).v[0]
    return {
        "leche_recibida": float(leche), "leche_aceptada": float(aceptada), "leche_usada": float(usada),
        "producido": _suma_por_unidad("produccion", "cantidad_obtenida", d, h),
        "despachado": _suma_por_unidad("pedidos", "cant_despachada", d, h),
        "devuelto": _suma_por_unidad("devoluciones", "cantidad", d, h),
        "fill_rate": ind["fill_rate"], "recuperacion": ind["recuperacion_global"],
        "merma_suero_l": ind["suero_merma_l"], "merma_suero_pct": ind["suero_merma_%"],
        "exactitud": ind["exactitud"], "past_conforme": ind["pasteurizacion_conforme"],
        "pasteurizaciones": ind["pasteurizaciones"], "tasa_devoluciones": ind["tasa_devoluciones"],
    }


def fila_resumen(etiqueta, r):
    def redondear(v):
        return None if v is None or pd.isna(v) else round(float(v), 1)
    return {
        "periodo": etiqueta,
        "leche_recibida_L": round(r["leche_recibida"], 1), "leche_aceptada_L": round(r["leche_aceptada"], 1),
        "leche_usada_L": round(r["leche_usada"], 1),
        "producción_kg": r["producido"].get("kg", 0.0), "producción_L": r["producido"].get("L", 0.0),
        "despachado_kg": r["despachado"].get("kg", 0.0), "despachado_L": r["despachado"].get("L", 0.0),
        "devuelto_kg": r["devuelto"].get("kg", 0.0), "devuelto_L": r["devuelto"].get("L", 0.0),
        "fill_rate_%": redondear(r["fill_rate"]), "recuperación_%": redondear(r["recuperacion"]),
        "merma_suero_L": redondear(r["merma_suero_l"]), "exactitud_%": redondear(r["exactitud"]),
        "pasteurización_conforme_%": redondear(r["past_conforme"]),
    }


def tabla_resumen(filas):
    df = pd.DataFrame(filas)
    for col in ("producción_L", "despachado_L", "devuelto_L"):  # se oculta si no hay productos en litros
        if col in df and (df[col] == 0).all():
            df = df.drop(columns=col)
    return df


def semanas_del_mes(anio, mes):
    """Semanas de lunes a domingo, recortadas al mes."""
    ultimo = date(anio, mes, monthrange(anio, mes)[1])
    ini, n, out = date(anio, mes, 1), 1, []
    while ini <= ultimo:
        fin = min(ini + timedelta(days=6 - ini.weekday()), ultimo)
        out.append((f"Semana {n} ({ini:%d/%m} – {fin:%d/%m})", ini, fin))
        ini, n = fin + timedelta(days=1), n + 1
    return out


def tabla_por_producto(d, h):
    def serie(tabla, campo):
        return q(f"""SELECT p.nombre, SUM(t.{campo}) AS total FROM {tabla} t
                     JOIN productos p ON p.id = t.producto_id
                     WHERE t.anulado = 0 AND t.fecha BETWEEN ? AND ? GROUP BY p.nombre""",
                 (d, h)).set_index("nombre")["total"]
    df = pd.concat({"producido": serie("produccion", "cantidad_obtenida"),
                    "despachado": serie("pedidos", "cant_despachada"),
                    "devuelto": serie("devoluciones", "cantidad")}, axis=1).fillna(0.0)
    df.index.name = "producto"
    return df.reset_index()


def texto_unidades(dic):
    partes = [f"{v:,.1f} {u}" for u, v in dic.items() if v]
    return " · ".join(partes) if partes else "0"


def tarjetas_resumen(r):
    a1, a2, a3 = st.columns(3)
    a1.metric("Leche recibida", f"{r['leche_recibida']:,.1f} L")
    a2.metric("Leche aceptada", f"{r['leche_aceptada']:,.1f} L")
    a3.metric("Leche usada en producción", f"{r['leche_usada']:,.1f} L")
    b1, b2, b3 = st.columns(3)
    b1.metric("Producción", texto_unidades(r["producido"]))
    b2.metric("Despachado", texto_unidades(r["despachado"]))
    b3.metric("Devuelto", texto_unidades(r["devuelto"]))
    c1, c2, c3 = st.columns(3)
    c1.metric("Fill rate (meta ≥80%)", fmt(r["fill_rate"]))
    c2.metric("Recuperación de leche", fmt(r["recuperacion"]))
    merma = r["merma_suero_l"]
    c3.metric("Merma de suero", "s/d" if merma is None else f"{merma:,.1f} L ({fmt(r['merma_suero_pct'])})")
    d1, d2, d3 = st.columns(3)
    d1.metric("Exactitud del inventario (meta ≥95%)", fmt(r["exactitud"]))
    d2.metric(f"Pasteurizaciones conformes ({r['pasteurizaciones']})", fmt(r["past_conforme"]))
    d3.metric("Tasa de devoluciones", fmt(r["tasa_devoluciones"]))


ESTILO_RESUMEN = """<style>
div[data-testid="stButton"] button[data-testid="stBaseButton-secondary"] {
    height: 3.2rem; font-weight: 700; background: #339966; color: #000;
    border: 2px solid #1f2a5c; border-radius: 4px; }
div[data-testid="stButton"] button[data-testid="stBaseButton-secondary"]:hover {
    background: #2b8558; color: #000; border-color: #1f2a5c; }
div[data-testid="stButton"] button[data-testid="stBaseButton-primary"] {
    height: 3.2rem; font-weight: 700; background: #1e6b45; color: #fff;
    border: 2px solid #1f2a5c; border-radius: 4px; }
</style>"""


def pag_resumen():
    st.markdown(ESTILO_RESUMEN, unsafe_allow_html=True)
    st.markdown("### <u>RESUMEN:</u>", unsafe_allow_html=True)

    hoy = date.today()
    primero = q("SELECT MIN(fecha) AS f FROM movimientos").f[0]
    anio_ini = int(str(primero)[:4]) if primero else hoy.year
    anios = list(range(hoy.year, min(anio_ini, hoy.year) - 1, -1))
    anio = st.selectbox("Año", anios, key="res_anio")

    if "res_sel" not in st.session_state:
        st.session_state["res_sel"] = hoy.month      # 1..12 = mes, 0 = año completo
    sel = st.session_state["res_sel"]

    for fila in range(3):                             # 12 meses en una cuadrícula de 4 x 3
        cols = st.columns(4)
        for j, col in enumerate(cols):
            m = fila * 4 + j + 1
            if col.button(MESES[m - 1], key=f"mes_{m}", width="stretch",
                          type="primary" if sel == m else "secondary"):
                st.session_state["res_sel"] = m
                st.rerun()
    if st.button(f"AÑO {anio} COMPLETO", key="mes_0", width="stretch",
                 type="primary" if sel == 0 else "secondary"):
        st.session_state["res_sel"] = 0
        st.rerun()

    st.divider()
    if sel == 0:
        st.subheader(f"Resumen del año {anio}")
        tarjetas_resumen(resumen_periodo(date(anio, 1, 1), date(anio, 12, 31)))
        st.subheader("Por mes")
        df = tabla_resumen([fila_resumen(MESES[m - 1].capitalize(),
                                         resumen_periodo(date(anio, m, 1), date(anio, m, monthrange(anio, m)[1])))
                            for m in range(1, 13)])
        nombre_csv = f"resumen_{anio}.csv"
    else:
        ini, fin = date(anio, sel, 1), date(anio, sel, monthrange(anio, sel)[1])
        st.subheader(f"Resumen de {MESES[sel - 1].capitalize()} {anio}")
        tarjetas_resumen(resumen_periodo(ini, fin))
        st.subheader("Por semana")
        df = tabla_resumen([fila_resumen(et, resumen_periodo(a, b)) for et, a, b in semanas_del_mes(anio, sel)])
        nombre_csv = f"resumen_{anio}_{sel:02d}.csv"
    st.dataframe(df, width="stretch", hide_index=True)
    st.download_button("Descargar esta tabla (CSV)", df.to_csv(index=False).encode("utf-8-sig"),
                       nombre_csv, "text/csv")
    if sel != 0:
        st.subheader("Por producto")
        pp = tabla_por_producto(str(ini), str(fin))
        if pp.empty:
            st.info("Sin producción, despachos ni devoluciones en este mes.")
        else:
            st.dataframe(pp, width="stretch", hide_index=True)
    st.caption("Los registros anulados no se cuentan. Fill rate, recuperación, merma y exactitud se "
               "calculan con los registros de cada periodo; las semanas van de lunes a domingo.")


PAGINAS = {
    "Panel": pag_dashboard,
    "Resumen": pag_resumen,
    "Catálogos": pag_catalogos,
    "Programación diaria": pag_programacion,
    "Llegada y análisis de leche": pag_recepcion,
    "Pasteurización": pag_pasteurizacion,
    "Producción y mermas": pag_produccion,
    "Trazabilidad": pag_trazabilidad,
    "Pedidos y despachos": pag_despachos,
    "Pedidos devueltos": pag_devoluciones,
    "Almacén de producto terminado": pag_almacen_pt,
    "Kardex": pag_kardex,
    "Inventario físico": pag_conteo,
    "Indicadores": pag_indicadores,
}


def main():
    st.set_page_config(page_title="ERP Lácteos", page_icon="🧀", layout="wide")
    init_db()
    st.sidebar.title("🧀 ERP Lácteos")
    PAGINAS[st.sidebar.radio("Módulo", list(PAGINAS))]()


if __name__ == "__main__":
    main()
