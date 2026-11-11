# BancoPLC

Programas para dejar un PC de la red probando proyectos CAP1131 en los PLC sin nadie delante.
Se construye por partes; cada parte funciona sola y al final se encadenan.

| # | Parte | Estado |
|---|---|---|
| 1 | Subir al PLC un proyecto ya compilado (`subir_al_plc.py`) | en pruebas |
| 2 | Compilar con CAP batch y subir | pendiente |
| 3 | Lanzar proyectos de POUtest (`POUtest1131.Console`) | pendiente |
| 4 | Lanzar secuenciales de TestStand | pendiente |
| 5 | Vigilante de la carpeta compartida (entrada, en curso, resultados, errores) | pendiente |
| 6 | Registro, resumen y avisos | pendiente |
| 7 | Instalación en el PC del banco (tarea programada) | pendiente |

## Parte 1: `subir_al_plc.py`

Recibe el paquete del proyecto (`.pcz`, el que crea CAP con *Extras → Pack*), ya compilado y con la IP del PLC
guardada, y con CAP en modo batch lo desempaqueta, hace login y lo descarga.

```bash
python subir_al_plc.py C:\ruta\LRV_043_v00_03_04.pcz
python subir_al_plc.py C:\ruta\LRV_043_v00_03_04.pcz --sin-login
```

Opciones:

- `--sin-login`: solo desempaqueta y comprueba; no conecta con el PLC.
- `--sin-flash`: descarga sin grabar en la flash (`online_download` en lugar de `online_download flash`).
- `--cap 6.2`: fuerza una versión de CAP. Por defecto se usa la del `PackInfo.txt` del paquete.
- `--trabajo CARPETA`: dónde se crean las carpetas de cada ejecución (por defecto `trabajo\` junto al programa).
- `--tiempo-max 600`: segundos máximos para CAP; si se pasa, se corta (puede haberse quedado en un diálogo).

Qué lee del paquete sin desempaquetarlo:

- `PackInfo.txt`: versión de CAP (`CAPVersion:`) y de MOS (`MOSVersion:`).
- La IP: en CAP 5.12 y 6.2 está en `selectron.ini` (`[Param] Accesspath=LAN,192.168.0.180`);
  en CAP 6.5 en `user.ini` (`[Online] ConnectionInfoString={"host":...}`).
- Si está compilado: `res.hex` (PLC real) o `res.dll` (simulador).

Cada ejecución deja en `trabajo\<fecha>_<paquete>\`:

- `resultado.txt` y `resultado.json`: OK o ERROR, en qué paso, conexión, versión de CAP y avisos.
- `subir.scb`: el batch que se ha lanzado.
- `scb.log` (y `scb_ok.log` o `scb_err.log`): el registro de CAP.
- `salida_sc.txt`: lo que haya escrito `sc.exe` por consola.
- `proyecto\`: el proyecto desempaquetado.

Códigos de salida: 0 bien, 1 error antes de lanzar CAP, 2 CAP con errores, 3 CAP superó el tiempo máximo.

El batch que se genera:

```
scb_log_file <carpeta>\scb.log clear
project_unpack "<paquete.pcz>" "<carpeta>\proyecto"
online_mode_on
online_download flash
online_mode_off
project_quit errlog notsave
```

### Prueba de humo (opcional)

Con el PLC conectado, antes de salir, comprueba que el programa funciona. Solo usa `online_monitor_read` y
`online_monitor_write`; los valores los escribe CAP en `prueba.csv` y Python los compara.

- `--vida VARIABLE`: lee la variable dos veces separadas 2 s; debe cambiar (el programa está corriendo).
- `--bool ENTRADA SALIDA`: pone ENTRADA a 1, espera y comprueba SALIDA = 1; la pone a 0 y comprueba SALIDA = 0.
  Se puede repetir. `--espera-ms` cambia la espera (1000 ms).

Antes de lanzar CAP se comprueba que las variables existen en el `VARLIST.CSV` del paquete.

```bash
python subir_al_plc.py C:\POUtest\043-Lausanne\lrv_043_v00_03_04_sim_11112026.pcz --vida VC.TCMS_Y_LIFE_SIGNAL_10201 --bool KS1R2.VC_B_RFIDCABA CAB.B_CABKEY1
```

Resultado el 2026-11-11 con el simulador: señal de vida 55 → 78 (OK); RFIDCABA = 1 → CABKEY1 = 1 (OK);
RFIDCABA = 0 → CABKEY1 = 0 (OK).

Errores que CAP cuenta en su código de salida pero que no impiden subir (salen como avisos y el resultado es OK):

- `SCC_E_TORTOISE_NOT_FOUND`: CAP no encuentra TortoiseSVN (`CAP500.ini` `[VCS] SvnVersion=tortoise`).
  Suma **2** al código de salida aunque en el log salga una línea (comprobado con un batch vacío en CAP 6.2).
- Un login que falla y luego conecta (con el simulador, el primer intento llega antes de que arranque).
  Es una línea de traza y CAP no la suma a su código.

Para saber si viene compilado se mira la última línea «N errors» de `ErrorLog.txt` y `CompileReport.txt`.

Las rutas no pueden llevar `~` (nombres cortos de Windows como `ALEJAN~1`): CAP lo cambia por `%` y falla.

Con el simulador (`localhost`), CAP arranca MosSim él solo, `online_download` no hace nada (el simulador ya
carga el `res.dll`) y al salir CAP el simulador se para.

Probado el 2026-11-11 con `lrv_043_v00_03_04_sim_11112026.pcz` (CAP 6.2, simulador): OK en 27 s.

Si el PLC no responde (probado con la IP 192.0.2.1, que nunca contesta): CAP intenta el login una vez,
espera `LOGINTIMEOUT` del `selectron.ini` (5000 ms en el 043), da `0x800705b4 Timeout of a command is occurred`,
aborta el batch y se cierra solo (17 s en total, sin cuadros de diálogo). El resultado es ERROR en el paso «login»
con ese motivo.

Requisitos: Python 3.11 o superior, sin librerías externas, y CAP1131 instalado en `C:\Selectron\CAP1131`.
