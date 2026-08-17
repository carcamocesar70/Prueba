from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from database import Base, engine, get_db
from models import (
    Cliente,
    CuentaPorPagar,
    DetalleCuenta,
    Producto,
    calcular_precios,
    descontar_unidades,
    redondear_vista,
    stock_en_unidades,
)
from schemas import (
    ClienteCreate,
    ClienteOut,
    ConsumoConfirmar,
    ConsumoConfirmarOut,
    ConsumoCreate,
    ConsumoHistorialOut,
    ConsumoOut,
    CuentaCreate,
    CuentaOut,
    CuentaUpdate,
    DeudaClienteOut,
    DeudorOut,
    DetalleCuentaCreate,
    DetalleCuentaOut,
    ProductoCreate,
    ProductoOut,
    ProductoUpdate,
)

def asegurar_esquema():
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    tablas = inspector.get_table_names()
    if "productos" in tablas:
        columnas = {columna["name"] for columna in inspector.get_columns("productos")}
        if "unidades_por_empaque" not in columnas:
            with engine.begin() as conexion:
                conexion.execute(
                    text(
                        "ALTER TABLE productos "
                        "ADD COLUMN unidades_por_empaque INTEGER NOT NULL DEFAULT 1"
                    )
                )
    if "detalle_cuenta" in tablas:
        columnas = {columna["name"] for columna in inspector.get_columns("detalle_cuenta")}
        with engine.begin() as conexion:
            if "fecha" not in columnas:
                conexion.execute(text("ALTER TABLE detalle_cuenta ADD COLUMN fecha DATETIME"))
            if "monto" not in columnas:
                conexion.execute(
                    text("ALTER TABLE detalle_cuenta ADD COLUMN monto FLOAT NOT NULL DEFAULT 0")
                )


asegurar_esquema()

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app = FastAPI(
    title="Inventario y Cuentas por Cobrar",
    description="API para gestionar productos, clientes y cuentas por cobrar.",
    version="1.0.0",
)

ESTADO_PENDIENTE = "pendiente"


def obtener_cuenta_con_detalles(db: Session, cuenta_id: int) -> CuentaPorPagar | None:
    return (
        db.query(CuentaPorPagar)
        .options(joinedload(CuentaPorPagar.detalles))
        .filter(CuentaPorPagar.id == cuenta_id)
        .first()
    )


def obtener_o_crear_cliente(db: Session, nombre: str) -> Cliente:
    nombre = nombre.strip()
    cliente = (
        db.query(Cliente)
        .filter(func.lower(Cliente.nombre) == nombre.lower())
        .first()
    )
    if cliente is None:
        cliente = Cliente(nombre=nombre)
        db.add(cliente)
        db.flush()
    return cliente


def obtener_o_crear_cuenta_pendiente(db: Session, cliente_id: int) -> CuentaPorPagar:
    cuenta = (
        db.query(CuentaPorPagar)
        .filter(
            CuentaPorPagar.cliente_id == cliente_id,
            CuentaPorPagar.estado == ESTADO_PENDIENTE,
        )
        .with_for_update()
        .first()
    )
    if cuenta is None:
        cuenta = CuentaPorPagar(
            cliente_id=cliente_id,
            total=0,
            estado=ESTADO_PENDIENTE,
        )
        db.add(cuenta)
        db.flush()
    return cuenta


def monto_linea(detalle: DetalleCuenta) -> float:
    if detalle.monto:
        return float(detalle.monto)
    producto = detalle.producto
    if not producto:
        return 0.0
    precios = calcular_precios(
        producto.costo,
        producto.porcentaje_ganancia,
        producto.unidades_por_empaque,
    )
    return redondear_vista(precios["precio_venta_unitario"] * detalle.cantidad)


def saldo_pendiente_cliente(cliente: Cliente) -> float:
    return round(
        sum(
            cuenta.total
            for cuenta in cliente.cuentas
            if cuenta.estado == ESTADO_PENDIENTE
        ),
        2,
    )


