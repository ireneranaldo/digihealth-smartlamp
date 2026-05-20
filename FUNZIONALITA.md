# DigiHealth Lamp — Funzionalità

Documento descrittivo (non tecnico) di tutte le funzionalità implementate nel progetto **DigiHealth Lamp**: una lampada smart su Raspberry Pi per il monitoraggio della qualità dell'ambiente, la visualizzazione luminosa adattiva e l'invio dei dati a un database cloud.

---

## 1. Panoramica generale

DigiHealth Lamp è un sistema modulare che svolge ciclicamente quattro attività ogni 30 secondi:

1. **Raccolta dati** dai sensori ambientali collegati al Raspberry Pi.
2. **Elaborazione** dei dati grezzi (calcolo dell'Indice di Qualità dell'Aria Interna — IAQI).
3. **Invio** dei dati al database InfluxDB nel cloud Digiplus.
4. **Risposta visiva** tramite una striscia LED NeoPixel e una dashboard web in tempo reale.

L'architettura è basata su *manager* indipendenti (sensori, elaboratori, attuatori, comunicatori, web), ognuno caricato dinamicamente solo se abilitato nella configurazione. Tutto il sistema gira come servizio `systemd` che si avvia automaticamente al boot della Raspberry.

---

## 2. Sensoristica ambientale

### 2.1 Sensore qualità dell'aria — ZPH01B

Sensore multi-gas collegato via porta seriale UART (`/dev/serial0`, 9600 baud). Ad ogni lettura invia un comando di interrogazione e riceve 26 byte di risposta da cui estrae i seguenti parametri:

| Parametro | Unità | Descrizione |
|---|---|---|
| PM1 | µg/m³ | Particolato fine (1 micron) |
| PM2.5 | µg/m³ | Particolato fine (2,5 micron) |
| PM10 | µg/m³ | Particolato (10 micron) |
| CO₂ | ppm | Anidride carbonica |
| TVOC | grado (0–4) | Composti organici volatili totali |
| Temperatura | °C | Temperatura ambiente |
| Umidità | % | Umidità relativa |
| CH₂O | mg/m³ | Formaldeide |
| CO | ppm | Monossido di carbonio |
| O₃ | ppm | Ozono |
| NO₂ | ppm | Biossido di azoto |

Se la lettura fallisce (porta non disponibile, lunghezza dati errata) il sistema registra l'errore nel log e continua il ciclo senza bloccarsi.

### 2.2 Sensore di luminosità — BH1750

Sensore di luce ambientale collegato via bus I²C (bus 1, indirizzo `0x23`). Restituisce il valore in **lux** della luminosità ambientale, usato sia per il monitoraggio sia per regolare la componente circadiana della lampada.

### 2.3 Sensori previsti ma non implementati

La configurazione prevede gli "agganci" per ulteriori sensori che al momento sono dichiarati ma non attivi:

- **Sensore porta** (GPIO 18) — non implementato.
- **Sensore finestra** (GPIO 17) — non implementato.
- **Microfono** — disabilitato di default.

---

## 3. Elaborazione dei dati — Indice di Qualità dell'Aria (IAQI)

Il processore IAQI calcola un indice sintetico (0–300+) ispirato allo standard AQI dell'EPA americana. Per ciascun inquinante usa una tabella di **breakpoint** (intervalli di concentrazione → intervalli di indice) e applica una formula di interpolazione lineare.

Inquinanti considerati per il calcolo:

- **PM2.5** — 4 fasce (0–12 µg/m³ ottima, fino a 55,4 µg/m³ pericolosa).
- **CO₂** — 5 fasce (400–800 ppm ottima, fino a 5000 ppm pericolosa).
- **TVOC** — 4 fasce (0–100 ottima, fino a 500 pericolosa).
- **CH₂O (formaldeide)** — 4 fasce (0–50 ottima, fino a 1000 pericolosa).
- **NO₂** — 4 fasce (0–0,05 ottima, fino a 1 pericolosa).

L'IAQI finale è il **massimo** tra i sotto-indici (logica "worst-case": l'aria è buona solo se *tutti* i parametri lo sono).

Il processore arricchisce inoltre i dati con un blocco `dashboard` semplificato contenente temperatura, umidità, CO₂ e IAQI, già pronto per essere consumato dall'interfaccia web.

---

## 4. Attuatore visivo — Striscia NeoPixel

Striscia LED RGB collegata al GPIO 12 con **144 pixel**, suddivisa in due segmenti funzionali distinti:

