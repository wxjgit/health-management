from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / '.env')

MODEL_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
MODEL_API_KEY = 'sk-034f66dbb4954e688ec5075e3fbe254f'
MODEL_NAME = 'qwen3.8-max'


@dataclass
class Settings:
    data_dir: Path = ROOT / 'data'
    model_base_url: str = ''
    model_api_key: str = ''
    model_name: str = ''
    scheduler_enabled: bool = True
    local_only: bool = False

    @classmethod
    def from_env(cls):
        return cls(
            data_dir=Path(os.getenv('DATA_DIR') or str(ROOT / 'data')),
            model_base_url=MODEL_BASE_URL,
            model_api_key=MODEL_API_KEY,
            model_name=MODEL_NAME,
            scheduler_enabled=os.getenv('SCHEDULER_ENABLED', 'true').lower() == 'true',
            local_only=os.getenv('PROCESSING_MODE', 'model_allowed') == 'local_only',
        )

    @property
    def mode(self):
        return 'model' if self.model_base_url and self.model_name and self.model_api_key else 'demo'

