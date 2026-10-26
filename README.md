# SaborLocal — Flask + MongoDB

Aplicación web con roles Cliente y Chef. Incluye menú de restaurantes, pedidos y pago simulado local. No se conecta a Wompi ni procesa cobros reales.

## Instalación
```powershell
python -m pip install -r requirements.txt
```

## Ejecutar
Con MongoDB local:
```powershell
python app.py
```
Sin MongoDB (datos temporales en memoria):
```powershell
$env:SABORLOCAL_MOCK="1"
python app.py
```
Abre `http://127.0.0.1:5000`.

## API REST
Las rutas `/api/v1/...` devuelven JSON conciso, no HTML.

- `GET /api/v1/restaurantes` — restaurantes publicados.
- `GET /api/v1/restaurantes/1` — detalle del restaurante.
- `GET /api/v1/restaurantes/1/menu` — menú activo de un restaurante.
- `GET /api/v1/restaurantes/1/menu/2` — detalle de un platillo.
- `GET /api/v1/clientes/me` — perfil del cliente autenticado (requiere sesión).
- `DELETE /api/v1/restaurantes/1/menu/2` — retira un platillo (requiere sesión de chef dueño del restaurante).

Ejemplo de respuesta:
```json
{"ok":true,"data":[{"id":1,"nombre_restaurante":"Sabor de Casa","estado":"PUBLICADO"}],"total":1}
```

Para probar rutas protegidas desde Postman, inicia sesión primero en la web y conserva las cookies de sesión. Si no hay sesión, la API devuelve JSON `401` en lugar de redirigir a una página HTML.

## Pago simulado
El pago local permite probar aprobación o rechazo sin Wompi ni dinero real. Una aprobación actualiza el pedido y genera el recibo.
