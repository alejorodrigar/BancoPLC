"""Parte 1 del banco de pruebas: subir al PLC un proyecto CAP1131 ya compilado.

Recibe el paquete del proyecto (.pcz, el ZIP que genera CAP con «Pack Project»), que ya trae la IP
del PLC guardada, y con CAP1131 en modo batch:
    1. lo desempaqueta en una carpeta de trabajo nueva (project_unpack),
    2. hace login en el PLC (online_mode_on, con la conexión guardada en el proyecto),
    3. descarga la aplicación (online_download [flash]) y sale sin guardar cambios.

La versión de CAP se elige sola con el PackInfo.txt del paquete (CAPVersion:).
No se toca ninguna DLL de CAP: solo se usan los comandos batch documentados en su ayuda.

Uso:
    python subir_al_plc.py LRV_043_v00_03_04.pcz
    python subir_al_plc.py LRV_043_v00_03_04.pcz --sin-login      (solo desempaqueta y comprueba)
    python subir_al_plc.py paquete.pcz --cap 6.2 --sin-flash --tiempo-max 900

Códigos de salida: 0 todo bien, 1 error antes de lanzar CAP (paquete, versión...),
2 CAP terminó con errores, 3 CAP superó el tiempo máximo, 4 la prueba de humo falló.
"""

from __future__ import annotations

import argparse
import configparser
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

SELECTRON = Path(r"C:\Selectron")
CARPETA_CAP = SELECTRON / "CAP1131"
CARPETA_MOS = SELECTRON / "MOS"
# Carpeta del programa: la del .exe si va empaquetado con PyInstaller (allí __file__ es una carpeta temporal)
EMPAQUETADO = getattr(sys, "frozen", False)
AQUI = Path(sys.executable).resolve().parent if EMPAQUETADO else Path(__file__).resolve().parent


class ErrorBanco(Exception):
    """Error que se explica al usuario y para la ejecución antes de lanzar CAP."""


# --------------------------------------------------------------------------------------------
# 1. Leer el paquete sin desempaquetarlo
# --------------------------------------------------------------------------------------------

@dataclass
class InfoPaquete:
    ruta: str
    cap_version: str = ""
    mos_version: str = ""
    empaquetado_por: str = ""
    fecha_paquete: str = ""
    carpeta_origen: str = ""
    conexion: str = ""          # texto legible: «SysCom LAN 192.168.0.180»
    ip: str = ""
    compilado: bool = False
    pruebas_compilado: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


def _leer_ini(texto: str) -> configparser.ConfigParser:
    ini = configparser.ConfigParser(interpolation=None, strict=False)
    ini.optionxform = str  # respetar mayúsculas de las claves
    ini.read_string(texto)
    return ini


def _buscar(nombres: dict[str, str], *candidatos: str) -> str | None:
    """Nombre real dentro del ZIP (sin distinguir mayúsculas), solo en la raíz."""
    for c in candidatos:
        if c.lower() in nombres:
            return nombres[c.lower()]
    return None


