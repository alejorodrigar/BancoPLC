# BancoPLC

Programas para dejar un PC de la red probando proyectos CAP1131 en los PLC sin nadie delante.
Se construye por partes; cada parte funciona sola y al final se encadenan.

| # | Parte | Estado |
|---|---|---|
| 1 | Subir al PLC un proyecto ya compilado (`subir_al_plc.py`) | probado con simulador; falta PLC real |
| 2 | Compilar con CAP batch y subir | pendiente |
| 3 | Lanzar proyectos de POUtest (`POUtest1131.Console`) | pendiente |
| 4 | Lanzar secuenciales de TestStand | pendiente |
| 5 | Vigilante diario de una carpeta de `.pcz` (`vigilar_carpeta.py`) | probado con simulador; falta PLC real |
| 6 | Registro, resumen y avisos | registro CSV y log diario hechos; avisos pendientes |
| 7 | Instalación en el PC del banco (`instalar_tarea.bat` / `instalar_tarea.py`, horas en `banco.toml`) | probado con `--mostrar`; sin crear tareas reales |

## Vigilante diario: `vigilar_carpeta.py`

Cada vez que se ejecuta revisa los `.pcz` de una carpeta y sube al PLC solo lo que ha cambiado:

1. Lee cada `.pcz` (sin desempaquetarlo) para saber a qué IP va y calcula su huella SHA-256.
2. Si hay varios `.pcz` para la misma IP, se queda con el más reciente y avisa de los demás.
3. Si la última subida **OK** a esa IP fue de un paquete con la misma huella: `SIN CAMBIOS`, no sube nada.
   Si no (nuevo, distinto o el último intento falló), lo sube con `subir_al_plc.py`.
4. Apunta cada decisión en `registro\registro_subidas.csv`
   (`fecha;hora;paquete;sha256;ip;cap;resultado;mensaje;carpeta`) y en `registro\vigilante_<fecha>.log`.

Los `.pcz` modificados hace menos de `minutos_copia` minutos se dejan para la próxima vez, por si se están copiando.
Un fichero `registro\vigilante.lock` impide que corran dos vigilantes a la vez.

Solo sabe lo que hay en el PLC por su propio registro: si alguien carga otro programa a mano, no se entera
(no hay comando batch documentado que diga qué proyecto tiene cargado el PLC).

```bash
copy banco.toml.ejemplo banco.toml       (y cambiar las rutas y las horas)
python vigilar_carpeta.py --simulacro    (decide y apunta, sin lanzar CAP)
python vigilar_carpeta.py
instalar_tarea.bat --mostrar             (enseña las tareas que crearía, sin tocar Windows)
instalar_tarea.bat                       (crea una tarea diaria por cada hora de banco.toml)
instalar_tarea.bat --borrar              (borra todas las tareas BancoPLC_*)
```

Las horas van en `banco.toml`, por ejemplo `horas = ['06:00', '14:00']`. `instalar_tarea.py` crea una tarea
`BancoPLC_HHMM` por hora, borrando antes las que hubiera, así que tras cambiar las horas basta con volver a
ejecutarlo. Las tareas usan `/IT`: solo corren con la sesión del usuario iniciada, porque CAP necesita escritorio.
Como `schtasks` admite como mucho 261 caracteres de orden, el instalador genera `tarea_vigilante.bat`
(con la ruta de Python y del `banco.toml` de ese PC) y la tarea lanza ese fichero; lo que escribe va a `salida_tarea.txt`.

Probado el 2026-11-11 en local con el simulador: 1.ª pasada OK, 2.ª `SIN CAMBIOS`; con un paquete a una IP que
no responde, ERROR y reintento en la siguiente pasada; con dos paquetes para la misma IP, se usa el más reciente.

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
