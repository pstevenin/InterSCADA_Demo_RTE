import os
from pathlib import Path
os.environ["GRAALVM_OPTIONS"] = f"-Dpowsybl.config.dirs={Path.cwd().parent / 'data'}"
import re
import json
import logging
import shutil
import time
import pandas as pd
from typing import List
from demo.config import config
from demo.utils import get_network_files, modify_par_file, modify_dyd_file
import pypowsybl as pp
from lxml import etree
from copy import deepcopy

logger = logging.getLogger(__name__)

def inner(text, open_text, close):
    """Content between <parametersSet ...> and </parametersSet>."""
    m_open = open_text.search(text)
    m_close = close.search(text)
    return text[m_open.end():m_close.start()].strip("\r\n")

def launch_first_dynawo(arc_file):
    """Launch dynawo simulation without faults to get based on arcade file"""

    # Load network and perform load flow
    network = pp.network.load(arc_file)
    pp.loadflow.run_ac(network)

    # Replace HVDC stations by fictitious generators
    stations = network.get_vsc_converter_stations()
    stations = stations[stations.p.notnull()]
    all_bbs = network.get_busbar_sections()
    for sid in stations.index:
        station = stations.loc[sid]
        bbs = all_bbs[all_bbs.bus_id == station.bus_id]
        pp.network.create_generator_bay(network,
                                        id="fict_" + sid,
                                        bus_or_busbar_section_id=bbs.index[0],
                                        position_order=0,
                                        min_p=-station.p,
                                        max_p=-station.p,
                                        target_p=-station.p,
                                        voltage_regulator_on=station.voltage_regulator_on,
                                        target_q=-station.q,
                                        target_v=station.target_v)
        network.disconnect(sid)
        logger.info(f"Replacing VSC station {sid} by a fictitious generator")

    # Check load flow results
    result = pp.loadflow.run_ac(network)
    logger.info(result[0].status)
    logger.info(f"Distributed active power: {result[0].distributed_active_power:.1f} MW")

    # Remove foreign groups
    generators = network.get_generators()
    generators = generators[generators.p.notnull()]
    generators = generators[generators.voltage_level_id.str[0]!='.']
    gen_df = pd.read_csv("../data/genmodels.csv", index_col=0)
    gen_df = gen_df[gen_df.index.isin(generators.index)]

    # Apply dynamic models
    model_mapping = pp.dynamic.ModelMapping()
    known_models = model_mapping.get_supported_models_information()
    categories = known_models.category.unique()
    for category in categories:
        category_models = known_models[known_models.category == category]
        category_gens = gen_df[gen_df.model_name.isin(category_models.index)]
        if len(category_gens) > 0:
            logger.info(f"Adding {len(category_gens)} to {category}")
            model_mapping.add_dynamic_model(category, category_gens)

    # Launch dynawo
    parameters = pp.dynamic.Parameters(start_time=0, stop_time=5)
    sim = pp.dynamic.Simulation()
    report_node = pp.report.ReportNode()
    logger.info("Launch first simulation")
    start = time.time()
    results = sim.run(network, model_mapping, None, None, parameters, report_node=report_node)
    logger.info(results.status())
    logger.info(results.status_text())
    logger.info(f"Simulation ends in {time.time() - start:.1f} seconds")

def copy_dynawo_files(fault_file, network_dir):
    """ Copy all dynawo files in temp output folder"""
    candidates = sorted(Path("/tmp/dynawo/").glob("dynawo*"), key=lambda p: p.stat().st_mtime)
    src = candidates[-1]
    for file in src.glob("*.par"):
        shutil.copy2(file, config.OUTPUT_TEMP_DIR / network_dir.name / fault_file.stem / file.name)
    shutil.copy(src / "powsybl_dynawo.dyd", config.OUTPUT_TEMP_DIR / network_dir.name / fault_file.stem)
    shutil.copy(src / "powsybl_dynawo.jobs", config.OUTPUT_TEMP_DIR / network_dir.name / fault_file.stem)
    shutil.copy(src / "powsybl_dynawo.xiidm", config.OUTPUT_TEMP_DIR / network_dir.name / fault_file.stem)

