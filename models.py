from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, event
from sqlalchemy.orm import relationship

from database import Base


def _decimal(valor) -> Decimal:
    return Decimal(str(valor))


def redondear_vista(valor) -> float:
    """Redondeo a 2 decimales solo para mostrar o guardar moneda."""
    return float(_decimal(valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def calcular_precios(
    costo_empaque: float,
    porcentaje_ganancia: float,
    unidades_por_empaque: int = 1,
) -> dict[str, float]:
    """Cálculos con decimales exactos; el redondeo a 2 se aplica al mostrar."""
    unidades = _decimal(
        unidades_por_empaque if unidades_por_empaque and unidades_por_empaque > 0 else 1
    )
    costo = _decimal(costo_empaque)
    factor_ganancia = 1 + _decimal(porcentaje_ganancia) / Decimal("100")

    costo_unitario = costo / unidades
    precio_venta_unitario = costo_unitario * factor_ganancia
    precio_venta_empaque = precio_venta_unitario * unidades
    ganancia_unidad = precio_venta_unitario - costo_unitario
    ganancia_empaque = precio_venta_empaque - costo

    return {
        "costo_empaque": float(costo),
        "costo_unitario": float(costo_unitario),
        "precio_venta_unitario": float(precio_venta_unitario),
        "precio_venta_empaque": float(precio_venta_empaque),
        "ganancia_unidad": float(ganancia_unidad),
        "ganancia_empaque": float(ganancia_empaque),
    }


def unidades_de_empaque(producto: "Producto") -> int:
    return producto.unidades_por_empaque if producto.unidades_por_empaque and producto.unidades_por_empaque > 0 else 1


def stock_en_unidades(producto: "Producto") -> int:
    return int(round(float(_decimal(producto.stock) * _decimal(unidades_de_empaque(producto)))))


def descontar_unidades(producto: "Producto", cantidad: int) -> float:
    """Descuenta unidades individuales y devuelve el monto a cargo (precio unitario * cantidad)."""
    unidades = unidades_de_empaque(producto)
    disponible = stock_en_unidades(producto)
    if cantidad > disponible:
        raise ValueError(disponible)
    restante = disponible - cantidad
    producto.stock = float(_decimal(restante) / _decimal(unidades))
    precios = calcular_precios(producto.costo, producto.porcentaje_ganancia, unidades)
    return redondear_vista(_decimal(precios["precio_venta_unitario"]) * _decimal(cantidad))


class Producto(Base):
    __tablename__ = "productos"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, nullable=False)
    costo = Column(Float, nullable=False)
    porcentaje_ganancia = Column(Float, nullable=False, default=0)
    precio_venta = Column(Float, nullable=False, default=0)
    unidades_por_empaque = Column(Integer, nullable=False, default=1)
    stock = Column(Float, nullable=False, default=0)

    detalles = relationship("DetalleCuenta", back_populates="producto")


class Cliente(Base):
    __tablename__ = "clientes"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, nullable=False)

    cuentas = relationship("CuentaPorPagar", back_populates="cliente")


class CuentaPorPagar(Base):
    __tablename__ = "cuentas_por_pagar"

    id = Column(Integer, primary_key=True, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=False)
    total = Column(Float, nullable=False, default=0)
    estado = Column(String, nullable=False, default="pendiente")

    cliente = relationship("Cliente", back_populates="cuentas")
    detalles = relationship(
        "DetalleCuenta",
        back_populates="cuenta",
        cascade="all, delete-orphan",
    )


class DetalleCuenta(Base):
    __tablename__ = "detalle_cuenta"

    id = Column(Integer, primary_key=True, index=True)
    cuenta_id = Column(Integer, ForeignKey("cuentas_por_pagar.id"), nullable=False)
    producto_id = Column(Integer, ForeignKey("productos.id"), nullable=False)
    cantidad = Column(Integer, nullable=False)
    monto = Column(Float, nullable=False, default=0)
    fecha = Column(DateTime, nullable=True, default=datetime.now)

    cuenta = relationship("CuentaPorPagar", back_populates="detalles")
    producto = relationship("Producto", back_populates="detalles")


@event.listens_for(Producto, "before_insert")
@event.listens_for(Producto, "before_update")
def actualizar_precio_venta(mapper, connection, target):
    precios = calcular_precios(
        target.costo,
        target.porcentaje_ganancia,
        target.unidades_por_empaque,
    )
    target.precio_venta = redondear_vista(precios["precio_venta_empaque"])
