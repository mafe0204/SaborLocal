"""Versión WEB de SaborLocal (Flask + MongoDB), reutilizando core/ y services/."""

import functools
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify

from core.dtos import UserRegistrationDTO, ChefRegistrationDTO
from services.auth_service import AuthService, AuthError
from services.restaurant_service import RestaurantService, RestaurantError
from services.order_service import OrderService, OrderError
from services.payment_service import PaymentService, PaymentError
from services.review_service import ReviewService, ReviewError

app = Flask(__name__)
app.secret_key = "saborlocal-web-dev-secret"

ESTADOS_LEGIBLES = {
    "PENDIENTE_CHEF": ("Esperando confirmación del restaurante", "warning"),
    "RECHAZADO": ("Rechazado por el restaurante", "danger"),
    "PAGADO": ("Pagado — en cola de preparación", "info"),
    "EN_PREPARACION": ("En preparación", "info"),
    "EN_TRANSITO": ("En camino", "info"),
    "LISTO_PARA_RECOGER": ("Listo para recoger", "info"),
    "COMPLETADO": ("Completado", "success"),
}


# Helpers de sesión / autorización
def usuario_actual():
    return session.get("usuario")


def requiere_login(rol=None):
    def decorador(vista):
        @functools.wraps(vista)
        def envoltura(*args, **kwargs):
            usuario = usuario_actual()
            if usuario is None:
                flash("Debes iniciar sesión para continuar.", "warning")
                return redirect(url_for("login"))
            if rol and usuario["rol"] != rol:
                flash("No tienes permiso para acceder a esa sección.", "danger")
                return redirect(url_for("index"))
            return vista(*args, **kwargs)
        return envoltura
    return decorador


@app.context_processor
def inyectar_globales():
    return {"usuario": usuario_actual(), "estados_legibles": ESTADOS_LEGIBLES}


def cantidad_en_carrito(producto_id):
    return sum(i["cantidad"] for i in session.get("carrito", []) if i["producto_id"] == producto_id)


# Home
@app.route("/")
def index():
    usuario = usuario_actual()
    if usuario is None:
        return redirect(url_for("login"))
    destino = {"CLIENTE": "panel_cliente", "CHEF": "panel_chef"}
    return redirect(url_for(destino[usuario["rol"]]))


# HU-01/02/03/04: autenticación
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        try:
            resultado = AuthService().iniciar_sesion(
                request.form.get("correo", "").strip(), request.form.get("contrasena", "")
            )
            session["usuario"] = resultado["usuario"]
            session["token"] = resultado["token"]
            session.setdefault("carrito", [])
            return redirect(url_for("index"))
        except AuthError as e:
            flash(str(e), "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/registro/cliente", methods=["GET", "POST"])
def registro_cliente():
    if request.method == "POST":
        dto = UserRegistrationDTO(
            nombre=request.form.get("nombre", ""),
            correo_electronico=request.form.get("correo", "").strip(),
            contrasena=request.form.get("contrasena", ""),
        )
        try:
            AuthService().registrar_cliente(dto)
            flash("Cuenta creada. Ahora puedes iniciar sesión.", "success")
            return redirect(url_for("login"))
        except AuthError as e:
            flash(str(e), "danger")
    return render_template("registro_cliente.html")


@app.route("/registro/chef", methods=["GET", "POST"])
def registro_chef():
    if request.method == "POST":
        dto = ChefRegistrationDTO(
            nombre=request.form.get("nombre", ""),
            correo_electronico=request.form.get("correo", "").strip(),
            contrasena=request.form.get("contrasena", ""),
            nombre_restaurante=request.form.get("nombre_restaurante", ""),
            direccion=request.form.get("direccion", ""),
            descripcion=request.form.get("descripcion", ""),
        )
        try:
            AuthService().registrar_chef(dto)
            flash("Restaurante registrado. Inicia sesión para configurar tu menú y publicarlo.", "success")
            return redirect(url_for("login"))
        except AuthError as e:
            flash(str(e), "danger")
    return render_template("registro_chef.html")