def leer_paquete(ruta: Path) -> InfoPaquete:
    if not ruta.is_file():
        raise ErrorBanco(f"No existe el paquete: {ruta}")
    try:
        z = zipfile.ZipFile(ruta)
    except zipfile.BadZipFile:
        raise ErrorBanco(f"{ruta.name} no es un paquete de CAP válido (no se puede abrir como ZIP).")

    info = InfoPaquete(ruta=str(ruta))
    with z:
        nombres = {n.lower(): n for n in z.namelist()}
        leer = lambda n: z.read(n).decode("latin-1", errors="replace")

        # PackInfo.txt: versión de CAP y de MOS con las que se empaquetó
        n = _buscar(nombres, "PackInfo.txt")
        if n is None:
            info.avisos.append("El paquete no trae PackInfo.txt: no se sabe con qué CAP se hizo.")
        else:
            for linea in leer(n).splitlines():
                clave, _, valor = linea.partition(":")
                valor = valor.strip()
                match clave.strip().lower():
                    case "capversion": info.cap_version = valor
                    case "mosversion": info.mos_version = valor
                    case "user": info.empaquetado_por = valor
                    case "date": info.fecha_paquete = valor
                    case "folder": info.carpeta_origen = valor

        # Conexión guardada: CAP 6.5 en user.ini [Online]; CAP 5.12 / 6.2 en selectron.ini [Param]
        n = _buscar(nombres, "user.ini")
        if n:
            ini = _leer_ini(leer(n))
            cadena = ini.get("Online", "ConnectionInfoString", fallback="").strip()
            if cadena:
                try:
                    d = json.loads(cadena)
                    info.ip = d.get("host") or d.get("destinationAddress") or ""
                    puerto = f", puerto {d['port']}" if d.get("port") else ""
                    info.conexion = f"{d.get('protocol', '?')} {info.ip}{puerto}"
                except json.JSONDecodeError:
                    info.conexion = cadena
        if not info.ip:
            n = _buscar(nombres, "selectron.ini")
            if n:
                ini = _leer_ini(leer(n))
                acceso = ini.get("Param", "Accesspath", fallback="").strip()   # p. ej. LAN,192.168.0.180
                medio, _, ip = acceso.partition(",")
                if ip:
                    info.ip = ip.strip()
                    info.conexion = f"SysCom {medio.strip()} {info.ip}"
        if not info.ip:
            info.avisos.append("No se encuentra la IP del PLC en el paquete (ni en user.ini ni en selectron.ini).")

        # ¿Viene compilado? res.hex (PLC real) o res.dll (simulador) en la raíz
        for fichero in ("res.hex", "res.dll", "hw_conf_plc.hex"):
            if _buscar(nombres, fichero):
                info.pruebas_compilado.append(fichero)
        info.compilado = bool({"res.hex", "res.dll"} & set(info.pruebas_compilado))
        # Informes de la última compilación: la última línea «N errors» manda
        for informe in ("ErrorLog.txt", "CompileReport.txt"):
            n = _buscar(nombres, informe)
            if not n:
                continue
            cuentas = re.findall(r"^\s*(\d+)\s+errors?\b", leer(n), re.IGNORECASE | re.MULTILINE)
            if cuentas:
                info.pruebas_compilado.append(f"{informe}: {cuentas[-1]} errores")
                if int(cuentas[-1]) > 0:
                    info.compilado = False
                    info.avisos.append(f"{informe} indica {cuentas[-1]} errores de compilación.")
    return info


# --------------------------------------------------------------------------------------------
# 2. Elegir CAP1131 y comprobar el MOS
# --------------------------------------------------------------------------------------------

def versiones_cap() -> dict[str, Path]:
    """{'6.2.0001.9194': ruta de sc.exe, ...} con las versiones instaladas."""
    res = {}
    for d in sorted(CARPETA_CAP.glob("CAP1131.V*")):
        sc = d / "SC40" / "sc.exe"
        if sc.is_file():
            res[d.name.removeprefix("CAP1131.V")] = sc
    return res


def elegir_cap(pedida: str) -> tuple[str, Path]:
    """`pedida` puede ser completa (6.2.0001.9194) o corta (6.2, 5.12)."""
    instaladas = versiones_cap()
    if not instaladas:
        raise ErrorBanco(f"No hay ninguna versión de CAP1131 instalada en {CARPETA_CAP}.")
    lista = ", ".join(instaladas)
    if not pedida:
        raise ErrorBanco(f"No se sabe qué versión de CAP usar. Indícala con --cap. Instaladas: {lista}")
    pedida = pedida.lstrip("Vv")
    if pedida in instaladas:
        return pedida, instaladas[pedida]
    coinciden = [v for v in instaladas if v.startswith(pedida + ".")]
    if len(coinciden) == 1:
        return coinciden[0], instaladas[coinciden[0]]
    raise ErrorBanco(f"El paquete pide CAP {pedida} y no está instalada. Instaladas: {lista}")


