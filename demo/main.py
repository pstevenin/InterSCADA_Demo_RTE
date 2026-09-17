import logging
import json
import shutil
import time
from datetime import datetime, timedelta
from demo.config import config
from demo.eeac_runner import run_eeac, parse_eeac_results
from demo.dynawo_runner import run_dynawo
from demo.utils import get_fault_files, modify_par_file, modify_dyd_file, get_network_files
from pathlib import Path
from typing import List

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def prepare_input(network_dir: Path, fault_files: List[Path]):
    """Prepare input files."""
    # Create input folders
    config.OUTPUT_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    config.OUTPUT_DYNAWO_DIR.mkdir(parents=True, exist_ok=True)
    config.OUTPUT_EEAC_DIR.mkdir(parents=True, exist_ok=True)

    # Prepare files
    iidm_file, dyd_file, jobs_file, par_file = get_network_files(network_dir)
    for fault_file in fault_files:
        fault_dir = config.OUTPUT_INPUT_DIR / network_dir.name / fault_file.stem
        fault_dir.mkdir(parents=True, exist_ok=True)
        # Copy files
        shutil.copy(iidm_file, fault_dir)
        shutil.copy(dyd_file, fault_dir)
        shutil.copy(jobs_file, fault_dir)
        shutil.copy(par_file, fault_dir)
        # Modify files regarding fault
        with open(fault_file) as f:
            fault_data = json.load(f)
        modify_dyd_file(fault_data["fault_type"], fault_data, fault_dir, iidm_file)
        modify_par_file(fault_data["fault_type"], fault_data, fault_dir, iidm_file)

def process_network(network_dir: Path) -> None:
    """Process EEAC + dynawo-algorithms."""
    network_name = network_dir.name
    logger.info(f"Processing network: {network_name}")

    # Step 1: Prepare Inputs
    fault_files = get_fault_files()
    prepare_input(network_dir, fault_files)

    # Step 1: Run EEAC
    run_eeac(network_dir)

    # Step 2: Parse EEAC results
    list1, list2 = parse_eeac_results(network_dir)

    # Save list 1 results
    if list1:
        output_network_dir = config.OUTPUT_EEAC_DIR / network_name
        result ={}
        for fault_name, cct, cluster in list1:
            result[fault_name] = {"status": "OK", "margin": "None", "CCT": cct, "critical_cluster": cluster}
            (output_network_dir / "result.json").write_text(json.dumps(result, indent=2))

    # Step 3: Run dynawo-algorithms for list 2
    if list2:
        run_dynawo(network_dir, list2)

def main():
    """Main program: run process every 30 minutes."""
    # Folder list
    network_dirs = []
    for d in config.NETWORK_DIR.iterdir():
        network_dirs.append(d)
    network_dirs.sort()
    now = datetime.now()
    logger.info("Starting new processing cycle")
    for network_dir in network_dirs:
        process_network(network_dir)
        if network_dir != network_dirs[-1]:
            # Wait TIME_LAP minutes before next run
            next_run = now + timedelta(minutes=config.TIME_LAP)
            logger.info(f"Next run scheduled at {next_run}")
            time.sleep(config.TIME_LAP * 60)

if __name__ == "__main__":
    main()
