# Avvio DigiHealth su Windows

## Prerequisiti

- Python 3.13
- Tutte le dipendenze già installate su questo PC

Se dovessi reinstallare le dipendenze:

```powershell
pip install flask tinytuya pydantic pyyaml pyaudio pyserial numpy scipy
```

## Comando di avvio

Apri PowerShell (o CMD) nella cartella del progetto ed esegui:

```powershell
cd C:\Users\digip\Desktop\digihealth-smartlamp
python -m digihealth.main
```

## Pagine web disponibili

| URL | Descrizione |
|-----|-------------|
| `http://127.0.0.1:5000/` | Dashboard principale (audio, aria, attuatori) |
| `http://127.0.0.1:5000/config` | Configurazione attuatori, sensori, processori |
| `http://127.0.0.1:5000/thresholds` | Soglie variabili fisiche (purificatore e climatizzatore) |

## File di configurazione attivo su Windows

```
config/windows.yaml
```

Viene selezionato automaticamente in base al sistema operativo.
Su Linux/Raspberry Pi viene usato `config/default.yaml`.

## Note sugli attuatori su Windows

- **Shelly**: attiva, tenta connessione alla lampada in rete locale
- **TuyaAC**: attivo se `enabled: true` in `windows.yaml`
- **TuyaPurifier**: disabilitato di default su Windows (`enabled: false`)
- **Sensore ZPH01B**: disabilitato su Windows (porta seriale non disponibile)
- **Microfono**: attivo, richiede dispositivo audio USB (device_index: 1)

## Soglie (pagina `/thresholds`)

Le soglie determinano quando gli attuatori intervengono:

- **Purificatore**: si accende se PM2.5 > tolleranza PM2.5 **oppure** CO2 > tolleranza CO2
- **Climatizzatore**: si accende se Temperatura > tolleranza TEMP

Dopo aver modificato e salvato le soglie, clicca **Riavvia** per applicarle.

## Fermare l'app

```
Ctrl+C
```
nel terminale dove gira l'app.