def comprobar_mos(mos: str) -> str | None:
    """Devuelve un aviso si el MOS del proyecto no está instalado (no para la ejecución)."""
    if not mos:
        return None
    if (CARPETA_MOS / mos).is_dir():
        return None
    parecidos = [d.name for d in CARPETA_MOS.glob(mos + "*")] if CARPETA_MOS.is_dir() else []
    extra = f" (solo está {', '.join(parecidos)})" if parecidos else ""
    return f"El MOS del proyecto, {mos}, no está instalado en {CARPETA_MOS}{extra}."


# --------------------------------------------------------------------------------------------
# 3. Batch de CAP
# --------------------------------------------------------------------------------------------

@dataclass
class PruebaHumo:
    """Comprobaciones rápidas con el PLC conectado. Solo se lee y se escribe con
    online_monitor_read/write: los resultados se comparan en Python, así un fallo no corta el batch."""
    vida: str = ""                                            # variable que el programa cambia sola
    bools: list[tuple[str, str]] = field(default_factory=list)  # (entrada, salida que la sigue)
    espera_ms: int = 1000

    def vacia(self) -> bool:
        return not self.vida and not self.bools

    def variables(self) -> list[str]:
        return ([self.vida] if self.vida else []) + [v for par in self.bools for v in par]


def lineas_prueba(p: PruebaHumo, csv: Path) -> list[str]:
    l = ["REM ---- prueba de humo ----"]
    if p.vida:
        l += [f"online_monitor_read {p.vida} vida1",
              "scb_wait 2000",
              f"online_monitor_read {p.vida} vida2",
              f"scb_echo {csv} vida;{p.vida};%vida1%;%vida2%"]
    for i, (ent, sal) in enumerate(p.bools, 1):
        l += [f"online_monitor_read {ent} e{i}ini",
              f"online_monitor_read {sal} s{i}ini",
              f"online_monitor_write {ent} 2#1",
              f"scb_wait {p.espera_ms}",
              f"online_monitor_read {sal} s{i}on",
              f"online_monitor_write {ent} 2#0",
              f"scb_wait {p.espera_ms}",
              f"online_monitor_read {sal} s{i}off",
              f"scb_echo {csv} bool;{ent};{sal};%e{i}ini%;%s{i}ini%;%s{i}on%;%s{i}off%"]
    return l


def variables_del_paquete(ruta: Path) -> dict[str, str]:
    """{nombre en mayúsculas: tipo} sacado del VARLIST.CSV del paquete."""
    with zipfile.ZipFile(ruta) as z:
        nombres = {n.lower(): n for n in z.namelist()}
        n = _buscar(nombres, "VARLIST.CSV")
        if n is None:
            return {}
        res = {}
        for linea in z.read(n).decode("latin-1", errors="replace").splitlines():
            c = linea.split(";")
            if len(c) >= 4 and c[0] == "VAR":
                res[c[1].upper()] = c[3]
        return res


def evaluar_prueba(csv: Path, p: PruebaHumo) -> tuple[bool, list[str]]:
    """Lee prueba.csv (escrito por CAP) y devuelve (todo bien, líneas para resultado.txt)."""
    if not csv.is_file():
        return False, ["No se generó prueba.csv: CAP no llegó a hacer la prueba (revisa scb.log)."]
    filas = [l.split(";") for l in csv.read_text(encoding="latin-1").splitlines() if l.strip()]
    ok_total, texto = True, []
    for f in filas:
        if f[0] == "vida" and len(f) >= 4:
            cambia = f[2].strip() != f[3].strip()
            texto.append(f"Señal de vida {f[1]}: {f[2]} -> {f[3]}  "
                         + ("OK (cambia: el programa está corriendo)" if cambia
                            else "FALLO (no cambia en 2 s)"))
            ok_total &= cambia
        elif f[0] == "bool" and len(f) >= 7:
            ent, sal, eini, sini, son, soff = f[1:7]
            bien_on, bien_off = son.strip() == "2#1", soff.strip() == "2#0"
            texto.append(f"{ent} = 1 -> {sal} = {son}  {'OK' if bien_on else 'FALLO (se esperaba 2#1)'}")
            texto.append(f"{ent} = 0 -> {sal} = {soff}  {'OK' if bien_off else 'FALLO (se esperaba 2#0)'}")
            texto.append(f"   (antes de la prueba: {ent} = {eini}, {sal} = {sini})")
            ok_total &= bien_on and bien_off
    vistos = {f[0] for f in filas}
    if p.vida and "vida" not in vistos or p.bools and sum(f[0] == "bool" for f in filas) < len(p.bools):
        texto.append("Faltan comprobaciones en prueba.csv: CAP se paró a mitad (revisa scb.log).")
        ok_total = False
    return ok_total, texto


