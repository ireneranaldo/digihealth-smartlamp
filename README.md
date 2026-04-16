# DigiHealth Lamp

Una lampada smart basata su Raspberry Pi per il monitoraggio ambientale e l'invio dati a InfluxDB tramite Telegraf.

## Caratteristiche

- **Sensori Ambientali**: ZPH01B (PM2.5, CO2, TVOC, temperatura, umidità), BH1750 (luminosità)
- **Calcolo IAQI**: Indice di Qualità dell'Aria Interna
- **Attuatori**: NeoPixel LED RGB, Shelly Smart Lamp
- **Dashboard Web**: Monitoraggio in tempo reale (opzionale)
- **Comunicazione**: InfluxDB via Telegraf
- **Modulare**: Estensioni opzionali caricabili dinamicamente

## Installazione su Raspberry Pi

### Prerequisiti

- Raspberry Pi (consigliato 4 o superiore)
- Raspbian OS
- Python 3.7+
- Connessioni hardware:
  - ZPH01B collegato a `/dev/serial0` (UART)
  - BH1750 collegato a I2C bus 1, indirizzo 0x23
  - NeoPixel collegati a GPIO 12
  - Shelly lamp accessibile via HTTP

### Passi di Installazione

1. **Clona il repository**:
   ```bash
   git clone https://github.com/yourusername/digihealth-lamp.git
   cd digihealth-lamp
   ```

2. **Installa le dipendenze di sistema**:
   ```bash
   sudo apt update
   sudo apt install python3-pip python3-dev
   ```

3. **Abilita interfacce hardware**:
   ```bash
   sudo raspi-config
   # Interfacing Options > I2C > Enable
   # Interfacing Options > Serial > Enable, disable console
   ```

4. **Installa il package**:
   ```bash
   sudo pip3 install -e .
   ```

5. **Configura** (opzionale):
   Modifica `config/default.yaml` per adattare alle tue impostazioni.

6. **Installa il servizio systemd**:
   ```bash
   sudo cp systemd/digihealth-lamp.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable digihealth-lamp
   sudo systemctl start digihealth-lamp
   ```

7. **Verifica**:
   ```bash
   sudo systemctl status digihealth-lamp
   sudo journalctl -u digihealth-lamp -f
   ```

## Utilizzo

### Avvio Manuale
```bash
digihealth-lamp
```

### Dashboard Web (se abilitata)
Apri http://raspberry-pi-ip:5000 nel browser.

## Configurazione

Modifica `config/default.yaml` per:
- Abilitare/disabilitare moduli
- Configurare indirizzi IP, pin GPIO
- Impostare soglie e parametri

## Sviluppo

### Struttura del Progetto
```
digihealth_lamp/
├── digihealth/
│   ├── main.py              # Entry point
│   ├── config.py            # Configurazione
│   ├── logger.py            # Logging
│   ├── sensors/             # Modulo sensori
│   ├── processors/          # Modulo processori
│   ├── actuators/           # Modulo attuatori
│   ├── communicator/        # Modulo comunicazione
│   └── web/                 # Modulo web (opzionale)
├── config/
│   └── default.yaml         # Configurazione di default
├── systemd/
│   └── digihealth-lamp.service
├── docker/
│   └── Dockerfile
├── requirements.txt
├── setup.py
└── README.md
```

### Aggiungere un Nuovo Sensore

1. Crea una classe che eredita da `BaseSensor` in `sensors/`
2. Aggiungi la configurazione in `config/default.yaml`
3. Registra il sensore in `SensorManager`

### Test

```bash
pip install pytest
pytest tests/
```

## Troubleshooting

- **Errore seriale**: Verifica che UART sia abilitato e non usato dalla console
- **Errore I2C**: Controlla indirizzi con `i2cdetect -y 1`
- **LED non funzionano**: Verifica connessione GPIO 12
- **Shelly non risponde**: Controlla indirizzo IP e connessione di rete

## Licenza

MIT License