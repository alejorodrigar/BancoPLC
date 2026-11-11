"""Crea las tareas programadas de Windows que lanzan vigilar_carpeta.py a las horas de banco.toml.

Una tarea por hora, llamadas «BancoPLC_HHMM». Antes de crearlas borra las BancoPLC_* que hubiera,
así que al cambiar `horas` en banco.toml basta con volver a ejecutar esto.

Las tareas solo corren con la sesión de este usuario iniciada (/IT): CAP1131 necesita escritorio.

Uso:
    python instalar_tarea.py                     (lee banco.toml junto al programa)
    python instalar_tarea.py --config otro.toml
    python instalar_tarea.py --mostrar           (enseña lo que haría, sin tocar Windows)
    python instalar_tarea.py --borrar            (borra todas las tareas BancoPLC_*)
"""

from __future__ import annotations

import argparse
import csv
import io
import subprocess
import sys
from pathlib import Path

from vigilar_carpeta import AQUI, leer_config

PREFIJO = "BancoPLC_"


def tareas_existentes() -> list[str]:
    salida = subprocess.run(["schtasks", "/Query", "/FO", "CSV", "/NH"], capture_output=True,
                            text=True, encoding="mbcs", errors="replace").stdout
    nombres = {fila[0].lstrip("\\") for fila in csv.reader(io.StringIO(salida)) if fila}
    return sorted(n for n in nombres if n.startswith(PREFIJO))


def ejecutar(cmd: list[str], mostrar: bool) -> bool:
    print("  " + subprocess.list2cmdline(cmd))
    if mostrar:
        return True
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="mbcs", errors="replace")
    if r.returncode != 0:
        print("    ERROR: " + (r.stderr or r.stdout).strip())
    return r.returncode == 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Crea las tareas programadas del vigilante.")
    ap.add_argument("--config", type=Path, help="fichero .toml (por defecto banco.toml junto al programa)")
    ap.add_argument("--mostrar", action="store_true", help="enseñar los comandos sin ejecutarlos")
    ap.add_argument("--borrar", action="store_true", help="solo borrar las tareas BancoPLC_*")
    a = ap.parse_args(argv)

    viejas = tareas_existentes()
    if viejas:
        print(f"Borrando {len(viejas)} tarea(s) anterior(es):")
    ok = all([ejecutar(["schtasks", "/Delete", "/TN", n, "/F"], a.mostrar) for n in viejas])
    if a.borrar:
        print("Hecho." if ok else "Hubo errores.")
        return 0 if ok else 1

    config = (a.config or AQUI / "banco.toml").resolve()
    if not config.is_file():
        print(f"No existe {config}. Copia banco.toml.ejemplo como banco.toml y ajusta las rutas y las horas.")
        return 1
    cfg = leer_config(config)

    # schtasks admite como mucho 261 caracteres en /TR: la orden completa va en un .bat propio de este PC
    lanzador = AQUI / "tarea_vigilante.bat"
    contenido = ("@echo off\r\n"
                 "REM Generado por instalar_tarea.py: lo lanza la tarea programada BancoPLC_*\r\n"
                 f'cd /d "{AQUI}"\r\n'
                 f'"{sys.executable}" vigilar_carpeta.py --config "{config}" >> salida_tarea.txt 2>&1\r\n')
    print(f"Lanzador: {lanzador}")
    if a.mostrar:
        print("  " + contenido.replace("\r\n", "\n  "))
    else:
        lanzador.write_bytes(contenido.encode("mbcs"))
    accion = f'"{lanzador}"'
    print(f"Creando {len(cfg['horas'])} tarea(s), todos los días a las {', '.join(cfg['horas'])}:")
    for hora in cfg["horas"]:
        nombre = PREFIJO + hora.replace(":", "")
        ok &= ejecutar(["schtasks", "/Create", "/F", "/TN", nombre, "/SC", "DAILY", "/ST", hora,
                        "/IT", "/TR", accion], a.mostrar)

    if a.mostrar:
        print("\n(--mostrar: no se ha cambiado nada en Windows)")
    elif ok:
        print("\nListo. Para lanzar una ahora:  schtasks /Run /TN " + PREFIJO + cfg["horas"][0].replace(":", ""))
        print("Para borrarlas todas:          python instalar_tarea.py --borrar")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