def escribir_scb(ruta_scb: Path, paquete: Path, proyecto: Path, log: Path,
                 login: bool, flash: bool, prueba: PruebaHumo | None = None,
                 csv_prueba: Path | None = None) -> None:
    lineas = [
        "REM Generado por subir_al_plc.py",
        f"scb_log_file {log} clear",
        f'project_unpack "{paquete}" "{proyecto}"',
    ]
    if login:
        lineas += [
            "REM login con la conexión guardada en el proyecto",
            "online_mode_on",
            "online_download flash" if flash else "online_download",
        ]
        if prueba and not prueba.vacia():
            lineas += lineas_prueba(prueba, csv_prueba)
        lineas.append("online_mode_off")
    # project_close guardaría cambios: se sale directamente sin guardar
    lineas.append("project_quit errlog notsave")
    ruta_scb.write_text("\n".join(lineas) + "\n", encoding="latin-1")


def lanzar_cap(sc: Path, scb: Path, tiempo_max: int, salida: Path) -> tuple[int | None, float]:
    """Lanza sc.exe /batch y espera. Devuelve (código de salida o None si se cortó, segundos)."""
    inicio = time.monotonic()
    with open(salida, "w", encoding="utf-8", errors="replace") as f:
        proc = subprocess.Popen([str(sc), "/batch", str(scb), "batch"],
                                cwd=sc.parent, stdout=f, stderr=subprocess.STDOUT)
        try:
            codigo = proc.wait(timeout=tiempo_max)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
            codigo = None
    return codigo, time.monotonic() - inicio


@dataclass
class AnalisisLog:
    login_ok: bool = False
    descarga: str = ""                  # qué dijo CAP tras online_download
    simulador: bool = False             # conectado a SIM1131: la descarga no hace nada
    errores: list[str] = field(default_factory=list)       # errores que no sabemos explicar
    explicados: list[str] = field(default_factory=list)    # errores conocidos que no impiden subir
    n_explicados: int = 0               # cuántos de los errores que cuenta CAP son conocidos
    motivo_login: str = ""              # lo que devolvió CAP en el último login fallido


# Errores que CAP cuenta en su código de salida pero que no impiden subir el programa
SVN_SIN_TORTOISE = "SCC_E_TORTOISE_NOT_FOUND"


