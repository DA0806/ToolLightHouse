# Nano clásico y nueve indicadores

El firmware está en `firmware/nano/nano.ino`. Elegir **Arduino Nano / ATmega328P**, no Nano Every ni ESP32. Algunos clones requieren seleccionar **ATmega328P (Old Bootloader)** en Arduino IDE. Cerrar el monitor serial antes de abrir el backend. Seleccionar explícitamente el COM correcto; no adivinar entre varios puertos.

| LED / bit | Pin |
| --- | --- |
| 1 / 0 | D2 |
| 2 / 1 | D3 |
| 3 / 2 | D4 |
| 4 / 3 | D5 |
| 5 / 4 | D6 |
| 6 / 5 | D7 |
| 7 / 6 | D8 |
| 8 / 7 | D9 |
| 9 / 8 | D10 |

Cada LED discreto necesita su propia resistencia: pin → resistencia → ánodo LED; cátodo → GND. Como punto de partida, 470 Ω a 5 V con caída de LED de 2 V produce aproximadamente `(5−2)/470 = 6.4 mA`; verificar caída y corriente del LED real. Nueve a ese valor representan unos 58 mA, además del consumo del microcontrolador. El [pinout oficial del Nano](https://content.arduino.cc/assets/Pinout-NANO_latest.pdf) especifica 20 mA máximos por I/O. Revisar también límites acumulados por grupos de puertos y alimentación en el datasheet de la placa/microcontrolador; no diseñar al límite absoluto. D0/D1 quedan reservados para serial.

Tiras, módulos o indicadores potentes requieren driver/transistor/MOSFET y fuente adecuada con masa común; no conectar cargas de potencia directamente a GPIO. Validar circuito, consumo y temperatura antes de la demo.

## Contrato serial
115200 baudios, ASCII, newline. El host envía `MASK:0` a `MASK:511`; Nano aplica el estado completo y responde `ACK:N`. Dos herramientas iguales activan un mismo bit. Retirar una instancia no apaga la categoría si permanece otra instancia confirmada.

Al abrir USB el Nano normalmente reinicia: esperar arranque y sincronizar cero antes de operar. Un ACK de otra máscara no confirma el comando actual. El estado solicitado en el dashboard se distingue del último estado confirmado. Un fallo de comunicación impide afirmar que las luces físicas están apagadas; el watchdog del firmware apaga a los 2500 ms sin mensajes válidos. La reconexión sincroniza cero antes de restaurar una escena vigente.

## Validación física pendiente
1. Cargar firmware; verificar `MASK:0`, `1`, `7`, `511` y cada bit individual con su ACK.
2. Medir corriente de un LED y del conjunto. Verificar pines/resistencias/polaridad.
3. Cortar backend, retirar USB y reconectar; medir el tiempo real de apagado y verificar sincronización cero.
4. Usar inventario definitivo de nueve nombres/ubicaciones y asignaciones únicas antes de habilitar modo real.
5. Repetir prueba de retiro parcial y dos instancias iguales con herramientas reales.

Las pruebas con transporte serial falso y ACK simulado sólo validan software. La compilación del firmware tampoco prueba conexiones, alimentación ni luces.
