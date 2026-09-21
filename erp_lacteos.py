"""
ERP de inventarios para planta de lácteos
=========================================
Módulos: catálogos (productos, proveedores, operadores), recepción de leche,
pasteurización, producción (con suero y mermas), pedidos/despachos, Kardex
transaccional, inventario físico e indicadores.

Requisitos:   pip install "streamlit>=1.50" pandas
Ejecución:    streamlit run erp_lacteos.py

Todo se guarda en un archivo SQLite local (erp_lacteos.db).
Regla central: NO se edita el stock a mano; el stock siempre es la suma de
los movimientos del Kardex (ENTRADA - SALIDA).
"""
import sqlite3
from datetime import date

import pandas as pd
import streamlit as st

DB = "erp_lacteos.db"

TIPOS_PRODUCTO = ["Materia prima", "Insumo", "Producto terminado", "Subproducto"]
UNIDADES = ["kg", "L", "empaque", "unidad"]
VERSION_CATALOGO = 2

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
def registrar_recepcion(fecha, proveedor_id, litros, grasa, acidez, densidad, temp, aprobada):
    def _op(con):
        con.execute(
            "INSERT INTO recepcion_leche (fecha, proveedor_id, litros, grasa, acidez, densidad, "
            "temperatura, aprobada) VALUES (?,?,?,?,?,?,?,?)",
            (str(fecha), proveedor_id, litros, grasa, acidez, densidad, temp, int(aprobada)))
        if aprobada:  # la leche rechazada no ingresa al inventario
            mover(con, fecha, producto_id(con, "MP-LECHE"), "ENTRADA",
                  "Recepción de leche", litros, documento=f"REC-{fecha}")
    transaccion(_op)


def registrar_produccion(fecha, lote, pid, litros, obtenida, suero, s_vendido, s_usado,
                         operador_id=None):
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
                             operador_id, observacion=None):
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
            "operador_id, conforme, observacion) VALUES (?,?,?,?,?,?,?,?,?)",
            (str(fecha), lote, litros, temperatura, tiempo_min, hora_inicio, operador_id,
             int(conforme), observacion))
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
            "SELECT litros, aprobada FROM recepcion_leche WHERE id=?", (rid,)).fetchone()
        if aprobada and litros > 0:
            mover(con, date.today(), producto_id(con, "MP-LECHE"), "SALIDA",
                  "Anulación de recepción", litros, f"ANUL-REC-{rid}", motivo)
    _transaccion_anulacion(_op)


def anular_produccion(rid, motivo):
    def _op(con):
        _marcar_anulado(con, "produccion", rid, motivo)
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


def anular_despacho(rid, motivo):
    def _op(con):
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


def registrar_despacho(fecha, cliente, pid, pedida, despachada, documento):
    if despachada > pedida:
        raise ValueError("No se puede despachar más de lo pedido.")

    def _op(con):
        con.execute(
            "INSERT INTO pedidos (fecha, cliente, producto_id, cant_pedida, cant_despachada, documento) "
            "VALUES (?,?,?,?,?,?)", (str(fecha), cliente, pid, pedida, despachada, documento))
        if despachada > 0:
            mover(con, fecha, pid, "SALIDA", "Despacho a cliente", despachada, documento, cliente)
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