def analizar_log(texto: str) -> AnalisisLog:
    """Lee scb.log línea a línea y separa errores reales de los conocidos."""
    a = AnalisisLog()
    lineas = [l.strip() for l in texto.splitlines()]
    login_fallos = 0
    tras_download = False
    for l in lineas:
        if SVN_SIN_TORTOISE in l:
            a.explicados.append("CAP no encuentra TortoiseSVN (CAP500.ini [VCS] SvnVersion=tortoise); "
                                "no afecta a la subida.")
            # Comprobado con un batch vacío (solo scb_log_file + project_quit) en CAP 6.2:
            # este fallo suma 2 al código de salida aunque en el log salga una sola línea.
            a.n_explicados += 2
            continue
        if "MAS_Login" in l:
            if "SUCCEEDED" in l:
                a.login_ok = True
            elif "FAILED" in l:
                login_fallos += 1
                # p. ej. «returned=0x800705b4=Timeout of a command is occurred»
                m = re.search(r"returned=(0x[0-9A-Fa-f]+)=?(.*)$", l)
                a.motivo_login = f"{m.group(2).strip()} ({m.group(1)})" if m else l
            continue
        if l.startswith("online_download"):
            tras_download = True
            continue
        if tras_download and l and not l.startswith("MAS-call"):
            a.descarga = l
            if "SIM1131" in l and "no effect" in l:
                a.simulador = True
            tras_download = False
        if re.match(r"(error|fatal)\b", l, re.IGNORECASE) or ("FAILED" in l and "MAS-call" in l):
            a.errores.append(l)
    if login_fallos and a.login_ok:
        # Es una línea de traza: CAP reintenta y no lo suma a su código de salida.
        a.explicados.append(f"El login falló {login_fallos} vez/veces antes de conectar "
                            "(normal si el simulador aún estaba arrancando).")
    elif login_fallos:
        a.errores.insert(0, f"Login fallido ({login_fallos} intento/s): {a.motivo_login}")
    return a


def leer_log(carpeta: Path) -> str:
    log = carpeta / "scb.log"
    return log.read_text(encoding="latin-1", errors="replace") if log.is_file() else ""


# --------------------------------------------------------------------------------------------
# 4. Programa
# --------------------------------------------------------------------------------------------

@dataclass
class Resultado:
    estado: str = "ERROR"            # OK | ERROR
    paso: str = ""                   # dónde se paró
    mensaje: str = ""
    paquete: dict = field(default_factory=dict)
    cap_version: str = ""
    sc_exe: str = ""
    login: bool = True
    flash: bool = True
    carpeta: str = ""
    proyecto_desempaquetado: str = ""
    codigo_cap: int | None = None
    segundos: float = 0.0
    errores_log: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    prueba_humo: list[str] = field(default_factory=list)
    inicio: str = ""
    fin: str = ""


