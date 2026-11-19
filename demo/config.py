import argparse
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = BASE_DIR / "param_example.json"


def parse_config(argv=None):
    parser = argparse.ArgumentParser(
        prog="demo", description="Choose configuration file."
    )
    parser.add_argument(
        "-c",
        "--config",
        default=None,
        help="configuration json file name (default: param_example.json)",
    )
    return parser.parse_args(argv)


class Config:
    """Vide à la création, remplie par load()."""

    def load(self, config_path=None):
        path = Path(config_path).resolve() if config_path else DEFAULT_CONFIG

        with open(path, "r", encoding="utf-8") as f:
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
        self.OUTPUT_TEMP_DIR = BASE_DIR / paths["output_temp"]
        self.OUTPUT_EEAC_DIR = BASE_DIR / paths["output_eeac"]
        self.OUTPUT_RESULTS_DIR = BASE_DIR / paths["output_results"]

        # Filenames
        self.OUTPUT_EEAC_FILE = filenames["eeac_clusters_results"]
        self.EEAC_PARAM_FILE = filenames["eeac_param"]
        self.DYNAWO_EXE = filenames["dynawo_exe"]
        self.NETWORK_FILE_TYPE = filenames["network_file_type"]

        # Parameters
        self.PROTECTION_DELAY = params["protection_delay"]
        self.DELTA = params["delta"]
        self.BASE_POWER = params["base_power"]
        self.TIME_LAP = params["time_lap"]
        self.LOW_BOUND = params["low_bound"]
        self.HIGH_BOUND = params["high_bound"]
        self.GAP = params["gap"]
        self.CPU = params["cpu"]

        return self


config = Config()


def init_config(argv=None):
    """À appeler une fois, au début de demo()."""
    return config.load(parse_config(argv).config)