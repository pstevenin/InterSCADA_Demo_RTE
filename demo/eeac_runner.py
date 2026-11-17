import json
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import List, Tuple
from demo.config import config
from demo.utils import get_network_files, get_fault_files

# Import EEAC from wheel
from deeac.main import deeac_dynawo
from deeac.IO.arguments_parser import DynawoRunConfiguration

logger = logging.getLogger(__name__)


def run_eeac(network_dir: Path):
    """Run EEAC."""

    network_name = network_dir.name
    output_dir = config.OUTPUT_EEAC_DIR / network_name
    output_dir.mkdir(parents=True, exist_ok=True)
    fault_files = get_fault_files()
    # Parallel launches
    task = partial(_one_run_eeac, network_dir = network_dir, network_name = network_name, output_dir=output_dir)
    with ThreadPoolExecutor(max_workers=config.CPU) as pool:
        list(pool.map(task, fault_files))


def _one_run_eeac(fault_file, network_dir, network_name, output_dir):
    """EEAC run for one fault."""

    iidm_file, dyd_file, jobs_file, par_file = get_network_files(config.OUTPUT_TEMP_DIR / network_dir.name / fault_file.stem)
    fault_name = fault_file.stem
    fault_output_dir = output_dir / fault_name
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
                        if "CCT" in dict_result:
                            cct = dict_result["CCT"]
                        else:
                            cct = None
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