def escribir_resultado(carpeta: Path, r: Resultado) -> None:
    r.fin = dt.datetime.now().isoformat(timespec="seconds")
    (carpeta / "resultado.json").write_text(json.dumps(asdict(r), indent=2, ensure_ascii=False),
                                            encoding="utf-8")
    p = r.paquete
    lineas = [
        f"{r.estado}" + (f" en el paso «{r.paso}»" if r.estado != "OK" else ""),
        r.mensaje,
        "",
        f"Paquete:      {p.get('ruta', '')}",
        f"Empaquetado:  {p.get('fecha_paquete', '')} por {p.get('empaquetado_por', '')}",
        f"CAP:          {r.cap_version}  ({r.sc_exe})",
        f"MOS:          {p.get('mos_version', '')}",
        f"Conexión:     {p.get('conexion', '') or 'desconocida'}",
        f"Login:        {'sí' if r.login else 'no (--sin-login)'}"
        + (f", grabar en flash: {'sí' if r.flash else 'no'}" if r.login else ""),
        f"Proyecto:     {r.proyecto_desempaquetado}",
        f"Código CAP:   {r.codigo_cap if r.codigo_cap is not None else '-'}   ({r.segundos:.0f} s)",
        f"Inicio / fin: {r.inicio} / {r.fin}",
    ]
    if r.prueba_humo:
        lineas += ["", "Prueba de humo:"] + [f"  {t}" for t in r.prueba_humo]
    if r.avisos:
        lineas += ["", "Avisos:"] + [f"  - {a}" for a in r.avisos]
    if r.errores_log:
        lineas += ["", "Errores en scb.log:"] + [f"  {e}" for e in r.errores_log[:30]]
    (carpeta / "resultado.txt").write_text("\n".join(lineas) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    return ejecutar(argv)[0]


def ejecutar(argv: list[str] | None = None) -> tuple[int, Path, Resultado]:
    """Lo mismo que main, pero devuelve también la carpeta y el resultado (lo usa vigilar_carpeta.py)."""
    ap = argparse.ArgumentParser(description="Sube al PLC un proyecto CAP1131 ya compilado (.pcz).")
    ap.add_argument("paquete", type=Path, help="paquete del proyecto (.pcz), con la IP ya guardada")
    ap.add_argument("--cap", default="", help="versión de CAP a usar (por defecto, la de PackInfo.txt)")
    ap.add_argument("--trabajo", type=Path, default=AQUI / "trabajo",
                    help="carpeta donde se crea una subcarpeta por ejecución")
    ap.add_argument("--sin-login", action="store_true",
                    help="solo desempaquetar y comprobar, sin conectar al PLC")
    ap.add_argument("--sin-flash", action="store_true", help="descargar sin grabar en la flash")
    ap.add_argument("--tiempo-max", type=int, default=600, help="segundos máximos para CAP (600)")
    ap.add_argument("--vida", default="", metavar="VARIABLE",
                    help="prueba de humo: variable que el programa cambia sola (se lee dos veces, 2 s)")
    ap.add_argument("--bool", nargs=2, action="append", default=[], metavar=("ENTRADA", "SALIDA"),
                    help="prueba de humo: pone ENTRADA a 1 y a 0 y comprueba que SALIDA la sigue "
                         "(se puede repetir)")
    ap.add_argument("--espera-ms", type=int, default=1000,
                    help="espera entre escribir la entrada y leer la salida (1000 ms)")
    a = ap.parse_args(argv)
    prueba = PruebaHumo(vida=a.vida, bools=[tuple(b) for b in a.bool], espera_ms=a.espera_ms)

    paquete = a.paquete.resolve()
    sello = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    carpeta = (a.trabajo / f"{sello}_{paquete.stem}").resolve()
    carpeta.mkdir(parents=True, exist_ok=True)
    r = Resultado(login=not a.sin_login, flash=not a.sin_flash, carpeta=str(carpeta),
                  inicio=dt.datetime.now().isoformat(timespec="seconds"))

    try:
        r.paso = "comprobar las rutas"
        for ruta in (paquete, carpeta):
            if "~" in str(ruta):
                raise ErrorBanco(f"CAP no admite rutas con «~» (las cambia por «%»): {ruta}. "
                                 "Usa la ruta larga o mueve el fichero.")

        r.paso = "leer el paquete"
        info = leer_paquete(paquete)
        r.paquete = asdict(info)
        r.avisos += info.avisos
        if not info.compilado:
            raise ErrorBanco("El paquete no trae la compilación (falta res.hex o res.dll). "
                             "Compílalo antes de empaquetarlo.")
        if r.login and not info.ip:
            raise ErrorBanco("El paquete no trae la IP del PLC, así que no se puede hacer login.")

        r.paso = "elegir CAP"
        r.cap_version, sc = elegir_cap(a.cap or info.cap_version)
        r.sc_exe = str(sc)
        if aviso := comprobar_mos(info.mos_version):
            r.avisos.append(aviso)

        if not prueba.vacia():
            r.paso = "comprobar las variables de la prueba"
            if not r.login:
                raise ErrorBanco("La prueba de humo necesita login: quita --sin-login.")
            varlist = variables_del_paquete(paquete)
            if not varlist:
                raise ErrorBanco("El paquete no trae VARLIST.CSV: no se pueden comprobar las variables.")
            faltan = [v for v in prueba.variables() if v.upper() not in varlist]
            if faltan:
                raise ErrorBanco("Estas variables no están en el VARLIST.CSV del paquete: " + ", ".join(faltan))
            no_bool = [v for par in prueba.bools for v in par if varlist[v.upper()] != "BOOL"]
            if no_bool:
                raise ErrorBanco("--bool solo admite variables BOOL: " + ", ".join(no_bool))

        r.paso = "preparar el batch"
        proyecto = carpeta / "proyecto"
        r.proyecto_desempaquetado = str(proyecto)
        scb = carpeta / "subir.scb"
        csv_prueba = carpeta / "prueba.csv"
        escribir_scb(scb, paquete, proyecto, carpeta / "scb.log", login=r.login, flash=r.flash,
                     prueba=prueba, csv_prueba=csv_prueba)

        print(f"Paquete {paquete.name}: CAP {r.cap_version}, MOS {info.mos_version}, "
              f"conexión {info.conexion or 'desconocida'}")
        print(f"Lanzando CAP ({'desempaquetar y subir' if r.login else 'solo desempaquetar'})...")
        r.paso = "CAP (desempaquetar, login y descarga)" if r.login else "CAP (desempaquetar)"
        antes = time.time()
        r.codigo_cap, r.segundos = lanzar_cap(sc, scb, a.tiempo_max, carpeta / "salida_sc.txt")
        # project_quit errlog deja scb_ok.log / scb_err.log en la carpeta de sc.exe: se copian aquí
        for nombre in ("scb_ok.log", "scb_err.log"):
            origen = sc.parent / nombre
            if origen.is_file() and origen.stat().st_mtime >= antes - 1:
                shutil.copy2(origen, carpeta / nombre)
        an = analizar_log(leer_log(carpeta))
        r.errores_log = an.errores
        r.avisos += an.explicados
        if an.simulador:
            r.avisos.append("Conectado al simulador SIM1131: CAP no descarga nada porque el simulador ya "
                            "carga el res.dll del proyecto. Al cerrar CAP, el simulador se para.")

        if r.codigo_cap is None:
            r.mensaje = (f"CAP no terminó en {a.tiempo_max} s y se ha cortado. Puede que esté esperando "
                         "en un cuadro de diálogo: revisa scb.log.")
            codigo = 3
        elif not (proyecto / "SOFTCTRL.PRO").is_file():
            r.paso = "desempaquetar"
            r.mensaje = "No aparece el proyecto desempaquetado. Revisa scb.log."
            codigo = 2
        elif r.login and not an.login_ok:
            r.paso = "login"
            r.mensaje = (f"No se pudo hacer login en {info.conexion}: "
                         f"{an.motivo_login or 'CAP no dio el motivo'}. Comprueba que el PLC está "
                         "encendido, que la IP es la correcta y que este PC llega a ella (ping).")
            codigo = 2
        elif an.errores or r.codigo_cap > an.n_explicados:
            r.mensaje = (f"CAP terminó con {r.codigo_cap} error(es) y solo {an.n_explicados} son conocidos. "
                         "Revisa scb.log.")
            codigo = 2
        elif not prueba.vacia() and not (bien := evaluar_prueba(csv_prueba, prueba))[0]:
            r.prueba_humo = bien[1]
            r.paso = "prueba de humo"
            r.mensaje = "El programa se subió pero la prueba de humo ha fallado (detalle abajo)."
            codigo = 4
        else:
            if not prueba.vacia():
                r.prueba_humo = evaluar_prueba(csv_prueba, prueba)[1]
            r.estado, r.paso = "OK", ""
            if not r.login:
                r.mensaje = "Paquete desempaquetado y comprobado (sin login)."
            elif an.simulador:
                r.mensaje = f"Login correcto en el simulador ({info.conexion}); no hace falta descargar."
            else:
                r.mensaje = f"Subido al PLC {info.ip} ({info.conexion}). CAP dijo: {an.descarga}"
            codigo = 0
    except ErrorBanco as e:
        r.mensaje = str(e)
        codigo = 1

    escribir_resultado(carpeta, r)
    print(f"{r.estado}: {r.mensaje}")
    for aviso in r.avisos:
        print(f"  Aviso: {aviso}")
    print(f"Resultado en {carpeta / 'resultado.txt'}")
    return codigo, carpeta, r


if __name__ == "__main__":
    sys.exit(main())
