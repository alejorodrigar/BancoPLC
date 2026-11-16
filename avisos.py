"""Avisos por correo del vigilante: un correo por pasada con lo que se ha subido o ha fallado.

Dos formas de enviar (clave `metodo` de la sección [correo] de banco.toml):
    'outlook'  usa el Outlook instalado en el PC, con la cuenta del usuario (sin servidor ni contraseña).
               Se maneja por COM desde PowerShell, así que no hace falta ninguna librería.
    'smtp'     se conecta a un servidor de correo (servidor, puerto, seguridad, usuario, contraseña).
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import smtplib
import socket
import subprocess
import tempfile
from email.message import EmailMessage
from pathlib import Path

POR_DEFECTO = {
    "metodo": "no",              # 'outlook', 'smtp' o 'no'
    "destinatarios": [],         # ['persona@empresa.com', 'grupo@empresa.com']
    "cuando": "subidas",         # 'subidas' (OK o ERROR), 'errores' (solo si algo falla) o 'siempre'
    "asunto": "BancoPLC",        # prefijo del asunto
    # solo para metodo = 'smtp'
    "servidor": "",
    "puerto": 25,
    "seguridad": "no",           # 'no', 'starttls' o 'ssl'
    "usuario": "",
    "contrasena": "",
    "remitente": "",
}
METODOS = ("no", "outlook", "smtp")
CUANDO = ("subidas", "errores", "siempre")


class ErrorCorreo(Exception):
    pass


def validar(datos: dict | None) -> dict:
    cfg = dict(POR_DEFECTO)
    if datos:
        if not isinstance(datos, dict):
            raise SystemExit("[correo] tiene que ser una sección: escribe [correo] en una línea y debajo sus claves.")
        desconocidas = set(datos) - set(POR_DEFECTO)
        if desconocidas:
            raise SystemExit(f"Claves desconocidas en [correo]: {', '.join(sorted(desconocidas))}")
        cfg.update(datos)
    if isinstance(cfg["destinatarios"], str):
        cfg["destinatarios"] = [cfg["destinatarios"]]
    cfg["destinatarios"] = [d.strip() for d in cfg["destinatarios"] if str(d).strip()]
    if cfg["metodo"] not in METODOS:
        raise SystemExit(f"[correo] metodo = {cfg['metodo']!r}: tiene que ser {', '.join(map(repr, METODOS))}.")
    if cfg["cuando"] not in CUANDO:
        raise SystemExit(f"[correo] cuando = {cfg['cuando']!r}: tiene que ser {', '.join(map(repr, CUANDO))}.")
    if cfg["metodo"] != "no" and not cfg["destinatarios"]:
        raise SystemExit("[correo] no tiene destinatarios: destinatarios = ['persona@empresa.com']")
    if cfg["metodo"] == "smtp" and not cfg["servidor"]:
        raise SystemExit("[correo] metodo = 'smtp' necesita servidor = 'smtp.empresa.com'")
    if cfg["seguridad"] not in ("no", "starttls", "ssl"):
        raise SystemExit("[correo] seguridad tiene que ser 'no', 'starttls' o 'ssl'.")
    return cfg


def hay_que_enviar(cfg: dict, subidas: int, errores: int) -> bool:
    if cfg["metodo"] == "no":
        return False
    return {"siempre": True, "subidas": subidas > 0 or errores > 0, "errores": errores > 0}[cfg["cuando"]]


def preparar_adjuntos(adjuntos: list[tuple[str, Path]], carpeta: Path) -> list[Path]:
    """Copia los adjuntos a una carpeta temporal con nombre único (varios paquetes tienen scb.log)."""
    res = []
    for nombre, ruta in adjuntos:
        if ruta.is_file():
            destino = carpeta / nombre
            shutil.copy2(ruta, destino)
            res.append(destino)
    return res


def enviar(cfg: dict, asunto: str, cuerpo: str, adjuntos: list[tuple[str, Path]]) -> None:
    """Envía el correo. `adjuntos` son pares (nombre en el correo, ruta). Lanza ErrorCorreo si falla."""
    with tempfile.TemporaryDirectory(prefix="bancoplc_correo_") as tmp:
        ficheros = preparar_adjuntos(adjuntos, Path(tmp))
        if cfg["metodo"] == "outlook":
            _por_outlook(cfg, asunto, cuerpo, ficheros, Path(tmp))
        elif cfg["metodo"] == "smtp":
            _por_smtp(cfg, asunto, cuerpo, ficheros)


# Script de PowerShell: lee los datos de un JSON y envía con el Outlook del usuario.
# Tras Send() fuerza un envío/recepción y espera un poco: si Outlook estaba cerrado y lo ha abierto
# este script, al terminar se cerraría con el correo todavía en la Bandeja de salida.
_PS_OUTLOOK = r"""
$ErrorActionPreference = 'Stop'
$d = [IO.File]::ReadAllText('__JSON__', [Text.Encoding]::UTF8) | ConvertFrom-Json
$o = New-Object -ComObject Outlook.Application
$m = $o.CreateItem(0)
$m.To = $d.para
$m.Subject = $d.asunto
$m.Body = $d.cuerpo
foreach ($a in $d.adjuntos) { [void]$m.Attachments.Add($a) }
$m.Send()
try { $o.Session.SendAndReceive($false) } catch {}
Start-Sleep -Seconds 10
"""


def _por_outlook(cfg: dict, asunto: str, cuerpo: str, ficheros: list[Path], tmp: Path) -> None:
    datos = tmp / "correo.json"
    datos.write_text(json.dumps({"para": "; ".join(cfg["destinatarios"]), "asunto": asunto,
                                 "cuerpo": cuerpo, "adjuntos": [str(f) for f in ficheros]},
                                ensure_ascii=False), encoding="utf-8")
    script = _PS_OUTLOOK.replace("__JSON__", str(datos).replace("'", "''"))
    # -EncodedCommand: no depende de la directiva de ejecución de scripts (.ps1) del PC
    codificado = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", codificado],
                           capture_output=True, text=True, timeout=120,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ErrorCorreo(f"no se pudo lanzar PowerShell para Outlook: {e}") from e
    if p.returncode != 0:
        detalle = (p.stderr or p.stdout).strip().splitlines()
        raise ErrorCorreo("Outlook no pudo enviar el correo: " + (detalle[0] if detalle else f"código {p.returncode}"))


def _por_smtp(cfg: dict, asunto: str, cuerpo: str, ficheros: list[Path]) -> None:
    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = cfg["remitente"] or cfg["usuario"] or f"bancoplc@{socket.gethostname()}"
    msg["To"] = ", ".join(cfg["destinatarios"])
    msg.set_content(cuerpo)
    for f in ficheros:
        msg.add_attachment(f.read_bytes(), maintype="text", subtype="plain", filename=f.name)
    try:
        clase = smtplib.SMTP_SSL if cfg["seguridad"] == "ssl" else smtplib.SMTP
        with clase(cfg["servidor"], int(cfg["puerto"]), timeout=60) as s:
            if cfg["seguridad"] == "starttls":
                s.starttls()
            if cfg["usuario"]:
                s.login(cfg["usuario"], cfg["contrasena"])
            s.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        raise ErrorCorreo(f"el servidor {cfg['servidor']}:{cfg['puerto']} no aceptó el correo: {e}") from e


def nombre_pc() -> str:
    return os.environ.get("COMPUTERNAME") or socket.gethostname()