def single_par_file(fault_file, network_dir):
    """ All par files in models.par file. EEAC does not work with several par files"""
    work = config.OUTPUT_TEMP_DIR / network_dir.name / fault_file.stem
    target_file = "models.par"
    #source_files = ["solvers.par"]
    source_files = [p.name for p in work.glob("*.par") if p.name not in (target_file, "network.par")]
    #source_files += other_source

    # Fusion in models.par
    parser = etree.XMLParser(remove_blank_text=False, remove_comments=False, strip_cdata=False)
    models_path = work / target_file
    tree = etree.parse(str(models_path), parser)
    root = tree.getroot()
    indent = (root.text or "\n").lstrip("\r\n")
    closing_tail = root[-1].tail if len(root) else "\n"
    for name in source_files:
        src_root = etree.parse(str(work / name), parser).getroot()
        for child in src_root.iterchildren():
            if len(root):
                root[-1].tail = "\n" + indent
            root.append(deepcopy(child))
        root[-1].tail = closing_tail
    tree.write(str(models_path), encoding="UTF-8", xml_declaration=True)

    # Rename .par files in .dyd et .jobs files
    par_ref = re.compile(r'(=\s*")([^"]*?)[^"/\\]*\.par(")')
    for f in (work / "powsybl_dynawo.dyd", work / "powsybl_dynawo.jobs"):
        txt = f.read_text(encoding="utf-8")
        new, n = par_ref.subn(lambda m: f"{m.group(1)}{m.group(2)}{target_file}{m.group(3)}", txt)
        f.write_text(new, encoding="utf-8")

    # Rename .xiidm file by iidm file, and link in jobs file
    f = next(work.glob("*.xiidm"))
    f.rename(f.with_suffix(".iidm"))
    jobs = work / "powsybl_dynawo.jobs"
    txt = jobs.read_text(encoding="utf-8")
    txt, n = re.subn(r'(?<=[/\\"])' + re.escape(f.name) + r'(?=")', f.with_suffix(".iidm").name, txt)
    jobs.write_text(txt, encoding="utf-8")

    # Delete all .par files except models.par
    for p in work.glob("*.par"):
        if p.name != target_file:
            p.unlink()


def prepare_input(network_dir: Path, fault_files: List[Path]) -> None:
    """Prepare input files."""

    # Create input and output folders
    config.OUTPUT_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    config.OUTPUT_EEAC_DIR.mkdir(parents=True, exist_ok=True)
    config.OUTPUT_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Prepare files
    if config.NETWORK_FILE_TYPE == "iidm":
        iidm_file, dyd_file, jobs_file, par_file = get_network_files(network_dir)
        for fault_file in fault_files:
            fault_dir = config.OUTPUT_TEMP_DIR / network_dir.name / fault_file.stem
            fault_dir.mkdir(parents=True, exist_ok=True)
            # Copy files
            shutil.copy(iidm_file, fault_dir)
            shutil.copy(dyd_file, fault_dir)
            shutil.copy(jobs_file, fault_dir)
            shutil.copy(par_file, fault_dir)
            # Modify files regarding fault
            with open(fault_file) as f:
                fault_data = json.load(f)
            modify_dyd_file(fault_data["fault_type"], fault_data, dyd_file, par_file)
            modify_par_file(fault_data["fault_type"], fault_data, iidm_file, par_file)
    else:
        arc_file = next(network_dir.glob("*.arc"), None)
        launch_first_dynawo(arc_file)
        for fault_file in fault_files:
            fault_dir = config.OUTPUT_TEMP_DIR / network_dir.name / fault_file.stem
            fault_dir.mkdir(parents=True, exist_ok=True)
            copy_dynawo_files(fault_file, network_dir)
            single_par_file(fault_file, network_dir)
            # Modify files regarding fault
            with open(fault_file) as f:
                fault_data = json.load(f)
            iidm_file, dyd_file, jobs_file, par_file = get_network_files(fault_dir)
            modify_dyd_file(fault_data["fault_type"], fault_data, dyd_file, par_file)
            modify_par_file(fault_data["fault_type"], fault_data, iidm_file, par_file)