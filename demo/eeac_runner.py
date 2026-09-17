import logging
import json
from demo.config import config
from demo.utils import get_network_files, get_fault_files
from pathlib import Path
from typing import List, Tuple

# Import EEAC from wheel
from deeac.main import deeac_dynawo
from deeac.IO.arguments_parser import DynawoRunConfiguration

logger = logging.getLogger(__name__)

def run_eeac(network_dir: Path) -> None:
    """Run EEAC."""
    network_name = network_dir.name
    temp_output_dir = config.OUTPUT_EEAC_DIR / network_name
    temp_output_dir.mkdir(parents=True, exist_ok=True)
    fault_files = get_fault_files()

    for fault_file in fault_files:
        iidm_file, dyd_file, jobs_file, par_file = get_network_files(config.OUTPUT_INPUT_DIR / network_dir.name / fault_file.stem)
        fault_name = fault_file.stem
        fault_output_dir = temp_output_dir / fault_name
        fault_output_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Running EEAC for {network_name} - {fault_name}")

        # Call to EEAC
        config_dynawo = DynawoRunConfiguration(
            jobs_file=str(jobs_file),
            iidm_file=None,
            dynawo_dyd_file=None,
            dynawo_par_file=None,
            dynawo_dyn_file=None,
            execution_tree_file=str(config.INPUT_DIR / config.EEAC_PARAM_FILE),
            execution_tree=None,
            island_threshold=0.0,
            cores=1,
            protection_delay=0.0,
            verbose=False,
            output_dir=fault_output_dir,
            json_path=None,
            rewrite=True,
            warn=False,
        )
        deeac_dynawo(config_dynawo)

def parse_eeac_results(network_dir: Path) -> Tuple[List[Tuple[str, float, str]], List[str]]:
    """Parse EEAC results and returns fault lists."""
    network_name = network_dir.name
    temp_output_dir = config.OUTPUT_EEAC_DIR / network_name
    l_stable = []
    l_unstable = []

    for fault_dir in temp_output_dir.iterdir():
        if fault_dir.is_dir():
            result_file = fault_dir / config.OUTPUT_EEAC_FILE
            if result_file.exists():
                with open(result_file) as f:
                    result = json.load(f)
                    if result:
                        dict_result = next(iter(result.values()))
                        cct = dict_result["CCT"]
                        status = dict_result["status"]
                        if status == "ALWAYS STABLE":
                            l_stable.append((fault_dir.name, cct, dict_result["critical_cluster"]))
                        elif status == "POTENTIALLY STABLE" and (cct / 2) >= (config.PROTECTION_DELAY + config.DELTA):
                            l_stable.append((fault_dir.name, cct, dict_result["critical_cluster"]))
                        else:
                            l_unstable.append(fault_dir.name)
                    else:
                        l_unstable.append(fault_dir.name)
    return l_stable, l_unstable
