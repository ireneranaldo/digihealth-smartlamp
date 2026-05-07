# DigiHealth Lamp

Sistema di monitoraggio ambientale indoor basato su Raspberry Pi. Legge sensori di qualità dell'aria, calcola l'IAQI (Indoor Air Quality Index), controlla una striscia LED NeoPixel e invia tutti i dati a InfluxDB. Include un'interfaccia web per il monitoraggio in tempo reale e la gestione del comfort acustico.

---

## Funzionalità principali

| Modulo | Descrizione |
|---|---|
| **Sensori** | ZPH01B (PM1/PM2.5/PM10, CO2, TVOC, CH2O, temperatura, umidità) via UART; BH1750 (lux) via I2C; microfono USB |
| **IAQI** | Calcolo Indice di Qualità dell'Aria Interna secondo breakpoint standard (PM2.5, CO2, TVOC, CH2O) |
| **NeoPixel** | Striscia 144 LED: pixel 0–79 visualizzano IAQI con effetto breathing, pixel 80–143 simulano la luce circadiana |
| **Audio comfort** | Monitoraggio livello sonoro, calibrazione automatica, riproduzione pink noise o file audio se la soglia viene superata |
| **Dashboard web** | Flask su porta 5000: grafici FFT in tempo reale, livello dB, qualità dell'aria, controllo volume e modalità |
| **InfluxDB** | Invio diretto a InfluxDB Cloud via `influxdb-client` (measurement `ZPHSensor_sensore`) |

---

## Hardware richiesto

- Raspberry Pi 4 (o superiore)
- Sensore ZPH01B collegato a `/dev/serial0` (UART, 9600 baud)
- Sensore BH1750 collegato a I2C bus 1, indirizzo `0x23`
- Striscia NeoPixel (144 pixel) su GPIO 12
- Microfono USB (USB PnP Sound Device, `device_index: 1`)
- Connessione di rete (per InfluxDB e dashboard web)

---

## Installazione

### 1. Sistema operativo e dipendenze di sistema

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-dev python3-venv \
    portaudio19-dev libasound2-dev mpg123 git
```

### 2. Abilitare le interfacce hardware

```bash
sudo raspi-config
```

- **Interfacing Options → Serial Port** → disable login shell, enable serial hardware
- **Interfacing Options → I2C** → Enable
- Riavviare: `sudo reboot`

### 3. Clonare il repository

```bash
cd /home/digip
git clone https://github.com/yourusername/digihealth-smartlamp.git digihealth-lamp
cd digihealth-lamp
```

### 4. Creare e attivare l'ambiente virtuale

```bash
python3 -m venv venv
source venv/bin/activate
```

### 5. Installare il pacchetto

```bash
pip install --upgrade pip
pip install -e .
```

### 6. Configurare il servizio systemd

Aggiornare il file di servizio con i percorsi corretti, poi installarlo:

```bash
# Verifica che User e WorkingDirectory in systemd/digihealth-lamp.service
# corrispondano al tuo utente (es. digip) e alla cartella del progetto.

sudo cp systemd/digihealth-lamp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable digihealth-lamp
sudo systemctl start digihealth-lamp
```

### 7. Verificare

```bash
sudo systemctl status digihealth-lamp
sudo journalctl -u digihealth-lamp -f
```

---

## Configurazione

Il file principale è `config/default.yaml`. Su Windows viene caricato automaticamente `config/windows.yaml`. È possibile sovrascrivere il file con la variabile d'ambiente `DIGIHEALTH_CONFIG`.

### Parametri chiave

```yaml
sensors:
  zph:
    port: "/dev/serial0"     # Porta UART del sensore ZPH01B
  microphone:
    device_index: 1          # Indice microfono USB (verificare con arecord -l)

processors:
  audio_comfort:
    tolerance_threshold: 45.0   # dB sopra cui si entra in CHECK
    critical_threshold: 55.0    # dB sopra cui parte il comfort audio
    check_duration: 10          # secondi di monitoraggio prima di intervenire
    comfort_duration: 300       # secondi di riproduzione audio comfort