def historial_cliente(cliente: Cliente) -> list[ConsumoHistorialOut]:
    lineas: list[tuple[datetime, ConsumoHistorialOut]] = []
    for cuenta in cliente.cuentas:
        for detalle in cuenta.detalles:
            fecha_valor = detalle.fecha or datetime.min
            fecha_texto = detalle.fecha.strftime("%d/%m/%Y %H:%M") if detalle.fecha else "Sin fecha"
            nombre_producto = detalle.producto.nombre if detalle.producto else "Producto eliminado"
            lineas.append(
                (
                    fecha_valor,
                    ConsumoHistorialOut(
                        fecha=fecha_texto,
                        producto=nombre_producto,
                        cantidad=detalle.cantidad,
                        total=monto_linea(detalle),
                    ),
                )
            )
    lineas.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in lineas]


def estado_deuda(saldo: float) -> str:
    return ESTADO_PENDIENTE if saldo > 0 else "al día"


@app.get("/", response_class=HTMLResponse)
def raiz(request: Request):
    return templates.TemplateResponse(request=request, name="admin.html")


@app.get("/admin", response_class=HTMLResponse)
def pagina_admin(request: Request):
    return templates.TemplateResponse(request=request, name="admin.html")


@app.get("/cliente", response_class=HTMLResponse)
def pagina_cliente(request: Request):
    return templates.TemplateResponse(request=request, name="cliente.html")


@app.post("/productos", response_model=ProductoOut, status_code=201)
def registrar_producto(payload: ProductoCreate, db: Session = Depends(get_db)):
    producto = Producto(**payload.model_dump())
    db.add(producto)
    db.commit()
    db.refresh(producto)
    return producto


@app.get("/productos", response_model=list[ProductoOut])
def listar_productos(db: Session = Depends(get_db)):
    return db.query(Producto).all()


@app.get("/productos/{producto_id}", response_model=ProductoOut)
def obtener_producto(producto_id: int, db: Session = Depends(get_db)):
    producto = db.get(Producto, producto_id)
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    return producto


@app.put("/productos/{producto_id}", response_model=ProductoOut)
def reemplazar_producto(
    producto_id: int,
    payload: ProductoCreate,
    db: Session = Depends(get_db),
):
    producto = db.get(Producto, producto_id)
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    for campo, valor in payload.model_dump().items():
        setattr(producto, campo, valor)

    db.commit()
    db.refresh(producto)
    return producto


@app.patch("/productos/{producto_id}", response_model=ProductoOut)
def modificar_producto(
    producto_id: int,
    payload: ProductoUpdate,
    db: Session = Depends(get_db),
):
    producto = db.get(Producto, producto_id)
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    for campo, valor in payload.model_dump(exclude_unset=True).items():
        setattr(producto, campo, valor)

    db.commit()
    db.refresh(producto)
    return producto


@app.delete("/productos/{producto_id}", status_code=204)
def eliminar_producto(producto_id: int, db: Session = Depends(get_db)):
    producto = db.get(Producto, producto_id)
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    try:
        db.delete(producto)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="No se puede eliminar: el producto ya está en cuentas de clientes.",
        )


@app.post("/clientes", response_model=ClienteOut, status_code=201)
def crear_cliente(payload: ClienteCreate, db: Session = Depends(get_db)):
    cliente = Cliente(nombre=payload.nombre)
    db.add(cliente)
    db.commit()
    db.refresh(cliente)
    return cliente


@app.get("/clientes", response_model=list[ClienteOut])
def listar_clientes(db: Session = Depends(get_db)):
    return db.query(Cliente).all()


@app.get("/clientes/{cliente_id}", response_model=ClienteOut)
def obtener_cliente(cliente_id: int, db: Session = Depends(get_db)):
    cliente = db.get(Cliente, cliente_id)
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    return cliente