### 4.1 Segmento IAQI (pixel 0–79) — Indicatore qualità dell'aria

Visualizza con un **effetto "breathing"** (respirazione, sinusoide lenta) lo stato dell'aria. Il colore varia in base al valore IAQI:

| IAQI | Colore | Significato |
|---|---|---|
| ≤ 25 | Azzurro | Eccellente |
| ≤ 50 | Verde | Buona |
| ≤ 100 | Giallo | Moderata |
| ≤ 150 | Arancio | Insalubre per gruppi sensibili |
| ≤ 170 | Arancio scuro | Insalubre |
| > 170 | Rosso | Pericolosa |

La luminosità è pulsante e limitata a un fattore di intensità per evitare abbagliamento.

### 4.2 Segmento circadiano (pixel 80–143) — Illuminazione adattiva

Riproduce un ritmo circadiano semplificato regolando la temperatura colore in funzione dell'ora:

- **Mattina/giorno (7:00–16:00)**: luce fredda (~6500 K, RGB bianco).
- **Sera/notte**: luce calda (~2700 K, RGB ambrato).

Esiste anche un meccanismo *opzionale* di indicazione del numero di persone presenti (lettura da file JSON `/home/digip/people_to_leds.json`) che illumina un numero proporzionale di LED in viola tenue. Attualmente forzato a 0.

### 4.3 Finestra di attività

I LED si attivano **solo tra le 08:10 e le 18:00**. Fuori da questo intervallo la striscia viene spenta completamente. Logica pensata per un contesto d'ufficio.

---

## 5. Comunicazione cloud — Invio a InfluxDB

Il modulo `TelegrafClient` (nonostante il nome, *non* usa più Telegraf) invia i dati **direttamente** al database InfluxDB Cloud di Digiplus:

- **Endpoint**: `https://influxdb1.digisense.it`
- **Organizzazione**: `Digiplus`
- **Bucket**: `health_data`
- **Measurement**: `ZPHSensor_sensore`

Ad ogni invio viene creato un *point* con tutti i campi misurati (mantenendo i nomi originali del vecchio script per non rompere le dashboard Grafana esistenti) e i seguenti tag:

- `sensor: ZPHS01B`
- `host: raspberry01`
- `lampada: AS00000046`
- `stanza: UfficioDigiplus`

> Nota: il token di scrittura è attualmente **hard-coded** nel file `telegraf_client.py`.

---

## 6. Dashboard Web (Flask)

Server Flask integrato che espone su porta `5000` (host `0.0.0.0`, raggiungibile da tutta la LAN) un'interfaccia di controllo e visualizzazione.

### 6.1 Endpoint HTTP

| Endpoint | Metodo | Funzione |
|---|---|---|
| `/` | GET | Pagina HTML principale (dashboard) |
| `/status` | GET | Stato JSON corrente del sistema (dati aria, modalità, livello audio, spettro) |
| `/toggle` | GET | Avvia/ferma la modalità CHECK |
| `/calibrate` | GET | Imposta la modalità CALIBRATION |
| `/set_volume?level=X` | GET | Imposta il volume audio |
| `/shutdown_kiosk` | GET | Chiude il browser Chromium (modalità kiosk) |

### 6.2 Funzionalità dell'interfaccia HTML

Pagina single-page con stile "spa-zen" (palette sabbia/pesca/crema) che mostra:

- **Modalità corrente** del sistema (IDLE / CHECK / COMFORT / CALIBRATION).
- **Livello audio in dB** con barra di progresso colorata (verde/giallo/rosso in base a soglie).
- **Timer countdown** per l'analisi in corso.
- **Pulsanti Avvia/Ferma e Calibra** con stato visivo.
- **Slider Volume**.
- **Pulsante Esci** (chiude la modalità kiosk).
- **Riga qualità aria**: Temperatura, Umidità, CO₂, IAQI (colore dell'IAQI dinamico: verde/giallo/rosso).
- **Grafico spettro audio** in tempo reale tramite Plotly (barre colorate sulle stesse soglie).
- **Aggiornamento automatico ogni 250 ms** via polling su `/status`.

Lo sfondo della pagina cambia colore in funzione della modalità per dare un feedback visivo immediato.

### 6.3 Stato condiviso

La dashboard riceve gli aggiornamenti dal ciclo principale tramite il metodo `WebManager.update_data()`: ad ogni iterazione i dati elaborati vengono iniettati in un dizionario `state` globale che le richieste HTTP leggono.

---

## 7. Configurazione

Tutti i parametri sono centralizzati in `config/default.yaml` e validati tramite modelli Pydantic. Permette di:

- **Abilitare/disabilitare** ogni singolo modulo (sensori, processori, attuatori, web).
- **Configurare l'hardware**: porta seriale, baud rate, indirizzi I²C, pin GPIO, numero di pixel.
- **Impostare i range** dei segmenti LED (IAQI vs circadiano).
- **Definire tag e measurement** per InfluxDB.
- **Configurare** IP, porta e abilitazione del web server.
- **Scegliere livello e file di logging**.

Se il file di configurazione non esiste, il sistema parte con valori di default ragionevoli.

---

## 8. Logging

Logging strutturato basato sul modulo standard Python con due output simultanei:

- **Console** (visibile da `journalctl` quando il sistema gira come servizio).
- **File** su `/var/log/digihealth-lamp.log` (configurabile).

Formato uniforme: `timestamp - modulo - livello - messaggio`. Livello impostabile da configurazione (default `INFO`).

I log di Flask (werkzeug) sono **silenziati** a livello `ERROR` per evitare rumore.

---

## 9. Esecuzione come servizio systemd

Il file `systemd/digihealth-lamp.service` definisce un servizio che:

- Si avvia automaticamente **al boot** della Raspberry (dopo che la rete è pronta).
- **Si riavvia da solo** in caso di crash, con backoff di 5 secondi.
- Gira come utente `pi` nella cartella `/home/pi/digihealth-lamp` (sul dispositivo reale è `/home/digip/digihealth-lamp`).
- Inoltra stdout/stderr al **journal** di systemd, consultabile con `journalctl -u digihealth-lamp -f`.

### Comandi di gestione principali

| Azione | Comando |
|---|---|
| Stato | `sudo systemctl status digihealth-lamp` |
| Avvio | `sudo systemctl start digihealth-lamp` |
| Stop | `sudo systemctl stop digihealth-lamp` |
| Riavvio | `sudo systemctl restart digihealth-lamp` |
| Abilita all'avvio | `sudo systemctl enable digihealth-lamp` |
| Log in tempo reale | `sudo journalctl -u digihealth-lamp -f` |
| Avvio manuale (debug) | `sudo ./venv/bin/python3 -m digihealth.main` |

---

## 10. Modello concorrente

L'applicazione usa **due thread daemon**:

1. **Thread sensori** — esegue il ciclo lettura → elaborazione → invio → aggiornamento web → aggiornamento LED ogni 30 secondi.
2. **Thread web** — esegue il server Flask per le richieste HTTP della dashboard.

Il thread principale resta in attesa e gestisce la chiusura pulita su `Ctrl+C`.

---

## 11. Estendibilità

Il progetto è strutturato per essere facilmente estendibile:

- **Nuovi sensori**: estendendo `BaseSensor` (con i metodi `read()` e `is_available()`) e registrandoli nel `SensorManager`.
- **Nuovi processori**: aggiungendo una classe con metodo `process(data) -> data` e abilitandola nella config.
- **Nuovi attuatori**: aggiungendo una classe con metodo `update(data)` al `ActuatorManager`.
- **Nuovi canali di comunicazione**: aggiungendo un client con metodo `send(data)` al `CommunicatorManager`.

Ogni manager carica dinamicamente i suoi componenti in base a quanto abilitato nella configurazione, senza richiedere modifiche al `main`.

---

## 12. Stato attuale e funzionalità solo previste

Funzionalità **attive e operative**:

- Lettura ZPH01B (qualità aria multi-parametro).
- Lettura BH1750 (lux).
- Calcolo IAQI.
- Invio dati a InfluxDB Cloud.
- Striscia NeoPixel con effetto breathing IAQI + segmento circadiano.
- Dashboard web Flask con qualità dell'aria in tempo reale.
- Servizio systemd autonomo.

Funzionalità **dichiarate ma non implementate** (presenti come placeholder):

- Sensore porta (GPIO 18).
- Sensore finestra (GPIO 17).
- Microfono / analisi acustica / spettro audio (la dashboard ha già la UI pronta).
- Processore `circadian` e `audio_comfort` indipendenti dall'IAQI processor.
- Attuatore **Shelly Smart Lamp** via HTTP (configurato ma disabilitato).
- Modalità COMFORT, CHECK, CALIBRATION reali (l'interfaccia web le commuta come stato, ma non c'è ancora la logica di backend che le gestisce).
- Conteggio persone via file JSON (codice presente ma chiamata commentata).
