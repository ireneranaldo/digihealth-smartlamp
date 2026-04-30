import influxdb_client
from influxdb_client.client.write_api import SYNCHRONOUS
from typing import Dict, Any
from ..config import config
from ..logger import logger

class TelegrafClient:
    """Invia i dati direttamente a InfluxDB Cloud usando il formato del vecchio script."""

    def __init__(self):
        # Configurazioni estratte dal tuo telegraf.conf
        self.url = "https://influxdb1.digisense.it"
        self.token = "qgkGXvOlp_Gj0veF-ewe8FSJGGCxQQoh0EfMg-Po9Bd8Vzw2iz8Y6d-Z2DE0hqojiGgIsMWJrsbqFjV6-Mi4Zw=="
        self.org = "Digiplus"
        self.bucket = "health_data"
        
        # Inizializzazione Client
        self.client = influxdb_client.InfluxDBClient(
            url=self.url,
            token=self.token,
            org=self.org
        )
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)

    def send(self, data: Dict[str, Any]):
        """Invia i dati mantenendo i nomi originali delle variabili."""
        try:
            # Creazione del punto per InfluxDB
            # Usiamo 'ZPHSensor' come nel vecchio file
            point = influxdb_client.Point("ZPHSensor_sensore")
            
            # Mappiamo i campi esattamente come faceva il vecchio parse_sensor_data
            # Questi nomi sono quelli che Telegraf riceveva e mandava al database
            fields = {
                "PM1-Particolato-[µg/m^3]": data.get("PM1-Particolato-[µg/m^3]"),
                "PM2_5-Particolato-[µg/m^3]": data.get("PM2_5-Particolato-[µg/m^3]"),
                "PM10-Particolato-[µg/m^3]": data.get("PM10-Particolato-[µg/m^3]"),
                "CO2-AnidrideCarbonica-[ppm]": data.get("CO2-AnidrideCarbonica-[ppm]"),
                "TVOC-QualitaAria-[G]": data.get("TVOC-QualitaAria-[G]"),
                "TEMP-[C]": data.get("TEMP-[C]"),
                "HUM-[%]": data.get("HUM-[%]"),
                "CH2O-Formaldeie-[mg/m^3]": data.get("CH2O-Formaldeie-[mg/m^3]"),
                "CO-MonossidoDiCarbonio-[ppm]": data.get("CO-MonossidoDiCarbonio-[ppm]"),
                "O3-Ozono-[ppm]": data.get("O3-Ozono-[ppm]"),
                "NO2-BiossidoDiAzoto-[ppm]": data.get("NO2-BiossidoDiAzoto-[ppm]"),
                "lux-IntensitaLuminosa": data.get("lux-IntensitaLuminosa"),
                "IAQI": data.get("IAQI")
            }

            # Aggiungiamo solo i campi che non sono None
            for key, val in fields.items():
                if val is not None:
                    point.field(key, float(val))

            # Aggiungiamo i TAGS originali per non rompere le dashboard esistenti
            point.tag("sensor", "ZPHS01B")
            point.tag("host", "raspberry01")
            point.tag("lampada", "AS00000046")
            point.tag("stanza", "UfficioDigiplus")

            # Invio fisico del dato
            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            
        except Exception as e:
            logger.error(f"Errore durante l'invio a InfluxDB: {e}")
