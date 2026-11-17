#import os
#from pathlib import Path
#os.environ["GRAALVM_OPTIONS"] = f"-Dpowsybl.config.dirs={Path.cwd().parent / 'data'}"
import json
import logging
import pandas as pd
import pypowsybl as pp
import re
import shutil
import time
from copy import deepcopy
from lxml import etree
from pathlib import Path
from typing import List, Dict, Any
from demo.config import config
from demo.utils import get_network_files, parse_xml

logger = logging.getLogger(__name__)


def prepare_input(network_dir: Path, fault_files: List[Path]) -> None:
    """Prepare input files."""

    # Create input and output folders
    config.OUTPUT_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    config.OUTPUT_EEAC_DIR.mkdir(parents=True, exist_ok=True)
    config.OUTPUT_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Prepare files
    if config.NETWORK_FILE_TYPE == "iidm":
        for fault_file in fault_files:
            iidm_file, dyd_file, jobs_file, par_file = get_network_files(network_dir)
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
            iidm_file, dyd_file, jobs_file, par_file = get_network_files(fault_dir)
            dyd_eeac(fault_data, dyd_file, par_file)
            par_eeac(fault_data, iidm_file, par_file)
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
            dyd_eeac(fault_data, dyd_file, par_file)
            par_eeac(fault_data, iidm_file, par_file)


def launch_first_dynawo(arc_file) -> None:
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


def copy_dynawo_files(fault_file, network_dir) -> None:
    """ Copy all dynawo files in temp output folder"""

    candidates = sorted(Path("/tmp/dynawo/").glob("dynawo*"), key=lambda p: p.stat().st_mtime)
    src = candidates[-1]
    for file in src.glob("*.par"):
        shutil.copy2(file, config.OUTPUT_TEMP_DIR / network_dir.name / fault_file.stem / file.name)
    shutil.copy(src / "powsybl_dynawo.dyd", config.OUTPUT_TEMP_DIR / network_dir.name / fault_file.stem)
    shutil.copy(src / "powsybl_dynawo.jobs", config.OUTPUT_TEMP_DIR / network_dir.name / fault_file.stem)
    shutil.copy(src / "powsybl_dynawo.xiidm", config.OUTPUT_TEMP_DIR / network_dir.name / fault_file.stem)


def single_par_file(fault_file, network_dir) -> None:
    """ All par files in models.par file. EEAC does not work with several par files"""

    work = config.OUTPUT_TEMP_DIR / network_dir.name / fault_file.stem
    target_file = "models.par"
    source_files = [p.name for p in work.glob("*.par") if p.name not in (target_file, "network.par")]

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


