import logging
import subprocess
from lxml import etree
import json
from pathlib import Path
from typing import List
from demo.config import config
from demo.utils import update_par_file, get_network_files

logger = logging.getLogger(__name__)

def compute_cct(par_file: Path, jobs_file: Path) -> float:
    """Calculate CCT using dichotomy."""
    parser = etree.XMLParser(remove_blank_text=True)
    tree = etree.parse(str(par_file), parser)
    root = tree.getroot()
    elements = root.xpath(".//*[@name='fault_tBegin' or @name='line_tBegin']")
    t_fault = float(elements[0].get("value"))
    l_bound = t_fault + config.LOW_BOUND
    h_bound = t_fault + config.HIGH_BOUND

    while h_bound - l_bound > config.GAP:
        m = l_bound + (h_bound - l_bound) / 2
        update_par_file(par_file, m)
        res = subprocess.run(
            f"{config.DYNAWO_DIR}/myEnvDynawo.sh jobs {jobs_file}",
            capture_output=True,
            shell=True
        ).returncode
        if res == 0:
            l_bound = m
        elif res == 1:
            h_bound = m

    cct = round(l_bound + (h_bound - l_bound) / 2, 3)
    update_par_file(par_file, cct)
    res = subprocess.run(
        f"{config.DYNAWO_DIR}/myEnvDynawo.sh jobs {jobs_file}",
        capture_output=True,
        shell=True
    ).returncode
    if res == 1:
        cct -= config.GAP

    return cct-t_fault

def run_dynawo(network_dir: Path, faults_list2: List[str]) -> None:
    """Run dynawo for faults from list 2."""
    network_name = network_dir.name
    dynawo_output_dir = config.OUTPUT_DYNAWO_DIR / network_name
    dynawo_output_dir.mkdir(parents=True, exist_ok=True)

    for fault_name in faults_list2:

        fault_name_dir = dynawo_output_dir / fault_name
        fault_name_dir.mkdir(parents=True, exist_ok=True)
        iidm_file, dyd_file, jobs_file, par_file = get_network_files(config.OUTPUT_INPUT_DIR / network_dir.name / fault_name)

        # Calculate CCT
        logger.info(f"Calculating CCT for {network_name} - {fault_name}")
        cct = compute_cct(par_file, jobs_file)

        if cct < config.PROTECTION_DELAY:
            status = "NOK"
            margin = ""
        elif cct < config.PROTECTION_DELAY + config.DELTA:
            status = "LOW_MARGIN"
            margin = cct - config.PROTECTION_DELAY
        else:
            status = "OK"
            margin = ""

        result_cct = {
            fault_name: {
                "status": status,
                "margin": margin,
                "CCT": cct
            }
        }

        (fault_name_dir / "results.json").write_text(json.dumps(result_cct, indent=2))
