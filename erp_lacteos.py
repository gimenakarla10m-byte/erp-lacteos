"""
ERP de inventarios para planta de lácteos
=========================================
Módulos: catálogos, recepción de leche, producción (con suero y mermas),
pedidos/despachos, Kardex transaccional, inventario físico e indicadores.

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
    suero_usado REAL DEFAULT 0
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
    ("MP-LECHE", "Leche cruda", "Materia prima", "L", 500),
    ("IN-CUAJO", "Cuajo", "Insumo", "kg", 2),
    ("IN-SAL", "Sal", "Insumo", "kg", 20),
    ("IN-CULT", "Cultivo lácteo", "Insumo", "kg", 1),
    ("PT-MOZ", "Queso mozzarella", "Producto terminado", "kg", 30),
    ("PT-MANT", "Queso mantecoso", "Producto terminado", "kg", 30),
    ("PT-RIC", "Ricotta", "Producto terminado", "kg", 10),
    ("PT-YOG", "Yogurt", "Producto terminado", "L", 50),
    ("PT-MANTEQ", "Mantequilla", "Producto terminado", "kg", 10),
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


def init_db():
    con = get_conn()
    con.executescript(SCHEMA)
    if con.execute("SELECT COUNT(*) FROM productos").fetchone()[0] == 0:
        con.executemany(
            "INSERT INTO productos (codigo, nombre, tipo, unidad, stock_minimo) "
            "VALUES (?,?,?,?,?)", SEED_PRODUCTOS)
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


def registrar_produccion(fecha, lote, pid, litros, obtenida, suero, s_vendido, s_usado):
    if s_vendido + s_usado > suero + 1e-9:
        raise ValueError("Suero vendido + usado no puede superar el suero obtenido.")

    def _op(con):
        con.execute(
            "INSERT INTO produccion (fecha, lote, producto_id, litros_leche, cantidad_obtenida, "
            "suero_obtenido, suero_vendido, suero_usado) VALUES (?,?,?,?,?,?,?,?)",
            (str(fecha), lote, pid, litros, obtenida, suero, s_vendido, s_usado))
        leche, suero_id = producto_id(con, "MP-LECHE"), producto_id(con, "SB-SUERO")
        mover(con, fecha, leche, "SALIDA", "Consumo en producción", litros, lote)
        mover(con, fecha, pid, "ENTRADA", "Producción terminada", obtenida, lote)
        if suero > 0:
            mover(con, fecha, suero_id, "ENTRADA", "Suero de producción", suero, lote)
        if s_vendido > 0:
            mover(con, fecha, suero_id, "SALIDA", "Venta de suero", s_vendido, lote)
        if s_usado > 0:
            mover(con, fecha, suero_id, "SALIDA", "Uso interno de suero", s_usado, lote)
    transaccion(_op)


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
            "INSERT INTO conteos (fecha, producto_id, stock_sistema, stock_fisico, diferencia) "
            "VALUES (?,?,?,?,?)", (str(fecha), pid, sistema, fisico, dif))
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
                "AND documento IS NOT NULL", (d, h)).n[0]
    out["control_existencias"] = (con_doc / docs_fisicos * 100) if docs_fisicos else None

    # 2) Exactitud: conteos sin diferencia / conteos realizados
    c = q("SELECT diferencia FROM conteos WHERE fecha BETWEEN ? AND ?", (d, h))
    out["exactitud"] = ((c.diferencia.abs() < 1e-9).mean() * 100) if len(c) else None

    # 3) Fill rate: cantidad despachada / cantidad pedida
    p = q("SELECT SUM(cant_pedida) ped, SUM(cant_despachada) des FROM pedidos "
          "WHERE fecha BETWEEN ? AND ?", (d, h))
    out["fill_rate"] = (p.des[0] / p.ped[0] * 100) if p.ped[0] else None

    # 4) Coeficiente de recuperación de leche = producto obtenido / leche usada x 100
    pr = q("""SELECT p.nombre, SUM(litros_leche) leche, SUM(cantidad_obtenida) obtenido,
                     SUM(suero_obtenido) suero, SUM(suero_vendido) vendido, SUM(suero_usado) usado
              FROM produccion pr JOIN productos p ON p.id = pr.producto_id
              WHERE fecha BETWEEN ? AND ? GROUP BY p.nombre""", (d, h))
    if len(pr):
        pr["recuperacion_%"] = pr.obtenido / pr.leche * 100
        out["recuperacion_global"] = pr.obtenido.sum() / pr.leche.sum() * 100
        out["suero_merma_l"] = (pr.suero - pr.vendido - pr.usado).sum()
        out["suero_merma_%"] = out["suero_merma_l"] / pr.suero.sum() * 100 if pr.suero.sum() else None
    else:
        out["recuperacion_global"] = out["suero_merma_l"] = out["suero_merma_%"] = None
    out["detalle_produccion"] = pr
    return out


# ─────────────────────────── Interfaz ───────────────────────────
def pick_producto(label, tipos=None, key=None):
    df = stock_actual()
    if tipos:
        df = df[df.tipo.isin(tipos)]
    opts = dict(zip(df.id.tolist(), (df.nombre + " (" + df.unidad + ")").tolist()))
    return st.selectbox(label, list(opts), format_func=opts.get, key=key)


def pick_proveedor():
    df = q("SELECT id, nombre FROM proveedores ORDER BY nombre")
    if df.empty:
        st.info("Registra primero un proveedor en «Catálogos».")
        return None
    opts = dict(zip(df.id.tolist(), df.nombre.tolist()))
    return st.selectbox("Proveedor", list(opts), format_func=opts.get)


def guardar(fn, *args, ok="Registrado correctamente."):
    try:
        fn(*args)
        st.success(ok)
    except (ValueError, sqlite3.Error) as e:
        st.error(str(e))


def pag_dashboard():
    st.header("Panel de inventario")
    df = stock_actual()
    df["estado"] = df.apply(
        lambda r: "⚠️ Bajo mínimo" if r.stock < r.stock_minimo else "OK", axis=1)
    c1, c2, c3 = st.columns(3)
    c1.metric("Productos activos", len(df))
    c2.metric("Bajo stock mínimo", int((df.stock < df.stock_minimo).sum()))
    c3.metric("Movimientos en Kardex", int(q("SELECT COUNT(*) n FROM movimientos").n[0]))
    st.dataframe(df[["codigo", "nombre", "tipo", "unidad", "stock", "stock_minimo", "estado"]],
                 width="stretch", hide_index=True)


def pag_catalogos():
    st.header("Catálogos")
    t1, t2 = st.tabs(["Productos", "Proveedores"])
    with t1:
        with st.form("f_prod", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            cod = c1.text_input("Código")
            nom = c2.text_input("Nombre")
            tipo = c3.selectbox("Tipo", TIPOS_PRODUCTO)
            u1, u2 = st.columns(2)
            uni = u1.selectbox("Unidad", ["kg", "L", "unidad"])
            minimo = u2.number_input("Stock mínimo", min_value=0.0, step=1.0)
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
        st.dataframe(q("SELECT codigo, nombre, tipo, unidad, stock_minimo FROM productos"),
                     width="stretch", hide_index=True)
    with t2:
        with st.form("f_prov", clear_on_submit=True):
            n = st.text_input("Nombre del proveedor / ganadero")
            r = st.text_input("Ruta de acopio")
            if st.form_submit_button("Agregar proveedor"):
                def _addp():
                    if not n.strip():
                        raise ValueError("El nombre es obligatorio.")
                    con = get_conn()
                    con.execute("INSERT INTO proveedores (nombre, ruta) VALUES (?,?)", (n.strip(), r.strip()))
                    con.commit()
                    con.close()
                guardar(_addp)
        st.dataframe(q("SELECT nombre, ruta FROM proveedores"), width="stretch", hide_index=True)


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
    st.dataframe(q("""SELECT r.fecha, v.nombre AS proveedor, r.litros, r.grasa, r.acidez,
                      r.densidad, r.temperatura, CASE r.aprobada WHEN 1 THEN 'Sí' ELSE 'No' END AS aprobada
                      FROM recepcion_leche r LEFT JOIN proveedores v ON v.id = r.proveedor_id
                      ORDER BY r.fecha DESC, r.id DESC"""),
                 width="stretch", hide_index=True)


def pag_produccion():
    st.header("Producción y mermas")
    with st.form("f_prod_reg", clear_on_submit=True):
        c1, c2 = st.columns(2)
        f = c1.date_input("Fecha", date.today())
        lote = c2.text_input("Lote")
        pid = pick_producto("Producto elaborado", ["Producto terminado"])
        c3, c4 = st.columns(2)
        litros = c3.number_input("Litros de leche usados", min_value=0.0, step=10.0)
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
                guardar(registrar_produccion, f, lote.strip(), pid, litros, obtenida, suero, vendido, usado)
    df = q("""SELECT pr.fecha, pr.lote, p.nombre AS producto, pr.litros_leche, pr.cantidad_obtenida,
                     ROUND(pr.cantidad_obtenida * 100.0 / pr.litros_leche, 2) AS "recuperación_%",
                     pr.suero_obtenido, pr.suero_vendido, pr.suero_usado,
                     pr.suero_obtenido - pr.suero_vendido - pr.suero_usado AS merma_suero_L
              FROM produccion pr JOIN productos p ON p.id = pr.producto_id
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
    st.dataframe(q("""SELECT d.fecha, d.cliente, p.nombre AS producto, d.cant_pedida, d.cant_despachada,
                      ROUND(d.cant_despachada * 100.0 / d.cant_pedida, 1) AS "fill_rate_%", d.documento
                      FROM pedidos d JOIN productos p ON p.id = d.producto_id
                      ORDER BY d.fecha DESC, d.id DESC"""),
                 width="stretch", hide_index=True)


def pag_kardex():
    st.header("Kardex")
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
    st.dataframe(q("""SELECT c.fecha, p.nombre AS producto, c.stock_sistema, c.stock_fisico, c.diferencia
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
    if r["suero_merma_l"] is not None:
        st.metric("Merma de suero (L)", f"{r['suero_merma_l']:.1f}  ({fmt(r['suero_merma_%'])})")
    if len(r["detalle_produccion"]):
        st.subheader("Recuperación por producto")
        st.dataframe(r["detalle_produccion"], width="stretch", hide_index=True)


PAGINAS = {
    "Panel": pag_dashboard,
    "Catálogos": pag_catalogos,
    "Recepción de leche": pag_recepcion,
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
