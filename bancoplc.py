"""Punto de entrada único para el .exe (BancoPLC.exe). Reparte a los tres programas:

    BancoPLC.exe vigilar   [--config banco.toml] [--simulacro]       -> vigilar_carpeta.py
    BancoPLC.exe instalar  [--config banco.toml] [--mostrar|--borrar] -> instalar_tarea.py
    BancoPLC.exe subir     paquete.pcz [opciones]                     -> subir_al_plc.py

Construir el .exe (en un Python con PyInstaller):
    py -3.13 -m PyInstaller --onefile --console --name BancoPLC bancoplc.py
"""

import sys

import instalar_tarea
import subir_al_plc
import vigilar_carpeta

ORDENES = {
    "vigilar": (vigilar_carpeta.main, "revisa la carpeta de entrada y sube lo que haya cambiado"),
    "instalar": (instalar_tarea.main, "crea las tareas programadas a las horas de banco.toml"),
    "subir": (subir_al_plc.main, "sube un .pcz concreto al PLC"),
}


def ayuda() -> int:
    print("Uso: BancoPLC.exe <orden> [opciones]\n")
    for nombre, (_, texto) in ORDENES.items():
        print(f"  {nombre:9} {texto}")
    print("\nAyuda de cada orden: BancoPLC.exe <orden> --help")
    return 1


def main() -> int:
    # Salida en UTF-8 para que las tildes no se rompan en salida_tarea.txt
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if len(sys.argv) < 2 or sys.argv[1] not in ORDENES:
        return ayuda()
    funcion = ORDENES[sys.argv[1]][0]
    sys.argv = [f"BancoPLC.exe {sys.argv[1]}"] + sys.argv[2:]   # para que --help enseñe el nombre bueno
    return funcion(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