# CLIENTE
@app.route("/cliente")
@requiere_login("CLIENTE")
def panel_cliente():
    restaurantes = RestaurantService().listar_restaurantes_publicados()
    return render_template("cliente_explorar.html", restaurantes=restaurantes)


@app.route("/cliente/restaurante/<int:restaurante_id>")
@requiere_login("CLIENTE")
def cliente_restaurante(restaurante_id):
    servicio = RestaurantService()
    productos = servicio.listar_productos(restaurante_id)
    restaurante = next((r for r in servicio.listar_restaurantes_publicados()
                         if r["id"] == restaurante_id), None)
    en_carrito = {p["id"]: cantidad_en_carrito(p["id"]) for p in productos}
    return render_template("cliente_menu.html", productos=productos, restaurante=restaurante,
                            restaurante_id=restaurante_id, en_carrito=en_carrito)


@app.route("/cliente/carrito/agregar", methods=["POST"])
@requiere_login("CLIENTE")
def carrito_agregar():
    restaurante_id = int(request.form["restaurante_id"])
    producto_id = int(request.form["producto_id"])
    try:
        cantidad = max(1, int(request.form.get("cantidad", 1)))
    except ValueError:
        cantidad = 1
    volver = redirect(url_for("cliente_restaurante", restaurante_id=restaurante_id))

    producto = RestaurantService().obtener_producto(producto_id)
    if producto is None or producto["restaurante_id"] != restaurante_id:
        flash("Ese producto ya no está disponible.", "danger")
        return volver

    carrito = session.get("carrito", [])
    if carrito and carrito[0]["restaurante_id"] != restaurante_id:
        flash("Ya tienes productos de otro restaurante en el carrito. Vacíalo antes de agregar de otro.", "warning")
        return volver

    ya_en_carrito = cantidad_en_carrito(producto_id)
    disponible = producto["inventario"] - ya_en_carrito
    if disponible <= 0:
        flash(f"No hay más unidades de '{producto['nombre']}' disponibles"
              + (" (ya tienes todo el stock en tu carrito)." if ya_en_carrito else "."), "warning")
        return volver
    if cantidad > disponible:
        flash(f"Solo puedes agregar {disponible} unidad(es) más de '{producto['nombre']}'. "
              f"Se agregó el máximo disponible.", "warning")
        cantidad = disponible

    for item in carrito:
        if item["producto_id"] == producto_id:
            item["cantidad"] += cantidad
            break
    else:
        carrito.append({"restaurante_id": restaurante_id, "producto_id": producto_id,
                         "nombre": producto["nombre"], "precio": producto["precio"], "cantidad": cantidad})
    session["carrito"] = carrito
    flash(f"'{producto['nombre']}' agregado al carrito.", "success")
    return volver


@app.route("/cliente/carrito/quitar/<int:producto_id>", methods=["POST"])
@requiere_login("CLIENTE")
def carrito_quitar(producto_id):
    session["carrito"] = [i for i in session.get("carrito", []) if i["producto_id"] != producto_id]
    return redirect(url_for("carrito_ver"))


@app.route("/cliente/carrito/actualizar/<int:producto_id>", methods=["POST"])
@requiere_login("CLIENTE")
def carrito_actualizar(producto_id):
    """Edita la cantidad de un producto ya agregado (siempre respetando el stock)."""
    try:
        nueva_cantidad = int(request.form.get("cantidad", 1))
    except (TypeError, ValueError):
        nueva_cantidad = 1

    if nueva_cantidad <= 0:
        session["carrito"] = [i for i in session.get("carrito", []) if i["producto_id"] != producto_id]
        return redirect(url_for("carrito_ver"))

    producto = RestaurantService().obtener_producto(producto_id)
    if producto is None:
        flash("Ese producto ya no está disponible; se quitó del carrito.", "warning")
        session["carrito"] = [i for i in session.get("carrito", []) if i["producto_id"] != producto_id]
        return redirect(url_for("carrito_ver"))
    if nueva_cantidad > producto["inventario"]:
        flash(f"Solo hay {producto['inventario']} unidades disponibles de '{producto['nombre']}'.", "warning")
        nueva_cantidad = max(1, producto["inventario"])

    carrito = session.get("carrito", [])
    for item in carrito:
        if item["producto_id"] == producto_id:
            item["cantidad"] = nueva_cantidad
            break
    session["carrito"] = carrito
    return redirect(url_for("carrito_ver"))


