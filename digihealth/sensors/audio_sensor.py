import numpy as np
import pyaudio
from ..logger import logger

class AudioSensor:
    def __init__(self):
        self.p = pyaudio.PyAudio()
        self.CHUNK = 1024
        self.RATE = 44100
        try:
            # Apriamo lo stream sia in input che in output
            self.stream = self.p.open(
                format=pyaudio.paInt16, 
                channels=1, 
                rate=self.RATE,
                input=True, 
                output=True,
                frames_per_buffer=self.CHUNK
            )
            logger.info("AudioSensor: Stream PyAudio aperto con successo (In/Out)")
        except Exception as e:
            logger.error(f"AudioSensor: Errore apertura stream: {e}")
            self.stream = None

    def collect(self):
        """Legge dal microfono, calcola i dB e lo spettro FFT"""
        if not self.stream:
            return {"audio_level": 0, "spectrum": [0]*20}
            
        try:
            # Leggiamo i dati grezzi dal microfono
            raw_data = self.stream.read(self.CHUNK, exception_on_overflow=False)
            data = np.frombuffer(raw_data, dtype=np.int16)
            
            # 1. Calcolo Decibel (dB)
            # rms = root mean square (media quadratica dei campioni)
            rms = np.sqrt(np.mean(data.astype(float)**2))
            # 95 è un offset di calibrazione tipico per microfoni USB economici
            db = 20 * np.log10(rms / 32768.0) + 95 if rms > 0 else 0
            
            # 2. Analisi Spettro (FFT) per le 20 barre della dashboard
            fft = np.abs(np.fft.fft(data))[:self.CHUNK//2]
            num_bars = 20
            bar_size = len(fft) // num_bars
            spectrum = []
            
            for i in range(num_bars):
                # Prendiamo la media della sezione di frequenze e normalizziamo
                avg_val = np.mean(fft[i*bar_size : (i+1)*bar_size])
                # Dividere per 500 scala il valore per la visualizzazione Plotly
                spectrum.append(int(avg_val / 500))
            
            return {
                "audio_level": round(max(0, db), 1),
                "spectrum": spectrum
            }
        except Exception as e:
            logger.error(f"Errore durante collect audio: {e}")
            return {"audio_level": 0, "spectrum": [0]*20}

    def play_noise(self, volume):
        """Genera e invia un chunk di rumore bianco alle casse"""
        if not self.stream:
            return
            
        try:
            # L'ampiezza del rumore dipende dal volume impostato sulla dashboard
            # 0.1 è un fattore di sicurezza per non distorcere
            amplitude = 0.1 * volume
            white_noise = np.random.uniform(-amplitude, amplitude, self.CHUNK)
            
            # Trasformiamo in formato bytes int16 per PyAudio
            noise_bytes = (white_noise * 32767).astype(np.int16).tobytes()
            self.stream.write(noise_bytes)
        except Exception as e:
            logger.error(f"Errore durante play_noise: {e}")

    def stop_all(self):
        """Chiude correttamente lo stream e PyAudio"""
        try:
            if self.stream:
                self.stream.stop_stream()
                self.stream.close()
            self.p.terminate()
            logger.info("AudioSensor: Risorse audio rilasciate.")
        except Exception as e:
            logger.error(f"Errore durante chiusura audio: {e}")