@app.post("/consumos", response_model=ConsumoOut, status_code=201)
def registrar_consumo(payload: ConsumoCreate, db: Session = Depends(get_db)):
    """Descuenta stock y suma la deuda del cliente en una sola transacción."""
    try:
        cliente = db.get(Cliente, payload.cliente_id)
        if not cliente:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")

        producto = (
            db.query(Producto)
            .filter(Producto.id == payload.producto_id)
            .with_for_update()
            .first()
        )
        if not producto:
            raise HTTPException(status_code=404, detail="Producto no encontrado")

        try:
            monto = descontar_unidades(producto, payload.cantidad)
        except ValueError as error:
            disponible = int(error.args[0]) if error.args else 0
            raise HTTPException(
                status_code=400,
                detail=f"Stock insuficiente. Disponible: {disponible} unidades",
            )

        cuenta = obtener_o_crear_cuenta_pendiente(db, payload.cliente_id)
        cuenta.total = round(cuenta.total + monto, 2)

        detalle = DetalleCuenta(
            cuenta_id=cuenta.id,
            producto_id=producto.id,
            cantidad=payload.cantidad,
            monto=monto,
            fecha=datetime.now(),
        )
        db.add(detalle)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="No se pudo registrar el consumo. La transacción fue revertida.",
        )

    cuenta = obtener_cuenta_con_detalles(db, cuenta.id)
    return ConsumoOut(
        cliente_id=payload.cliente_id,
        producto_id=producto.id,
        cantidad=payload.cantidad,
        monto=monto,
        stock_restante=stock_en_unidades(producto),
        cuenta=cuenta,
    )


@app.post("/consumos/confirmar", response_model=ConsumoConfirmarOut, status_code=201)
def confirmar_consumo(payload: ConsumoConfirmar, db: Session = Depends(get_db)):
    """Registra el consumo del cliente: descuenta stock y suma deuda en una transacción."""
    try:
        cliente = obtener_o_crear_cliente(db, payload.nombre)
        cuenta = obtener_o_crear_cuenta_pendiente(db, cliente.id)

        cantidades: dict[int, int] = {}
        for item in payload.items:
            cantidades[item.producto_id] = cantidades.get(item.producto_id, 0) + item.cantidad

        monto_agregado = 0.0
        for producto_id, cantidad in cantidades.items():
            producto = (
                db.query(Producto)
                .filter(Producto.id == producto_id)
                .with_for_update()
                .first()
            )
            if not producto:
                raise HTTPException(status_code=404, detail="Producto no encontrado")
            try:
                monto = descontar_unidades(producto, cantidad)
            except ValueError as error:
                disponible = int(error.args[0]) if error.args else 0
                raise HTTPException(
                    status_code=400,
                    detail=f"Stock insuficiente de {producto.nombre}. Disponible: {disponible} unidades",
                )

            cuenta.total = round(cuenta.total + monto, 2)
            monto_agregado = round(monto_agregado + monto, 2)
            db.add(
                DetalleCuenta(
                    cuenta_id=cuenta.id,
                    producto_id=producto.id,
                    cantidad=cantidad,
                    monto=monto,
                    fecha=datetime.now(),
                )
            )

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="No se pudo confirmar el consumo. La transacción fue revertida.",
        )

    return ConsumoConfirmarOut(
        cliente=cliente,
        monto_agregado=monto_agregado,
        cuenta=obtener_cuenta_con_detalles(db, cuenta.id),
    )


@app.post("/cuentas", response_model=CuentaOut, status_code=201)
def crear_cuenta(payload: CuentaCreate, db: Session = Depends(get_db)):
    cliente = db.get(Cliente, payload.cliente_id)
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    cuenta = CuentaPorPagar(
        cliente_id=payload.cliente_id,
        estado=payload.estado,
        total=0,
    )
    db.add(cuenta)
    db.commit()
    db.refresh(cuenta)
    return cuenta


@app.get("/cuentas", response_model=list[CuentaOut])
def listar_cuentas(db: Session = Depends(get_db)):
    return db.query(CuentaPorPagar).options(joinedload(CuentaPorPagar.detalles)).all()