@app.route("/cliente/carrito")
@requiere_login("CLIENTE")
def carrito_ver():
    carrito = session.get("carrito", [])
    total = round(sum(i["precio"] * i["cantidad"] for i in carrito), 2)
    return render_template("cliente_carrito.html", carrito=carrito, total=total)


@app.route("/cliente/checkout", methods=["POST"])
@requiere_login("CLIENTE")
def checkout():
    carrito = session.get("carrito", [])
    if not carrito:
        flash("Tu carrito está vacío.", "warning")
        return redirect(url_for("panel_cliente"))

    tipo_entrega = request.form.get("tipo_entrega", "DOMICILIO")
    direccion = request.form.get("direccion", "")
    try:
        RestaurantService.validar_tipo_entrega(tipo_entrega, direccion)
        carrito_items = [{"producto_id": i["producto_id"], "cantidad": i["cantidad"]} for i in carrito]
        pedido_id = OrderService().crear_pedido(
            cliente_id=usuario_actual()["id"],
            restaurante_id=carrito[0]["restaurante_id"],
            carrito=carrito_items,
            tipo_entrega=tipo_entrega,
            direccion_entrega=direccion if tipo_entrega == "DOMICILIO" else None,
        )
        session["carrito"] = []
        flash(f"Pedido #{pedido_id} creado. Espera a que el restaurante lo confirme.", "success")
        return redirect(url_for("cliente_pedidos"))
    except (RestaurantError, OrderError) as e:
        flash(str(e), "danger")
        return redirect(url_for("carrito_ver"))


@app.route("/cliente/pedidos")
@requiere_login("CLIENTE")
def cliente_pedidos():
    ordenes = OrderService()
    pedidos = ordenes.listar_pedidos_de_cliente(usuario_actual()["id"])
    aceptados = {p["id"]: ordenes.pedido_fue_aceptado(p["id"]) for p in pedidos}
    calificados = {p["id"]: ReviewService().pedido_calificado(p["id"]) for p in pedidos}
    return render_template("cliente_pedidos.html", pedidos=pedidos, aceptados=aceptados, calificados=calificados)


@app.route("/cliente/pedido/<int:pedido_id>/pagar", methods=["GET", "POST"])
@requiere_login("CLIENTE")
def cliente_pagar(pedido_id):
    payment_service = PaymentService()
    if request.method == "POST":
        tipo_pago = request.form.get("tipo_pago")
        campos = {
            "numero_tarjeta": request.form.get("numero_tarjeta", ""),
            "fecha_expiracion": request.form.get("fecha_expiracion", ""),
            "cvv": request.form.get("cvv", ""),
            "banco": request.form.get("banco", ""),
            "numero_referencia": request.form.get("numero_referencia", ""),
        }
        try:
            dto = payment_service.preparar_pago(pedido_id, tipo_pago, cliente_id=usuario_actual()["id"], **campos)
            resultado = payment_service.procesar_pago(pedido_id, dto)
            flash(f"¡Pago aprobado! Transacción {resultado['id_transaccion']}.", "success")
            return redirect(url_for("cliente_recibo", pedido_id=pedido_id))
        except PaymentError as e:
            flash(str(e), "danger")
    try:
        cabecera, detalles = OrderService().detalle_pedido(pedido_id)
        if cabecera["cliente_id"] != usuario_actual()["id"]:
            flash("Este pedido no te pertenece.", "danger")
            return redirect(url_for("cliente_pedidos"))
    except (OrderError, KeyError):
        flash("No se encontró el pedido.", "danger")
        return redirect(url_for("cliente_pedidos"))
    return render_template("cliente_pagar.html", pedido=cabecera, detalles=detalles)


