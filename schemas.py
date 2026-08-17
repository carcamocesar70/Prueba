from typing import Optional

from pydantic import BaseModel, Field, computed_field

from models import calcular_precios


class ProductoBase(BaseModel):
    nombre: str
    costo: float = Field(gt=0, description="Costo pagado por 1 empaque (costo_empaque).")
    porcentaje_ganancia: float = Field(ge=0)
    unidades_por_empaque: int = Field(ge=1, default=1)
    stock: float = Field(ge=0, default=0)


class ProductoCreate(ProductoBase):
    pass


class ProductoUpdate(BaseModel):
    nombre: Optional[str] = None
    costo: Optional[float] = Field(default=None, gt=0)
    porcentaje_ganancia: Optional[float] = Field(default=None, ge=0)
    unidades_por_empaque: Optional[int] = Field(default=None, ge=1)
    stock: Optional[float] = Field(default=None, ge=0)


class ProductoOut(ProductoBase):
    id: int
    precio_venta: float

    def _precios(self) -> dict[str, float]:
        return calcular_precios(
            self.costo,
            self.porcentaje_ganancia,
            self.unidades_por_empaque,
        )

    @computed_field
    @property
    def costo_empaque(self) -> float:
        return self.costo

    @computed_field
    @property
    def costo_unitario(self) -> float:
        return self._precios()["costo_unitario"]

    @computed_field
    @property
    def precio_venta_unitario(self) -> float:
        return self._precios()["precio_venta_unitario"]

    @computed_field
    @property
    def precio_venta_empaque(self) -> float:
        return self._precios()["precio_venta_empaque"]

    @computed_field
    @property
    def ganancia_unidad(self) -> float:
        return self._precios()["ganancia_unidad"]

    @computed_field
    @property
    def ganancia_empaque(self) -> float:
        return self._precios()["ganancia_empaque"]

    @computed_field
    @property
    def unidades_totales(self) -> int:
        return int(round(self.stock * self.unidades_por_empaque))

    class Config:
        from_attributes = True


class ClienteBase(BaseModel):
    nombre: str


class ClienteCreate(ClienteBase):
    pass


class ClienteOut(ClienteBase):
    id: int

    class Config:
        from_attributes = True


class DetalleCuentaCreate(BaseModel):
    producto_id: int
    cantidad: int = Field(gt=0)


class DetalleCuentaOut(BaseModel):
    id: int
    cuenta_id: int
    producto_id: int
    cantidad: int

    class Config:
        from_attributes = True


class CuentaCreate(BaseModel):
    cliente_id: int
    estado: str = "pendiente"


class CuentaUpdate(BaseModel):
    estado: Optional[str] = None


class CuentaOut(BaseModel):
    id: int
    cliente_id: int
    total: float
    estado: str
    detalles: list[DetalleCuentaOut] = []

    class Config:
        from_attributes = True


class ConsumoCreate(BaseModel):
    cliente_id: int
    producto_id: int
    cantidad: int = Field(gt=0)


class ConsumoOut(BaseModel):
    cliente_id: int
    producto_id: int
    cantidad: int
    monto: float
    stock_restante: int
    cuenta: CuentaOut


class ConsumoItem(BaseModel):
    producto_id: int
    cantidad: int = Field(gt=0)


class ConsumoConfirmar(BaseModel):
    nombre: str = Field(min_length=1)
    items: list[ConsumoItem] = Field(min_length=1)


class ConsumoConfirmarOut(BaseModel):
    cliente: ClienteOut
    monto_agregado: float
    cuenta: CuentaOut


class ConsumoHistorialOut(BaseModel):
    fecha: Optional[str] = None
    producto: str
    cantidad: int
    total: float


class DeudaClienteOut(BaseModel):
    cliente_id: int
    nombre: str
    saldo_pendiente: float
    estado: str
    historial: list[ConsumoHistorialOut] = []


class DeudorOut(BaseModel):
    cliente_id: int
    nombre: str
    saldo_pendiente: float
    estado: str
    consumos: int
