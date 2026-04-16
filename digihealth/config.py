import yaml
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import os

class SensorConfig(BaseModel):
    zph: Dict[str, Any] = Field(default_factory=dict)
    light: Dict[str, Any] = Field(default_factory=dict)
    door: Dict[str, Any] = Field(default_factory=dict)
    window: Dict[str, Any] = Field(default_factory=dict)
    microphone: Dict[str, Any] = Field(default_factory=dict)

class ProcessorConfig(BaseModel):
    iaqi: Dict[str, Any] = Field(default_factory=dict)
    circadian: Dict[str, Any] = Field(default_factory=dict)
    audio_comfort: Dict[str, Any] = Field(default_factory=dict)

class ActuatorConfig(BaseModel):
    neopixel: Dict[str, Any] = Field(default_factory=dict)
    shelly: Dict[str, Any] = Field(default_factory=dict)

class CommunicatorConfig(BaseModel):
    telegraf: Dict[str, Any] = Field(default_factory=dict)
    ipc: Dict[str, Any] = Field(default_factory=dict)

class WebConfig(BaseModel):
    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 5000

class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: Optional[str] = None

class DigiHealthConfig(BaseModel):
    sensors: SensorConfig = Field(default_factory=SensorConfig)
    processors: ProcessorConfig = Field(default_factory=ProcessorConfig)
    actuators: ActuatorConfig = Field(default_factory=ActuatorConfig)
    communicator: CommunicatorConfig = Field(default_factory=CommunicatorConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

def load_config(config_path: str = "config/default.yaml") -> DigiHealthConfig:
    """Load configuration from YAML file."""
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            data = yaml.safe_load(f)
        return DigiHealthConfig(**data)
    else:
        return DigiHealthConfig()

config = load_config()