@app.route("/cliente/pedido/<int:pedido_id>/recibo")
@requiere_login("CLIENTE")
def cliente_recibo(pedido_id):
    recibo = PaymentService().obtener_recibo(pedido_id)
    return render_template("cliente_recibo.html", recibo=recibo, pedido_id=pedido_id)


@app.route("/cliente/pedido/<int:pedido_id>/confirmar", methods=["POST"])
@requiere_login("CLIENTE")
def cliente_confirmar(pedido_id):
    try:
        OrderService().confirmar_recepcion(pedido_id, usuario_actual()["id"])
        flash("Recepción confirmada. ¡Buen provecho!", "success")
    except OrderError as e:
        flash(str(e), "danger")
    return redirect(url_for("cliente_pedidos"))


@app.route("/cliente/pedido/<int:pedido_id>/calificar", methods=["POST"])
@requiere_login("CLIENTE")
def cliente_calificar(pedido_id):
    try:
        ReviewService().calificar_pedido(
            pedido_id, usuario_actual()["id"],
            int(request.form.get("puntuacion", 5)), request.form.get("comentario", ""),
        )
        flash("¡Gracias por tu calificación!", "success")
    except ReviewError as e:
        flash(str(e), "danger")
    return redirect(url_for("cliente_pedidos"))


# CHEF
@app.route("/chef")
@requiere_login("CHEF")
def panel_chef():
    restaurante = RestaurantService().obtener_restaurante_por_usuario(usuario_actual()["id"])
    pedidos, aceptados, detalles = [], {}, {}
    if restaurante and restaurante["estado"] == "PUBLICADO":
        ordenes = OrderService()
        pedidos = ordenes.listar_pedidos_de_chef(restaurante["id"])
        aceptados = {p["id"]: ordenes.pedido_fue_aceptado(p["id"]) for p in pedidos}
        detalles = {p["id"]: ordenes.detalle_pedido(p["id"])[1] for p in pedidos}
    return render_template("chef_panel.html", restaurante=restaurante, pedidos=pedidos,
                            aceptados=aceptados, detalles=detalles)


@app.route("/chef/menu", methods=["GET", "POST"])
@requiere_login("CHEF")
def chef_menu():
    servicio = RestaurantService()
    usuario_id = usuario_actual()["id"]
    restaurante = servicio.obtener_restaurante_por_usuario(usuario_id)
    borrador = session.get("borrador_platillos", [])

    if request.method == "POST":
        accion = request.form.get("accion")
        try:
            if accion == "agregar_platillo":            # a la lista, antes de publicar
                precio = float(request.form.get("precio", 0))
                inventario = int(request.form.get("inventario", 0))
                if precio <= 0:
                    raise ValueError("El precio debe ser mayor a 0.")
                if inventario < 0:
                    raise ValueError("El inventario no puede ser negativo.")
                borrador.append({
                    "nombre": request.form.get("nombre_platillo", ""),
                    "descripcion": request.form.get("descripcion_platillo", ""),
                    "precio": precio, "inventario": inventario,
                })
                session["borrador_platillos"] = borrador
                flash("Platillo agregado a la lista (aún no publicado).", "success")

            elif accion == "quitar_platillo":
                idx = int(request.form.get("indice"))
                if 0 <= idx < len(borrador):
                    borrador.pop(idx)
                session["borrador_platillos"] = borrador

            elif accion == "publicar":
                servicio.configurar_perfil_y_menu(
                    usuario_id=usuario_id,
                    nombre=request.form.get("nombre_restaurante", ""),
                    direccion=request.form.get("direccion", ""),
                    descripcion=request.form.get("descripcion", ""),
                    hora_apertura=request.form.get("hora_apertura", ""),
                    hora_cierre=request.form.get("hora_cierre", ""),
                    platillos=borrador,
                )
                session["borrador_platillos"] = []
                flash("¡Restaurante publicado! Ya aparece en el catálogo de clientes.", "success")
                return redirect(url_for("panel_chef"))

            elif accion == "actualizar_perfil":         # restaurante ya publicado
                servicio.actualizar_perfil(
                    usuario_id, request.form.get("nombre_restaurante", ""),
                    request.form.get("direccion", ""), request.form.get("descripcion", ""),
                    request.form.get("hora_apertura", ""), request.form.get("hora_cierre", ""),
                )
                flash("Perfil del restaurante actualizado.", "success")

            elif accion == "agregar_platillo_publicado":  # directo al menú publicado
                servicio.agregar_platillo(
                    usuario_id, request.form.get("nombre_platillo", ""),
                    request.form.get("descripcion_platillo", ""),
                    float(request.form.get("precio", 0)), int(request.form.get("inventario", 0)),
                )
                flash("Platillo agregado al menú publicado.", "success")

            elif accion == "quitar_publicado":
                servicio.quitar_producto(int(request.form.get("producto_id")), usuario_id)
                flash("Platillo retirado del menú.", "info")
        except (RestaurantError, ValueError) as e:
            flash(str(e), "danger")
        return redirect(url_for("chef_menu"))

    menu_publicado = []
    if restaurante and restaurante["estado"] == "PUBLICADO":
        menu_publicado = servicio.listar_productos(restaurante["id"])
    return render_template("chef_menu.html", restaurante=restaurante, borrador=borrador,
                            menu_publicado=menu_publicado)