def dyd_eeac(fault_data: Dict, dyd_file: Path, par_file: Path) -> None:
    """Modify .dyd file for a given fault to launch EEAC."""

    # parse .dyd files
    file_name = par_file.name
    tree, root = parse_xml(dyd_file)
    ns = root.nsmap.get("dyn")

    def q_name(namespace, tag):
        return f"{{{namespace}}}{tag}" if ns else tag

    if fault_data["fault_type"] == "Line":
        etree.SubElement(root, q_name(ns, "blackBoxModel"), {
            "id": "LineFault",
            "lib": "LineFault",
            "parFile": f"{file_name}",
            "parId": "Fault",
            "staticId": fault_data["static_id"]
        })
        etree.SubElement(root, q_name(ns, "blackBoxModel"), {
            "id": "DisconnectLine",
            "lib": "EventSetPointBoolean",
            "parFile": f"{file_name}",
            "parId": "Disconnect"
        })
        etree.SubElement(root, q_name(ns, "connect"), {
            "id1": "DisconnectLine",
            "var1": "event_state1_value",
            "id2": "LineFault",
            "var2": "line_switchOffSignal1_value"
        })
        etree.SubElement(root, q_name(ns, "connect"), {
            "id1": "LineFault",
            "var1": "line_terminal1",
            "id2": "NETWORK",
            "var2": f"{fault_data['terminal_1']}_ACPIN"
        })
        etree.SubElement(root, q_name(ns, "connect"), {
            "id1": "LineFault",
            "var1": "line_terminal2",
            "id2": "NETWORK",
            "var2": f"{fault_data['terminal_2']}_ACPIN"
        })

    elif fault_data["fault_type"] == "Transformer":

        # node fault in primary bus
        etree.SubElement(root, q_name(ns, "blackBoxModel"), {
            "id": "NodeFault",
            "lib": "NodeFault",
            "parFile": f"{file_name}",
            "parId": "Fault"
        })
        etree.SubElement(root, q_name(ns, "connect"), {
            "id1": "NodeFault",
            "var1": "fault_terminal",
            "id2": "NETWORK",
            "var2": f"{fault_data['terminal_1']}_ACPIN"
        })

        # transformer disconnect
        etree.SubElement(root, q_name(ns, "blackBoxModel"), {
            "id": "DisconnectTransformer",
            "lib": "EventQuadripoleDisconnection",
            "parFile": f"{file_name}",
            "parId": "Disconnect"
        })
        etree.SubElement(root, q_name(ns, "connect"), {
            "id1": "DisconnectTransformer",
            "var1": "event_state1_value",
            "id2": "NETWORK",
            "var2": f"{fault_data['static_id']}_state_value"
        })

    elif fault_data["fault_type"] == "Node":

        etree.SubElement(root, q_name(ns, "blackBoxModel"), {
            "id": "NodeFault",
            "lib": "NodeFault",
            "parFile": f"{file_name}",
            "parId": "Fault"
        })
        etree.SubElement(root, q_name(ns, "connect"), {
            "id1": "NodeFault",
            "var1": "fault_terminal",
            "id2": "NETWORK",
            "var2": f"{fault_data['terminal']}_ACPIN"
        })

    etree.indent(root, space="  ")
    tree.write(str(dyd_file), encoding="UTF-8", xml_declaration=True, pretty_print=True)


