import json
import argparse
from pathlib import Path

# Get config file
parser = argparse.ArgumentParser(description="Choose configuration file.")
parser.add_argument(
    "--config",
    type=str,
    default="param_example.json",
    help="configuration json file name",
)
args = parser.parse_args()

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / args.config

class Config:
    def __init__(self, config_path: Path = CONFIG_FILE):
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        paths = data["paths"]
        filenames = data["filenames"]
        params = data["parameters"]

        # Paths
        self.DYNAWO_DIR = BASE_DIR / paths["dynawo"]
        self.INPUT_DIR = BASE_DIR / paths["input"]
        self.FAULTS_DIR = BASE_DIR / paths["faults"]
        self.NETWORK_DIR = BASE_DIR / paths["network"]
        self.OUTPUT_DIR = BASE_DIR / paths["output"]
        self.OUTPUT_INPUT_DIR = BASE_DIR / paths["output_input"]
        self.OUTPUT_EEAC_DIR = BASE_DIR / paths["output_eeac"]
        self.OUTPUT_DYNAWO_DIR = BASE_DIR / paths["output_dynawo"]

        # Filenames
        self.OUTPUT_EEAC_FILE = filenames["eeac_clusters_results"]
        self.EEAC_PARAM_FILE = filenames["eeac_param"]
        self.DYNAWO_ZIP_RESULTS = filenames["dynawo_zip_results"]

        # Parameters
        self.PROTECTION_DELAY = params["protection_delay"]
        self.DELTA = params["delta"]
        self.BASE_POWER = params["base_power"]
        self.TIME_LAP = params["time_lap"]
        self.LOW_BOUND = params["low_bound"]
        self.HIGH_BOUND = params["high_bound"]
        self.GAP = params["gap"]

# Unique instance, directly importable
config = Config()