@app.route("/chef/producto/<int:producto_id>/editar", methods=["GET", "POST"])
@requiere_login("CHEF")
def chef_editar_producto(producto_id):
    """Edita nombre, descripción, precio y stock de un platillo ya publicado."""
    servicio = RestaurantService()
    try:
        producto = servicio.obtener_producto_de_chef(producto_id, usuario_actual()["id"])
    except RestaurantError as e:
        flash(str(e), "danger")
        return redirect(url_for("chef_menu"))

    if request.method == "POST":
        try:
            servicio.actualizar_producto(
                producto_id=producto_id, usuario_id=usuario_actual()["id"],
                nombre=request.form.get("nombre", ""), descripcion=request.form.get("descripcion", ""),
                precio=float(request.form.get("precio", 0)), inventario=int(request.form.get("inventario", 0)),
            )
            flash("Platillo actualizado.", "success")
            return redirect(url_for("chef_menu"))
        except (RestaurantError, ValueError) as e:
            flash(str(e), "danger")
    return render_template("chef_editar_producto.html", producto=producto)


@app.route("/chef/pedido/<int:pedido_id>/aceptar", methods=["POST"])
@requiere_login("CHEF")
def chef_aceptar(pedido_id):
    try:
        OrderService().aceptar_pedido(pedido_id, usuario_actual()["id"])
        flash("Pedido aceptado. El cliente ya puede pagar.", "success")
    except OrderError as e:
        flash(str(e), "danger")
    return redirect(url_for("panel_chef"))


@app.route("/chef/pedido/<int:pedido_id>/rechazar", methods=["POST"])
@requiere_login("CHEF")
def chef_rechazar(pedido_id):
    try:
        OrderService().rechazar_pedido(pedido_id, usuario_actual()["id"], request.form.get("motivo", ""))
        flash("Pedido rechazado. Se devolvió el stock reservado.", "info")
    except OrderError as e:
        flash(str(e), "danger")
    return redirect(url_for("panel_chef"))


@app.route("/chef/pedido/<int:pedido_id>/preparar", methods=["POST"])
@requiere_login("CHEF")
def chef_preparar(pedido_id):
    try:
        OrderService().iniciar_preparacion_comando(pedido_id, usuario_actual()["id"])
        flash("Preparación iniciada.", "success")
    except OrderError as e:
        flash(str(e), "danger")
    return redirect(url_for("panel_chef"))


@app.route("/chef/pedido/<int:pedido_id>/despachar", methods=["POST"])
@requiere_login("CHEF")
def chef_despachar(pedido_id):
    try:
        resultado = OrderService().despachar_o_marcar_listo(pedido_id, usuario_actual()["id"])
        flash(resultado["mensaje"], "success")
    except OrderError as e:
        flash(str(e), "danger")
    return redirect(url_for("panel_chef"))