def par_eeac(fault_data: Dict, iidm_file: Path, par_file: Path) -> None:
    """Create .par file for a giver fault."""

    tree, root = parse_xml(par_file)

    if fault_data["fault_type"] == "Line":
        r, x, g, b = get_line_params(fault_data['static_id'], iidm_file)

        disconnect_set = etree.SubElement(root, "set", {"id": "Disconnect"})
        etree.SubElement(disconnect_set, "par", {
            "type": "DOUBLE",
            "name": "event_tEvent",
            "value": str(fault_data["t_disconnect"])
        })
        etree.SubElement(disconnect_set, "par", {
            "type": "BOOL",
            "name": "event_stateEvent1",
            "value": "true"
        })
        etree.SubElement(disconnect_set, "par", {
            "type": "BOOL",
            "name": "event_disconnectExtremity",
            "value": "true"
        })

        fault_set = etree.SubElement(root, "set", {"id": "Fault"})
        etree.SubElement(fault_set, "par", {
            "type": "DOUBLE",
            "name": "line_RPu",
            "value": str(r)
        })
        etree.SubElement(fault_set, "par", {
            "type": "DOUBLE",
            "name": "line_XPu",
            "value": str(x)
        })
        etree.SubElement(fault_set, "par", {
            "type": "DOUBLE",
            "name": "line_GPu",
            "value": str(g)
        })
        etree.SubElement(fault_set, "par", {
            "type": "DOUBLE",
            "name": "line_BPu",
            "value": str(b)
        })
        etree.SubElement(fault_set, "par", {
            "type": "DOUBLE",
            "name": "line_RFaultPu",
            "value": str(fault_data["r_fault"])
        })
        etree.SubElement(fault_set, "par", {
            "type": "DOUBLE",
            "name": "line_XFaultPu",
            "value": str(fault_data["x_fault"])
        })
        etree.SubElement(fault_set, "par", {
            "type": "DOUBLE",
            "name": "line_tBegin",
            "value": str(fault_data["t_begin_fault"])
        })
        etree.SubElement(fault_set, "par", {
            "type": "DOUBLE",
            "name": "line_tEnd",
            "value": str(fault_data["t_end_fault"])
        })
        etree.SubElement(fault_set, "par", {
            "type": "DOUBLE",
            "name": "line_D",
            "value": str(fault_data["d_terminal_1"])
        })

    elif fault_data["fault_type"] == "Transformer":

        # disconnect parameters
        disconnect_set = etree.SubElement(root, "set", {"id": "Disconnect"})
        etree.SubElement(disconnect_set, "par", {
            "type": "DOUBLE",
            "name": "event_tEvent",
            "value": str(fault_data["t_disconnect"])
        })
        etree.SubElement(disconnect_set, "par", {
            "type": "BOOL",
            "name": "event_stateEvent1",
            "value": "true"
        })
        etree.SubElement(disconnect_set, "par", {
            "type": "BOOL",
            "name": "event_disconnectExtremity",
            "value": "true"
        })
        etree.SubElement(disconnect_set, "par", {
            "type": "BOOL",
            "name": "event_disconnectOrigin",
            "value": "true"
        })

        # fault parameters
        fault_set = etree.SubElement(root, "set", {"id": "Fault"})
        etree.SubElement(fault_set, "par", {
            "type": "DOUBLE",
            "name": "fault_RPu",
            "value": str(fault_data["r_fault"])
        })
        etree.SubElement(fault_set, "par", {
            "type": "DOUBLE",
            "name": "fault_XPu",
            "value": str(fault_data["x_fault"])
        })
        etree.SubElement(fault_set, "par", {
            "type": "DOUBLE",
            "name": "fault_tBegin",
            "value": str(fault_data["t_begin_fault"])
        })
        etree.SubElement(fault_set, "par", {
            "type": "DOUBLE",
            "name": "fault_tEnd",
            "value": str(fault_data["t_end_fault"])
        })

    elif fault_data["fault_type"] == "Node":

        # fault parameters
        fault_set = etree.SubElement(root, "set", {"id": "Fault"})
        etree.SubElement(fault_set, "par", {
            "type": "DOUBLE",
            "name": "fault_RPu",
            "value": str(fault_data["r_fault"])
        })
        etree.SubElement(fault_set, "par", {
            "type": "DOUBLE",
            "name": "fault_XPu",
            "value": str(fault_data["x_fault"])
        })
        etree.SubElement(fault_set, "par", {
            "type": "DOUBLE",
            "name": "fault_tBegin",
            "value": str(fault_data["t_begin_fault"])
        })
        etree.SubElement(fault_set, "par", {
            "type": "DOUBLE",
            "name": "fault_tEnd",
            "value": str(fault_data["t_end_fault"])
        })

    etree.indent(root, space="  ")
    tree.write(str(par_file), encoding="UTF-8", xml_declaration=True, pretty_print=True)


def get_line_params(static_id, iidm_file) -> tuple[float | Any, float | Any, float | Any, float | Any] | None:
    """Extract R, X, G, B parameters from .iidm file."""

    ps_grid = pp.network.load(iidm_file)
    buses = ps_grid.get_buses(all_attributes=True)
    lines = ps_grid.get_lines(all_attributes=True)
    for i, row in lines.iterrows():
        if i==static_id:
            bus1 = buses.v_mag[row.bus1_id]
            bus2 = buses.v_mag[row.bus2_id]
            base_impedance: float = bus1 * bus2 / config.BASE_POWER
            r = row.r / base_impedance
            x = row.x / base_impedance
            g = row.g1 / base_impedance
            b = row.b1 / base_impedance
            return r, x, g, b