actuators:
  neopixel:
    pin: 12
    num_pixels: 144
    iaqi_range: [0, 79]         # pixel per IAQI
    circadian_range: [80, 143]  # pixel per luce circadiana

communicator:
  telegraf:
    measurement: "ZPHSensor_sensore"
    tags:
      sensor: "ZPHS01B"
      host: "raspberry01"       # identificativo del dispositivo

web:
  enabled: true
  port: 5000
```

### Verificare il device index del microfono

```bash
arecord -l
# Cerca "USB PnP Sound Device" e usa Card X, Device Y → device_index: X
```

---

## Utilizzo

### Avvio tramite servizio (produzione)

```bash
sudo systemctl start digihealth-lamp
```

### Avvio manuale (test/debug)

```bash
sudo systemctl stop digihealth-lamp   # ferma il servizio prima
source venv/bin/activate
sudo ./venv/bin/python3 -m digihealth.main
# CTRL+C per fermare, non CTRL+Z
```

### Dashboard web

Apri `http://<ip-raspberry>:5000` nel browser.

La dashboard mostra:
- Livello sonoro in dB e spettro FFT in tempo reale
- Temperatura, umidità, CO2 e IAQI
- Pulsanti: calibra microfono, avvia/ferma monitoraggio, seleziona modalità comfort
- Slider volume

---

## Struttura del progetto

```
digihealth-lamp/
├── digihealth/
│   ├── main.py                  # Entry point, loop principale (ciclo 30s)
│   ├── config.py                # Caricamento e validazione config (Pydantic)
│   ├── logger.py                # Logger strutturato
│   ├── audio_worker.py          # Processo separato per PyAudio
│   ├── sensors/
│   │   ├── base.py              # Classe base astratta
│   │   ├── zph.py               # Sensore ZPH01B (UART)
│   │   ├── light.py             # Sensore BH1750 (I2C)
│   │   └── microphone.py        # Acquisizione audio
│   ├── processors/
│   │   ├── iaqi.py              # Calcolo IAQI
│   │   └── audio_comfort.py     # State machine comfort acustico
│   ├── actuators/
│   │   └── neopixel_controller.py  # Controllo striscia LED
│   ├── communicator/
│   │   └── telegraf_client.py   # Invio dati a InfluxDB
│   └── web/
│       ├── __init__.py          # Flask app e route API
│       └── templates/
│           └── dashboard.html
├── config/
│   ├── default.yaml             # Configurazione Raspberry Pi
│   └── windows.yaml             # Configurazione Windows (sviluppo)
├── systemd/
│   └── digihealth-lamp.service  # Unit file systemd
├── audio/
│   ├── low-pink-noise.mp3
│   └── wind-chimes-and-light-rain.mp3
├── docker/
│   └── Dockerfile
├── tests/
│   └── test_basic.py
├── requirements.txt
├── setup.py
└── GUIDA_SERVIZIO.txt
```

### Flusso dati

```
ZPH01B (UART) ──┐
BH1750  (I2C) ──┤→ SensorManager → ProcessorManager (IAQI, AudioComfort)
Microfono USB ──┘                          │
                              ┌────────────┼─────────────┐
                              ↓            ↓             ↓
                         InfluxDB     NeoPixel LED    Dashboard
                         (Cloud)      (GPIO 12)       (Flask :5000)
```

---

## Troubleshooting

| Problema | Soluzione |
|---|---|
| `Serial: no such device /dev/serial0` | Abilitare UART in `raspi-config`, disabilitare console seriale |
| `I2C error` / sensore luce non trovato | Verificare con `i2cdetect -y 1`; deve apparire `0x23` |
| LED NeoPixel non si accendono | GPIO 12 richiede permessi root; verificare il cablaggio |
| Microfono non trovato | Verificare `device_index` con `arecord -l`; usare `null` per auto-detect |
| Dashboard non raggiungibile | Verificare che `web.enabled: true` in config e che la porta 5000 sia aperta |
| InfluxDB: errore autenticazione | Verificare token e URL in `communicator/telegraf_client.py` |

---

## Test

```bash
source venv/bin/activate
pytest tests/
```

---

## Licenza

MIT License
