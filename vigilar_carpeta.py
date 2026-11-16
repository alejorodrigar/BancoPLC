"""Vigilante diario: sube al PLC los paquetes .pcz nuevos o cambiados de una carpeta.

Cada vez que se ejecuta (la lanza una tarea programada de Windows):
    1. busca los .pcz de la carpeta de entrada,
    2. calcula la huella SHA-256 de cada uno y lee del paquete a qué IP va,
    3. si para esa IP la última subida correcta fue de un paquete con la misma huella, no hace nada
       («SIN CAMBIOS»); si no, lo sube con subir_al_plc.py,
    4. apunta cada decisión en registro_subidas.csv y deja un log del día,
    5. si está configurado ([correo] en banco.toml), manda un correo con lo que ha subido o ha fallado.

Si hay varios .pcz para la misma IP se sube solo el más reciente. Los .pcz modificados hace menos de
`minutos_copia` minutos se dejan para la próxima vez (pueden estar copiándose todavía).

Uso:
    python vigilar_carpeta.py                      (usa banco.toml junto al programa)
    python vigilar_carpeta.py --config otro.toml
    python vigilar_carpeta.py --simulacro          (decide y lo apunta, pero no lanza CAP)
    python vigilar_carpeta.py --probar-correo      (solo manda un correo de prueba)
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import os
import sys
import tomllib
from pathlib import Path

import avisos
import subir_al_plc as subir

AQUI = subir.AQUI           # carpeta del programa (o del .exe)
CAMPOS = ["fecha", "hora", "paquete", "sha256", "ip", "cap", "resultado", "mensaje", "carpeta"]


# --------------------------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------------------------

POR_DEFECTO = {
    "entrada": str(AQUI / "entrada"),       # carpeta donde se dejan los .pcz
    "trabajo": str(AQUI / "trabajo"),       # una subcarpeta por cada subida
    "registro": str(AQUI / "registro"),     # registro_subidas.csv y logs del día
    "grabar_flash": True,
    "tiempo_max_s": 600,
    "minutos_copia": 2,
    "sin_login": False,                     # solo para probar: desempaqueta sin conectar
    "horas": ["06:00"],                     # a qué horas lanza la tarea programada (instalar_tarea.py)
    "correo": {},                           # sección [correo]: ver avisos.py
}


def validar_horas(horas) -> list[str]:
    """Comprueba que `horas` es una lista de «HH:MM» y la devuelve normalizada y sin repetidos."""
    if isinstance(horas, str):
        horas = [horas]
    if not isinstance(horas, list) or not horas:
        raise SystemExit("«horas» debe ser una lista de horas, por ejemplo: horas = ['06:00', '14:30']")
    res = []
    for h in horas:
        try:
            t = dt.datetime.strptime(str(h).strip(), "%H:%M")
        except ValueError:
            raise SystemExit(f"Hora no válida en «horas»: {h!r}. Usa el formato HH:MM, por ejemplo '06:00'.")
        if (n := f"{t:%H:%M}") not in res:
            res.append(n)
    return sorted(res)


def leer_config(ruta: Path | None) -> dict:
    cfg = dict(POR_DEFECTO)
    if ruta is None:
        ruta = AQUI / "banco.toml"
        if not ruta.is_file():
            cfg["correo"] = avisos.validar(None)
            return cfg
    try:
        with open(ruta, "rb") as f:
            datos = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise SystemExit(f"Error en {ruta}: {e}. Pon las rutas entre comillas simples: 'C:\\carpeta'.")
    desconocidas = set(datos) - set(POR_DEFECTO)
    if desconocidas:
        raise SystemExit(f"Claves desconocidas en {ruta}: {', '.join(sorted(desconocidas))}")
    cfg.update(datos)
    cfg["horas"] = validar_horas(cfg["horas"])
    cfg["correo"] = avisos.validar(cfg["correo"])
    return cfg


# --------------------------------------------------------------------------------------------
# Registro
# --------------------------------------------------------------------------------------------

class Log:
    """Escribe a la vez en pantalla y en registro/vigilante_<fecha>.log."""

    def __init__(self, carpeta: Path):
        carpeta.mkdir(parents=True, exist_ok=True)
        self.ruta = carpeta / f"vigilante_{dt.date.today():%Y-%m-%d}.log"
        self.lineas: list[str] = []         # las de esta pasada, para el correo

    def __call__(self, texto: str) -> None:
        linea = f"{dt.datetime.now():%H:%M:%S}  {texto}"
        print(linea)
        self.lineas.append(linea)
        with open(self.ruta, "a", encoding="utf-8") as f:
            f.write(linea + "\n")


def leer_registro(ruta: Path) -> list[dict]:
    if not ruta.is_file():
        return []
    with open(ruta, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=";"))


def apuntar(ruta: Path, fila: dict) -> None:
    nuevo = not ruta.is_file()
    with open(ruta, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS, delimiter=";")
        if nuevo:
            w.writeheader()
        ahora = dt.datetime.now()
        w.writerow({"fecha": f"{ahora:%Y-%m-%d}", "hora": f"{ahora:%H:%M:%S}", **fila})


def ultima_subida_ok(registro: list[dict], ip: str) -> dict | None:
    for fila in reversed(registro):
        if fila["ip"] == ip and fila["resultado"] == "OK":
            return fila
    return None


# --------------------------------------------------------------------------------------------
# Bloqueo: que no corran dos vigilantes a la vez
# --------------------------------------------------------------------------------------------

def _proceso_vivo(pid: int) -> bool:
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    codigo = ctypes.c_ulong()
    k.GetExitCodeProcess(h, ctypes.byref(codigo))
    k.CloseHandle(h)
    return codigo.value == 259                   # STILL_ACTIVE


def bloquear(carpeta: Path) -> Path | None:
    cerrojo = carpeta / "vigilante.lock"
    if cerrojo.is_file():
        try:
            pid = int(cerrojo.read_text().strip())
        except ValueError:
            pid = 0
        if pid and _proceso_vivo(pid):
            return None
    cerrojo.write_text(str(os.getpid()))
    return cerrojo


# --------------------------------------------------------------------------------------------
# Programa
# --------------------------------------------------------------------------------------------

def huella(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sube al PLC los .pcz nuevos o cambiados de una carpeta.")
    ap.add_argument("--config", type=Path, help="fichero .toml (por defecto banco.toml junto al programa)")
    ap.add_argument("--simulacro", action="store_true", help="decidir y apuntar, sin lanzar CAP ni mandar correo")
    ap.add_argument("--probar-correo", action="store_true",
                    help="mandar solo un correo de prueba con la configuración de [correo]")
    a = ap.parse_args(argv)
    cfg = leer_config(a.config)

    if a.probar_correo:
        return probar_correo(cfg["correo"])

    carpeta_reg = Path(cfg["registro"])
    log = Log(carpeta_reg)
    cerrojo = bloquear(carpeta_reg)
    if cerrojo is None:
        log("Ya hay otro vigilante en marcha; esta ejecución no hace nada.")
        return 0
    eventos: list[dict] = []        # cada subida intentada: paquete, ip, resultado, mensaje, carpeta
    try:
        errores = revisar(cfg, a.simulacro, log, eventos)
    finally:
        cerrojo.unlink(missing_ok=True)

    if not a.simulacro:
        avisar(cfg["correo"], log, eventos, errores)
    return 1 if errores else 0


def revisar(cfg: dict, simulacro: bool, log: Log, eventos: list[dict]) -> int:
    """Una pasada del vigilante. Devuelve el número de errores y deja las subidas en `eventos`."""
    entrada, trabajo, carpeta_reg = (Path(cfg[k]) for k in ("entrada", "trabajo", "registro"))
    ruta_csv = carpeta_reg / "registro_subidas.csv"
    log(f"--- Inicio{' (SIMULACRO)' if simulacro else ''}. Entrada: {entrada}")
    if not entrada.is_dir():
        log(f"ERROR: no existe la carpeta de entrada {entrada} (¿falla la red?)")
        eventos.append({"paquete": "-", "ip": "-", "resultado": "ERROR", "carpeta": "",
                        "mensaje": f"No se puede abrir la carpeta de entrada {entrada}."})
        return 1

    # 1. Leer los paquetes de la carpeta
    limite = dt.datetime.now().timestamp() - cfg["minutos_copia"] * 60
    por_ip: dict[str, list[tuple[Path, subir.InfoPaquete]]] = {}
    errores = 0
    for pcz in sorted(entrada.glob("*.pcz")):
        if pcz.stat().st_mtime > limite:
            log(f"{pcz.name}: modificado hace menos de {cfg['minutos_copia']} min, se deja para la próxima.")
            continue
        try:
            info = subir.leer_paquete(pcz)
        except subir.ErrorBanco as e:
            log(f"{pcz.name}: no se puede leer ({e})")
            apuntar(ruta_csv, {"paquete": pcz.name, "resultado": "ERROR", "mensaje": str(e)})
            eventos.append({"paquete": pcz.name, "ip": "-", "resultado": "ERROR", "mensaje": str(e), "carpeta": ""})
            errores += 1
            continue
        if not info.ip:
            msg = "El paquete no trae la IP del PLC."
            log(f"{pcz.name}: el paquete no trae IP, se ignora.")
            apuntar(ruta_csv, {"paquete": pcz.name, "cap": info.cap_version, "resultado": "ERROR", "mensaje": msg})
            eventos.append({"paquete": pcz.name, "ip": "-", "resultado": "ERROR", "mensaje": msg, "carpeta": ""})
            errores += 1
            continue
        por_ip.setdefault(info.ip, []).append((pcz, info))
    if not por_ip:
        log("No hay paquetes que revisar.")

    # 2. Por cada PLC, el paquete más reciente
    registro = leer_registro(ruta_csv)
    for ip, lista in sorted(por_ip.items()):
        lista.sort(key=lambda x: x[0].stat().st_mtime)
        pcz, info = lista[-1]
        for otro, _ in lista[:-1]:
            log(f"{otro.name}: hay un paquete más reciente para {ip} ({pcz.name}); se ignora.")
        sha = huella(pcz)
        base = {"paquete": pcz.name, "sha256": sha, "ip": ip, "cap": info.cap_version}

        anterior = ultima_subida_ok(registro, ip)
        if anterior and anterior["sha256"] == sha:
            msg = f"igual que el subido el {anterior['fecha']} {anterior['hora']}"
            log(f"{pcz.name} -> {ip}: SIN CAMBIOS ({msg}).")
            apuntar(ruta_csv, {**base, "resultado": "SIN CAMBIOS", "mensaje": msg})
            continue

        # último intento real de subir a esta IP (sin contar simulacros ni «sin cambios»)
        fallo = next((f for f in reversed(registro) if f["ip"] == ip
                      and f["resultado"] not in ("SIMULACRO", "SIN CAMBIOS")), None)
        if fallo and fallo["sha256"] == sha and fallo["resultado"] == "ERROR":
            motivo = f"el último intento, el {fallo['fecha']} {fallo['hora']}, falló; se reintenta"
        elif not anterior:
            motivo = "nunca se ha subido nada a esta IP"
        else:
            motivo = f"distinto del subido el {anterior['fecha']} ({anterior['paquete']})"
        log(f"{pcz.name} -> {ip}: hay que subirlo ({motivo}).")
        if simulacro:
            apuntar(ruta_csv, {**base, "resultado": "SIMULACRO", "mensaje": f"Se subiría: {motivo}"})
            continue

        # 3. Subir
        args = [str(pcz), "--trabajo", str(trabajo), "--tiempo-max", str(cfg["tiempo_max_s"])]
        if not cfg["grabar_flash"]:
            args.append("--sin-flash")
        if cfg["sin_login"]:
            args.append("--sin-login")
        try:
            codigo, carpeta, r = subir.ejecutar(args)
            resultado, mensaje = r.estado, r.mensaje
            if resultado == "OK" and cfg["sin_login"]:
                resultado = "OK SIN LOGIN"           # no cuenta como subido al PLC
        except Exception as e:                      # que un paquete roto no pare a los demás
            codigo, carpeta, resultado, mensaje = 99, "", "ERROR", f"Fallo inesperado: {e!r}"
        if not resultado.startswith("OK"):
            errores += 1
        log(f"{pcz.name} -> {ip}: {resultado}. {mensaje}")
        apuntar(ruta_csv, {**base, "resultado": resultado, "mensaje": mensaje, "carpeta": str(carpeta)})
        eventos.append({"paquete": pcz.name, "ip": ip, "resultado": resultado, "mensaje": mensaje,
                        "carpeta": str(carpeta)})

    log(f"--- Fin. {errores} error(es).")
    return errores


# --------------------------------------------------------------------------------------------
# Correo
# --------------------------------------------------------------------------------------------

def componer_correo(cfg_correo: dict, log: Log, eventos: list[dict], errores: int) -> tuple[str, str, list]:
    pc = avisos.nombre_pc()
    oks = [e for e in eventos if e["resultado"].startswith("OK")]
    if errores:
        estado = f"ERROR ({errores})"
    elif oks:
        estado = "OK"
    else:
        estado = "sin cambios"
    detalle = ", ".join(f"{e['paquete']} -> {e['ip']}" for e in eventos[:3]) or "nada que subir"
    if len(eventos) > 3:
        detalle += f" y {len(eventos) - 3} más"
    asunto = f"[{cfg_correo['asunto']}] {estado}: {detalle}"

    cuerpo = [f"BancoPLC en {pc}, {dt.datetime.now():%Y-%m-%d %H:%M}.", ""]
    if eventos:
        cuerpo.append("Subidas:")
        for e in eventos:
            cuerpo.append(f"  {e['resultado']:<6} {e['paquete']} -> {e['ip']}")
            cuerpo.append(f"         {e['mensaje']}")
            if e["carpeta"]:
                cuerpo.append(f"         Carpeta: {e['carpeta']}")
        cuerpo.append("")
    else:
        cuerpo += ["No había nada que subir.", ""]
    cuerpo += ["Log de esta pasada:"] + [f"  {l}" for l in log.lineas]

    adjuntos = []
    for e in eventos:
        if not e["carpeta"]:
            continue
        c = Path(e["carpeta"])
        base = Path(e["paquete"]).stem
        adjuntos.append((f"{base}_resultado.txt", c / "resultado.txt"))
        if not e["resultado"].startswith("OK"):
            adjuntos.append((f"{base}_scb.log", c / "scb.log"))
    return asunto, "\n".join(cuerpo) + "\n", adjuntos


def avisar(cfg_correo: dict, log: Log, eventos: list[dict], errores: int) -> None:
    subidas = sum(1 for e in eventos if e["resultado"].startswith("OK"))
    if not avisos.hay_que_enviar(cfg_correo, subidas, errores):
        return
    asunto, cuerpo, adjuntos = componer_correo(cfg_correo, log, eventos, errores)
    try:
        avisos.enviar(cfg_correo, asunto, cuerpo, adjuntos)
        log(f"Correo enviado a {', '.join(cfg_correo['destinatarios'])}: {asunto}")
    except avisos.ErrorCorreo as e:
        log(f"No se pudo enviar el correo: {e}")      # no cuenta como error de subida


def probar_correo(cfg_correo: dict) -> int:
    if cfg_correo["metodo"] == "no":
        print("No hay correo configurado: en banco.toml pon la sección [correo] con metodo = 'outlook' o 'smtp'.")
        return 1
    asunto = f"[{cfg_correo['asunto']}] Correo de prueba desde {avisos.nombre_pc()}"
    cuerpo = (f"Correo de prueba de BancoPLC ({dt.datetime.now():%Y-%m-%d %H:%M}).\n"
              f"Método: {cfg_correo['metodo']}. Si te llega, los avisos del vigilante también llegarán.\n")
    try:
        avisos.enviar(cfg_correo, asunto, cuerpo, [])
    except avisos.ErrorCorreo as e:
        print(f"ERROR: {e}")
        return 1
    print(f"Correo de prueba enviado a {', '.join(cfg_correo['destinatarios'])}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
