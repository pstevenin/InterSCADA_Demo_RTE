import logging
import subprocess
from lxml import etree
from pathlib import Path
from typing import List, Dict
from demo.config import config
from demo.utils import update_par_file, get_network_files, set_files_compliant

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
            f"{config.DYNAWO_DIR}/{config.DYNAWO_EXE} jobs {jobs_file}",
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
        f"{config.DYNAWO_DIR}/{config.DYNAWO_EXE} jobs {jobs_file}",
        capture_output=True,
        shell=True
    ).returncode
    if res == 1:
        cct -= config.GAP

    return cct-t_fault

def run_dynawo(network_dir: Path, faults_list2: List[str]) -> Dict:
    """Run dynawo for faults from list 2."""
    network_name = network_dir.name
    result_cct = {}
    for fault_name in faults_list2:

        iidm_file, dyd_file, jobs_file, par_file = get_network_files(config.OUTPUT_TEMP_DIR / network_dir.name / fault_name)

        # Modify dyd and par files to be compliant with dynawo
        set_files_compliant(dyd_file, par_file, jobs_file)

        # Calculate CCT
        logger.info(f"Calculating CCT for {network_name} - {fault_name}")
        cct = compute_cct(par_file, jobs_file)

        if cct < config.PROTECTION_DELAY:
            status = "NOK"
            margin = "None"
        elif cct < config.PROTECTION_DELAY + config.DELTA:
            status = "LOW_MARGIN"
            margin = cct - config.PROTECTION_DELAY
        else:
            status = "OK"
            margin = "None"

        result_cct[fault_name] = {
            "status": status,
            "margin": margin,
            "CCT": cct
        }

    return result_cct