# API REST: respuestas JSON breves, sin plantillas HTML.
def api_error(mensaje, estado):
    return jsonify({"ok": False, "error": mensaje}), estado


def api_requiere_rol(rol=None):
    def decorador(vista):
        @functools.wraps(vista)
        def envoltura(*args, **kwargs):
            usuario = usuario_actual()
            if usuario is None:
                return api_error("Autenticación requerida", 401)
            if rol and usuario.get("rol") != rol:
                return api_error("No autorizado", 403)
            return vista(*args, **kwargs)
        return envoltura
    return decorador


def restaurante_json(r):
    return {k: r.get(k) for k in ("id", "nombre_restaurante", "direccion", "descripcion", "estado", "hora_apertura", "hora_cierre", "en_horario") if k in r}


def producto_json(p):
    return {k: p.get(k) for k in ("id", "restaurante_id", "nombre", "descripcion", "precio", "inventario", "esta_activo") if k in p}


@app.route("/api/v1/clientes/me", methods=["GET"])
@api_requiere_rol("CLIENTE")
def api_cliente_actual():
    usuario = usuario_actual()
    return jsonify({"ok": True, "data": {"id": usuario["id"], "nombre": usuario.get("nombre"), "correo": usuario.get("correo"), "rol": usuario.get("rol")}})


@app.route("/api/v1/restaurantes", methods=["GET"])
def api_restaurantes():
    restaurantes = RestaurantService().listar_restaurantes_publicados()
    return jsonify({"ok": True, "data": [restaurante_json(r) for r in restaurantes], "total": len(restaurantes)})


@app.route("/api/v1/restaurantes/<int:restaurante_id>", methods=["GET"])
def api_restaurante(restaurante_id):
    restaurante = next((r for r in RestaurantService().listar_restaurantes_publicados() if r["id"] == restaurante_id), None)
    if restaurante is None:
        return api_error("Restaurante no encontrado", 404)
    return jsonify({"ok": True, "data": restaurante_json(restaurante)})


@app.route("/api/v1/restaurantes/<int:restaurante_id>/menu", methods=["GET"])
def api_menu_restaurante(restaurante_id):
    restaurante = next((r for r in RestaurantService().listar_restaurantes_publicados() if r["id"] == restaurante_id), None)
    if restaurante is None:
        return api_error("Restaurante no encontrado", 404)
    productos = RestaurantService().listar_productos(restaurante_id)
    return jsonify({"ok": True, "restaurante": {"id": restaurante["id"], "nombre": restaurante["nombre_restaurante"]}, "data": [producto_json(p) for p in productos], "total": len(productos)})


@app.route("/api/v1/restaurantes/<int:restaurante_id>/menu/<int:producto_id>", methods=["GET"])
def api_producto_menu(restaurante_id, producto_id):
    restaurante = next((r for r in RestaurantService().listar_restaurantes_publicados() if r["id"] == restaurante_id), None)
    if restaurante is None:
        return api_error("Restaurante no encontrado", 404)
    producto = RestaurantService().obtener_producto(producto_id)
    if producto is None or producto.get("restaurante_id") != restaurante_id:
        return api_error("Platillo no encontrado", 404)
    return jsonify({"ok": True, "data": producto_json(producto)})


@app.route("/api/v1/restaurantes/<int:restaurante_id>/menu/<int:producto_id>", methods=["DELETE"])
@api_requiere_rol("CHEF")
def api_quitar_producto(restaurante_id, producto_id):
    servicio = RestaurantService()
    restaurante = servicio.obtener_restaurante_por_usuario(usuario_actual()["id"])
    if restaurante is None or restaurante["id"] != restaurante_id:
        return api_error("El restaurante no pertenece al chef autenticado", 403)
    try:
        servicio.quitar_producto(producto_id, usuario_actual()["id"])
    except RestaurantError as e:
        return api_error(str(e), 404)
    return jsonify({"ok": True, "message": "Platillo retirado del menú", "data": {"restaurante_id": restaurante_id, "producto_id": producto_id, "esta_activo": False}})


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
