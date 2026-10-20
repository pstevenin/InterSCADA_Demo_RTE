import os
from pathlib import Path
#os.environ["GRAALVM_OPTIONS"] = f"-Dpowsybl.config.dirs={Path('../data').absolute()}"
os.environ["GRAALVM_OPTIONS"] = f"-Dpowsybl.config.dirs={Path.cwd().parent / 'data'}"
import shutil
import sys
import logging
import json
import time
from datetime import datetime, timedelta
from demo.config import config
from demo.eeac_runner import run_eeac, parse_eeac_results
from demo.dynawo_runner import run_dynawo
from demo.utils import get_fault_files
from demo.prepare_input import prepare_input
#from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
    result_eeac = {}
    if list1:
        for fault_name, cct, cluster in list1:
            result_eeac[fault_name] = {"status": "OK", "margin": "None", "CCT": cct}

    # Step 3: Run dynawo for list 2
    result_dynawo = {}
    if list2:
        result_dynawo = run_dynawo(network_dir, list2)

    # Step 4: Write final results
    output_result_dir = config.OUTPUT_RESULTS_DIR / network_name
    output_result_dir.mkdir(parents=True, exist_ok=True)
    result_final = {**result_eeac, **result_dynawo}
    (output_result_dir / "result.json").write_text(json.dumps(result_final, indent=2))

def demo(args=None):
    """ Main program: run process every 30 minutes."""

    # Delete previous outputs
    if os.path.isdir(config.OUTPUT_DIR):
        shutil.rmtree(config.OUTPUT_DIR)
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
    demo(sys.argv[1:])