def guardar(fn, *args, ok="Registrado correctamente."):
    try:
        fn(*args)
        st.success(ok)
    except (ValueError, sqlite3.Error) as e:
        st.error(str(e))


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
    t1, t2, t3 = st.tabs(["Productos", "Proveedores", "Operadores"])
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
    st.header("Recepción de leche")
    prov = pick_proveedor()
    with st.form("f_rec", clear_on_submit=True):
        f = st.date_input("Fecha", date.today())
        litros = st.number_input("Litros recibidos", min_value=0.0, step=10.0)
        c1, c2, c3, c4 = st.columns(4)
        grasa = c1.number_input("Grasa (%)", min_value=0.0, step=0.1)
        acidez = c2.number_input("Acidez (°D)", min_value=0.0, step=0.5)
        dens = c3.number_input("Densidad", min_value=0.0, step=0.001, format="%.3f")
        temp = c4.number_input("Temp. (°C)", min_value=0.0, step=0.5)
        aprobada = st.checkbox("Aprobada por control de calidad", value=True)
        if st.form_submit_button("Registrar recepción") and prov is not None:
            guardar(registrar_recepcion, f, prov, litros, grasa, acidez, dens, temp, aprobada)
    form_anular("rec", q("""SELECT r.id, 'N°' || r.id || ' · ' || r.fecha || ' · ' ||
                                   COALESCE(v.nombre, 's/proveedor') || ' · ' || r.litros || ' L' AS etiqueta
                            FROM recepcion_leche r LEFT JOIN proveedores v ON v.id = r.proveedor_id
                            WHERE r.anulado = 0 ORDER BY r.id DESC LIMIT 100"""), anular_recepcion)
    st.dataframe(q("""SELECT r.id AS n, r.fecha, v.nombre AS proveedor, r.litros, r.grasa, r.acidez,
                      r.densidad, r.temperatura, CASE r.aprobada WHEN 1 THEN 'Sí' ELSE 'No' END AS aprobada,
                      CASE r.anulado WHEN 1 THEN 'ANULADO' ELSE 'Vigente' END AS estado,
                      r.motivo_anulacion
                      FROM recepcion_leche r LEFT JOIN proveedores v ON v.id = r.proveedor_id
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
        obs = st.text_input("Observaciones (opcional)")
        if st.form_submit_button("Registrar pasteurización"):
            if not lote.strip():
                st.error("El lote es obligatorio.")
            else:
                try:
                    ok = registrar_pasteurizacion(f, lote.strip(), litros, temp, tiempo,
                                                  hora.strftime("%H:%M"), oper, obs.strip() or None)
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
    df = q("""SELECT ps.id AS n, ps.fecha, ps.hora_inicio, ps.lote, ps.litros, ps.temperatura,
                     ps.tiempo_min AS "tiempo_min", o.nombre AS operador,
                     CASE ps.conforme WHEN 1 THEN 'Conforme' ELSE 'NO conforme' END AS resultado,
                     CASE ps.anulado WHEN 1 THEN 'ANULADO' ELSE 'Vigente' END AS estado,
                     ps.motivo_anulacion, ps.observacion
              FROM pasteurizacion ps JOIN operadores o ON o.id = ps.operador_id
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
        if st.form_submit_button("Registrar producción"):
            if not lote.strip():
                st.error("El lote es obligatorio.")
            else:
                guardar(registrar_produccion, f, lote.strip(), pid, litros, obtenida, suero,
                        vendido, usado, oper)
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


def pag_despachos():
    st.header("Pedidos y despachos")
    with st.form("f_desp", clear_on_submit=True):
        c1, c2 = st.columns(2)
        f = c1.date_input("Fecha", date.today())
        cliente = c2.text_input("Cliente")
        pid = pick_producto("Producto", ["Producto terminado"])
        c3, c4, c5 = st.columns(3)
        pedida = c3.number_input("Cantidad pedida", min_value=0.0, step=1.0)
        desp = c4.number_input("Cantidad despachada", min_value=0.0, step=1.0)
        doc = c5.text_input("N° de guía")
        if st.form_submit_button("Registrar despacho"):
            if not cliente.strip() or pedida <= 0:
                st.error("Cliente y cantidad pedida son obligatorios.")
            else:
                guardar(registrar_despacho, f, cliente.strip(), pid, pedida, desp, doc.strip() or None)
    form_anular("desp", q("""SELECT d.id, 'N°' || d.id || ' · ' || d.fecha || ' · ' || d.cliente || ' · ' ||
                                    p.nombre || ' · ' || d.cant_despachada AS etiqueta
                             FROM pedidos d JOIN productos p ON p.id = d.producto_id
                             WHERE d.anulado = 0 ORDER BY d.id DESC LIMIT 100"""), anular_despacho)
    st.dataframe(q("""SELECT d.id AS n, d.fecha, d.cliente, p.nombre AS producto, d.cant_pedida,
                      d.cant_despachada, ROUND(d.cant_despachada * 100.0 / d.cant_pedida, 1) AS "fill_rate_%",
                      d.documento, CASE d.anulado WHEN 1 THEN 'ANULADO' ELSE 'Vigente' END AS estado,
                      d.motivo_anulacion
                      FROM pedidos d JOIN productos p ON p.id = d.producto_id
                      ORDER BY d.fecha DESC, d.id DESC"""),
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
    n1, n2 = st.columns(2)
    if r["suero_merma_l"] is not None:
        n1.metric("Merma de suero (L)", f"{r['suero_merma_l']:.1f}  ({fmt(r['suero_merma_%'])})")
    n2.metric(f"Pasteurizaciones conformes ({r['pasteurizaciones']} en el periodo)",
              fmt(r["pasteurizacion_conforme"]))
    if len(r["detalle_produccion"]):
        st.subheader("Recuperación por producto")
        st.dataframe(r["detalle_produccion"], width="stretch", hide_index=True)


PAGINAS = {
    "Panel": pag_dashboard,
    "Catálogos": pag_catalogos,
    "Recepción de leche": pag_recepcion,
    "Pasteurización": pag_pasteurizacion,
    "Producción y mermas": pag_produccion,
    "Pedidos y despachos": pag_despachos,
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