@app.get("/cuentas/{cuenta_id}", response_model=CuentaOut)
def obtener_cuenta(cuenta_id: int, db: Session = Depends(get_db)):
    cuenta = obtener_cuenta_con_detalles(db, cuenta_id)
    if not cuenta:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada")
    return cuenta


@app.patch("/cuentas/{cuenta_id}", response_model=CuentaOut)
def actualizar_cuenta(
    cuenta_id: int,
    payload: CuentaUpdate,
    db: Session = Depends(get_db),
):
    cuenta = db.get(CuentaPorPagar, cuenta_id)
    if not cuenta:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada")

    if payload.estado is not None:
        cuenta.estado = payload.estado

    db.commit()
    db.refresh(cuenta)
    return cuenta


@app.post(
    "/cuentas/{cuenta_id}/detalles",
    response_model=DetalleCuentaOut,
    status_code=201,
)
def agregar_detalle(
    cuenta_id: int,
    payload: DetalleCuentaCreate,
    db: Session = Depends(get_db),
):
    try:
        cuenta = (
            db.query(CuentaPorPagar)
            .options(joinedload(CuentaPorPagar.detalles))
            .filter(CuentaPorPagar.id == cuenta_id)
            .with_for_update()
            .first()
        )
        if not cuenta:
            raise HTTPException(status_code=404, detail="Cuenta no encontrada")

        producto = (
            db.query(Producto)
            .filter(Producto.id == payload.producto_id)
            .with_for_update()
            .first()
        )
        if not producto:
            raise HTTPException(status_code=404, detail="Producto no encontrado")

        try:
            monto = descontar_unidades(producto, payload.cantidad)
        except ValueError as error:
            disponible = int(error.args[0]) if error.args else 0
            raise HTTPException(
                status_code=400,
                detail=f"Stock insuficiente. Disponible: {disponible} unidades",
            )

        detalle = DetalleCuenta(
            cuenta_id=cuenta_id,
            producto_id=payload.producto_id,
            cantidad=payload.cantidad,
            monto=monto,
            fecha=datetime.now(),
        )
        cuenta.total = round(cuenta.total + monto, 2)
        db.add(detalle)
        db.commit()
        db.refresh(detalle)
        return detalle
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="No se pudo agregar el detalle. La transacción fue revertida.",
        )


@app.get("/api/deuda/{nombre_cliente}", response_model=DeudaClienteOut)
def consultar_deuda(nombre_cliente: str, db: Session = Depends(get_db)):
    cliente = (
        db.query(Cliente)
        .options(
            joinedload(Cliente.cuentas)
            .joinedload(CuentaPorPagar.detalles)
            .joinedload(DetalleCuenta.producto)
        )
        .filter(func.lower(Cliente.nombre) == nombre_cliente.strip().lower())
        .first()
    )
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    saldo = saldo_pendiente_cliente(cliente)
    return DeudaClienteOut(
        cliente_id=cliente.id,
        nombre=cliente.nombre,
        saldo_pendiente=saldo,
        estado=estado_deuda(saldo),
        historial=historial_cliente(cliente),
    )


@app.get("/api/deudores", response_model=list[DeudorOut])
def listar_deudores(db: Session = Depends(get_db)):
    clientes = (
        db.query(Cliente)
        .options(joinedload(Cliente.cuentas).joinedload(CuentaPorPagar.detalles))
        .all()
    )
    deudores = []
    for cliente in clientes:
        saldo = saldo_pendiente_cliente(cliente)
        consumos = sum(len(cuenta.detalles) for cuenta in cliente.cuentas)
        deudores.append(
            DeudorOut(
                cliente_id=cliente.id,
                nombre=cliente.nombre,
                saldo_pendiente=saldo,
                estado=estado_deuda(saldo),
                consumos=consumos,
            )
        )
    deudores.sort(key=lambda item: item.saldo_pendiente, reverse=True)
    return deudores
