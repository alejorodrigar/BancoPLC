# Instalar BancoPLC en el PC del banco

No hace falta Python: todo va dentro de `BancoPLC.exe`.

## 1. Comprobar lo que ya tiene que haber en el PC

- CAP1131 en `C:\Selectron\CAP1131\`, con las versiones de vuestros paquetes (5.12, 6.2, 6.5).
- Los MOS de los proyectos en `C:\Selectron\MOS\`.
- Que llega a los PLC: `ping 192.168.0.180` (por ejemplo).
- Que CAP abre el proyecto y hace login **a mano** al menos una vez. Si a mano no va, el programa tampoco.

## 2. Copiar el programa

Crea una carpeta, por ejemplo `C:\BancoPLC`, y copia dentro:

- `BancoPLC.exe` (está en la carpeta `exe\` del repositorio)
- `banco.toml.ejemplo`

Y crea las carpetas de trabajo:

```
C:\BancoPLC\entrada     <- aquí se dejan los .pcz
C:\BancoPLC\trabajo
C:\BancoPLC\registro
```

Si la entrada es una carpeta de red, usa la ruta `\\servidor\carpeta`, no una letra de unidad
(la tarea programada no ve las unidades de red asignadas).

## 3. Configurar

Copia `banco.toml.ejemplo` como `banco.toml`, **junto a `BancoPLC.exe`**, y ajusta rutas y horas
(con comillas simples):

```toml
entrada  = 'C:\BancoPLC\entrada'
trabajo  = 'C:\BancoPLC\trabajo'
registro = 'C:\BancoPLC\registro'
horas    = ['06:00']
```

## 4. Probar a mano

Deja un `.pcz` en `entrada` y, en una consola dentro de `C:\BancoPLC`:

```
BancoPLC.exe vigilar --simulacro      decide qué haría, sin tocar el PLC
BancoPLC.exe vigilar                  lo sube de verdad
BancoPLC.exe vigilar                  la segunda vez tiene que decir SIN CAMBIOS
```

Mira `trabajo\<fecha>_<paquete>\resultado.txt` y `registro\registro_subidas.csv`.

## 5. Programar la tarea

```
BancoPLC.exe instalar --mostrar       enseña las tareas que crearía, sin tocar Windows
BancoPLC.exe instalar                 crea una tarea diaria por cada hora de banco.toml
schtasks /Run /TN BancoPLC_0600       para lanzarla ya, sin esperar a la hora
```

Si cambias las horas en `banco.toml`, vuelve a ejecutar `BancoPLC.exe instalar`.
Para quitarlas: `BancoPLC.exe instalar --borrar`.

## 6. Dejar el PC preparado

- La tarea solo corre con la **sesión del usuario iniciada** (CAP necesita escritorio). Puede estar bloqueada,
  pero no cerrada. Lo ideal es un usuario con inicio de sesión automático.
- Que no se suspenda ni se apague a esas horas.
- Que Windows Update no reinicie cerca de la hora de la tarea.

## 7. Día a día

- Dejáis el `.pcz` nuevo en `entrada`: a la hora programada se sube solo si ha cambiado.
- Qué ha pasado: `registro\registro_subidas.csv` y `registro\vigilante_<fecha>.log`.
- Lo que escribe la tarea programada va a `salida_tarea.txt`, junto a `BancoPLC.exe`.

## Subir un paquete concreto a mano

```
BancoPLC.exe subir C:\ruta\paquete.pcz
BancoPLC.exe subir C:\ruta\paquete.pcz --sin-login          solo desempaqueta y comprueba
BancoPLC.exe subir --help                                   todas las opciones
```
