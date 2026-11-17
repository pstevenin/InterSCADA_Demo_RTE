import json
import logging
import pypowsybl as pp
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from lxml import etree
from pathlib import Path
from typing import List, Dict, Any, Tuple
from demo.config import config
from demo.utils import get_network_files, parse_xml, get_fault_files

logger = logging.getLogger(__name__)


def run_dynawo(network_dir: Path, faults_list2: List[str]) -> Dict:
    """Run dynawo for faults from list 2."""

    fault_files = [f for f in get_fault_files() if f.stem in faults_list2]
    # Parallel launches
    task = partial(_one_run_dynawo, network_dir=network_dir)
    with ThreadPoolExecutor(max_workers=config.CPU) as pool:
        outputs = list(pool.map(task, fault_files))
    result_cct = {}
    for key, result in outputs:
        result_cct[key] = result

    return result_cct


def _one_run_dynawo(fault_file: Path, network_dir: Path) -> Tuple[str, Dict]:
    """Dynawo simulation for one fault."""

    network_name = network_dir.name
    with open(fault_file) as f:
        fault_data = json.load(f)

    # Modify files to be compliant with dynawo
    iidm_file, dyd_file, jobs_file, par_file = get_network_files(
        config.OUTPUT_TEMP_DIR / network_dir.name / fault_file.stem
    )
    set_files_compliant(fault_data, iidm_file, dyd_file, par_file, jobs_file)

    # Calculate CCT
    logger.info(f"Calculating CCT for {network_name} - {fault_data['static_id']}")
    cct, gen_id = compute_cct(par_file, jobs_file)

    # find generator name
    if gen_id:
        tree, root = parse_xml(iidm_file)
        generators = root.xpath(
            f"//*[local-name()='generator' and @id='{gen_id}']/@name"
        )
        gen_name = generators[0] if generators else gen_id
    else:
        gen_name = "None"

    if cct < config.PROTECTION_DELAY:
        status = "NOK"
        margin = "None"
    elif cct < config.PROTECTION_DELAY + config.DELTA:
        status = "LOW_MARGIN"
        margin = cct - config.PROTECTION_DELAY
    else:
        status = "OK"
        margin = "None"

    result = {
        "status": status,
        "margin": margin,
        "CCT": cct,
        "critical_generator": gen_name,
    }

    return fault_data["static_id"], result


def set_files_compliant(fault_data, iidm_file: Path, dyd_file: Path, par_file: Path, jobs_file: Path) -> None:
    """Modify input files to be compliant with dynawo."""

    if fault_data["fault_type"] == "Line":
        line_fault_dynawo(fault_data, iidm_file, dyd_file, par_file)

    node_fault_dynawo(dyd_file, par_file, jobs_file)
    init_correction(dyd_file, par_file)


def line_fault_dynawo(fault_data: Dict, iidm_file: Path, dyd_file: Path, par_file: Path) -> None:
    """modify .iidm, .dyd and .par files to insert fault"""

    # modify .iidm file
    #-----------------

    # get useful network and fault parameters
    line_id = fault_data["static_id"]
    distance = fault_data["d_terminal_1"]
    network = pp.network.load(iidm_file)
    lines = network.get_lines()
    r = lines.loc[line_id]['r']
    x = lines.loc[line_id]['x']
    g1 = lines.loc[line_id]['g1']
    b1 = lines.loc[line_id]['b1']
    g2 = lines.loc[line_id]['g2']
    b2 = lines.loc[line_id]['b2']
    vl = network.get_voltage_levels(all_attributes=True)

    # create fictive substation
    network.create_substations(id=f"FictSub_{line_id}")

    # bus-breaker mode
    if vl.iloc[0,6]=="BUS_BREAKER":
        network.create_voltage_levels(id=f"FictVL_{line_id}",
                                      substation_id=f"FictSub_{line_id}",
                                      topology_kind='BUS_BREAKER',
                                      nominal_v=vl.loc[lines.loc[line_id]["voltage_level1_id"]]['nominal_v'])
        network.create_buses(id=f"FictBus_{line_id}",
                             voltage_level_id=f"FictVL_{line_id}")
        # remove current line and create new ones connecting fictive substation to network
        bus_1 = network.get_bus_breaker_topology(lines.loc[line_id]["voltage_level1_id"]).elements.loc[line_id,"bus_id"]
        bus_2 = network.get_bus_breaker_topology(lines.loc[line_id]["voltage_level2_id"]).elements.loc[line_id,"bus_id"]
        network.create_lines(id=line_id + '_0',
                             voltage_level1_id=lines.loc[line_id]["voltage_level1_id"],
                             bus1_id=bus_1,
                             voltage_level2_id=f"FictVL_{line_id}",
                             bus2_id=f"FictBus_{line_id}",
                             b1=b1, b2=b2, g1=g1, g2=g2, r=r * distance, x=x * distance)
        network.create_lines(id=line_id + '_1',
                             voltage_level1_id=f"FictVL_{line_id}",
                             bus1_id=f"FictBus_{line_id}",
                             voltage_level2_id=lines.loc[line_id]["voltage_level2_id"],
                             bus2_id=bus_2,
                             b1=b1, b2=b2, g1=g1, g2=g2, r=r * (1 - distance), x=x * (1 - distance))
        network.remove_elements(line_id)

    # node-breaker mode
    else:
        network.create_voltage_levels(id=f"FictVL_{line_id}",
                                      substation_id=f"FictSub_{line_id}",
                                      topology_kind='NODE_BREAKER',
                                      nominal_v=vl.loc[lines.loc[line_id]["voltage_level1_id"]]['nominal_v'])
        network.create_busbar_sections(id=f"FictBus_{line_id}",
                                       voltage_level_id=f"FictVL_{line_id}",
                                       node=0)
        # get nodes number of line
        node_topo_1 = network.get_node_breaker_topology(lines.loc[line_id]["voltage_level1_id"])
        node_line_id_1 = node_topo_1.nodes.loc[node_topo_1.nodes['connectable_id']==line_id].index[0]
        node_topo_2 = network.get_node_breaker_topology(lines.loc[line_id]["voltage_level2_id"])
        node_line_id_2 = node_topo_2.nodes.loc[node_topo_2.nodes['connectable_id'] == line_id].index[0]
        # remove current line and create new ones connecting fictive substation to network
        network.remove_elements(line_id)
        network.create_lines(id=line_id + '_0',
                             voltage_level1_id=lines.loc[line_id]["voltage_level1_id"],
                             node1=node_line_id_1,
                             voltage_level2_id=f"FictVL_{line_id}",
                             node2=1,
                             b1=b1, b2=b2, g1=g1, g2=g2, r=r * distance, x=x * distance)
        network.create_lines(id=line_id + '_1',
                             voltage_level1_id=f"FictVL_{line_id}",
                             node1=2,
                             voltage_level2_id=lines.loc[line_id]["voltage_level2_id"],
                             node2=node_line_id_2,
                             b1=b1, b2=b2, g1=g1, g2=g2, r=r * (1 - distance), x=x * (1 - distance))

    # save and replace .iidm file
    network.save(iidm_file, format="XIIDM", parameters=dict([("iidm.export.xml.version", "1.5")]))
    f = next(iidm_file.parent.glob("*.xiidm"))
    iidm_file.unlink()
    f.rename(f.with_suffix(".iidm"))

    # modify .dyd file
    #-----------------

    file_name = par_file.name
    tree, root = parse_xml(dyd_file)
    ns = root.nsmap.get("dyn")

    def q_name(namespace, tag):
        return f"{{{namespace}}}{tag}" if ns else tag

    # remove previous modification for EEAC
    ids_to_remove = {"LineFault", "DisconnectLine"}
    for bbm in root.findall(q_name(ns, "blackBoxModel")):
        if bbm.get("id") in ids_to_remove:
            root.remove(bbm)
    for conn in root.findall(q_name(ns, "connect")):
        if conn.get("id1") in ids_to_remove or conn.get("id2") in ids_to_remove:
            root.remove(conn)

    # add fault
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
        "var2": f"FictBus_{fault_data['static_id']}_ACPIN"
    })

    # add 1st line disconnection
    etree.SubElement(root, q_name(ns, "blackBoxModel"), {
        "id": "DisconnectLine_1",
        "lib": "EventQuadripoleDisconnection",
        "parFile": f"{file_name}",
        "parId": "Disconnect"
    })
    etree.SubElement(root, q_name(ns, "connect"), {
        "id1": "DisconnectLine_1",
        "var1": "event_state1",
        "id2": "NETWORK",
        "var2": f"{fault_data['static_id']}_0_state"
    })

    # add 2nd line disconnection
    etree.SubElement(root, q_name(ns, "blackBoxModel"), {
        "id": "DisconnectLine_2",
        "lib": "EventQuadripoleDisconnection",
        "parFile": f"{file_name}",
        "parId": "Disconnect"
    })
    etree.SubElement(root, q_name(ns, "connect"), {
        "id1": "DisconnectLine_2",
        "var1": "event_state1",
        "id2": "NETWORK",
        "var2": f"{fault_data['static_id']}_1_state"
    })

    etree.indent(root, space="  ")
    tree.write(dyd_file, pretty_print=True, encoding="utf-8", xml_declaration=True)

    # modify .par file
    # -----------------

    tree, root = parse_xml(par_file)

    # remove previous modifications for EEAC
    ids_to_remove = {"Fault", "Disconnect"}
    for s in root.findall("{*}set"):
        if s.get("id") in ids_to_remove:
            root.remove(s)

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
        "value": str(fault_data["x_fault"] + 0.0001)
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
    tree.write(par_file, pretty_print=True, encoding="utf-8", xml_declaration=True)


def node_fault_dynawo(dyd_file: Path, par_file: Path, jobs_file: Path) -> None:
    """input files modifications for node fault"""

    tree_dyd, root_dyd = parse_xml(dyd_file)
    tree_par, root_par = parse_xml(par_file)
    tree_jobs, root_jobs = parse_xml(jobs_file)

    # get parId for network parameters
    network_par_id = root_jobs.xpath("//*[local-name()='modeler']/*[local-name()='network']/@parId")[0]

    for elem_dyd in root_dyd.iter():

        # add _hasShortCircuitCapabilities property to node fault
        if elem_dyd.get("id1") == "NodeFault":
            node_id = elem_dyd.get("var2")
            for elem_par in root_par.iter():
                if elem_par.get("id") == network_par_id:
                    bus_name = node_id[:-6]
                    etree.SubElement(elem_par, "par", {
                        "name": f"{bus_name}_hasShortCircuitCapabilities",
                        "type": "BOOL",
                        "value": "true"
                    })

    # write files
    tree_dyd.write(dyd_file, pretty_print=True, encoding="utf-8", xml_declaration=True)
    tree_par.write(par_file, pretty_print=True, encoding="utf-8", xml_declaration=True)


def init_correction(dyd_file: Path, par_file: Path) -> None:
    """Dynawo initialization correction for VRSemipol2 models"""

    tree_dyd, root_dyd = parse_xml(dyd_file)
    tree_par, root_par = parse_xml(par_file)

    for elem_dyd in root_dyd.iter():

        # generator_iStart0Pu_re = 1 for GeneratorSynchronousFourWindingsGoverGt1VRSemipol2 generators
        if elem_dyd.get("lib") == "GeneratorSynchronousFourWindingsGoverGt1VRSemipol2":
            par_id = elem_dyd.get("parId")
            for elem_par in root_par.iter():
                if elem_par.get("id") == par_id:
                    already_exists = any(p.get("name") == "generator_iStart0Pu_re" for p in elem_par)
                    if not already_exists:
                        etree.SubElement(elem_par, "par", {
                            "name": "generator_iStart0Pu_re",
                            "type": "DOUBLE",
                            "value": "1."
                        })

    # write files
    tree_dyd.write(dyd_file, pretty_print=True, encoding="utf-8", xml_declaration=True)
    tree_par.write(par_file, pretty_print=True, encoding="utf-8", xml_declaration=True)


def compute_cct(par_file: Path, jobs_file: Path) -> tuple[float, str]:
    """Calculate CCT using dichotomy."""

    tree_par, root_par = parse_xml(par_file)
    tree_jobs, root_jobs = parse_xml(jobs_file)
    elements = root_par.xpath(".//*[@name='fault_tBegin' or @name='line_tBegin']")
    t_fault = float(elements[0].get("value"))
    l_bound = t_fault + config.LOW_BOUND
    h_bound = t_fault + config.HIGH_BOUND
    generator = None
    outputs_dir = root_jobs.xpath("//*[local-name()='outputs']/@directory")[0]
    log_file = root_jobs.xpath("//*[local-name()='outputs']//*[local-name()='appender']/@file")[0]

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
            logger.info(f"Simulation succeed for m = {m}")
        elif res == 1:
            h_bound = m
            logger.info(f"Simulation failed for m = {m}")
            generator = extract_gen(Path(par_file.parent, outputs_dir, "logs", log_file))

    cct = round(l_bound + (h_bound - l_bound) / 2, 3)
    update_par_file(par_file, cct)
    res = subprocess.run(
        f"{config.DYNAWO_DIR}/{config.DYNAWO_EXE} jobs {jobs_file}",
        capture_output=True,
        shell=True
    ).returncode
    if res == 1:
        cct -= config.GAP

    return cct-t_fault, generator


def update_par_file(file_path: Path, value: float) -> None:
    """Update tEnd and tEvent in .par file."""
    tree, root = parse_xml(file_path)
    t_fault = root.find(".//*[@name='fault_tEnd']")
    if t_fault is not None:
        t_fault.set("value", str(value))

    t_fault = root.find(".//*[@name='line_tEnd']")
    if t_fault is not None:
        t_fault.set("value", str(value))

    t_disconnect = root.find(".//*[@name='event_tEvent']")
    if t_disconnect is not None:
        t_disconnect.set("value", str(value))

    tree.write(str(file_path), encoding="UTF-8", xml_declaration=True, pretty_print=True)


def extract_gen(path: Path) -> str | None | Any:
    """Extract generator name from theta error in log file"""

    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    for i, line in enumerate(lines):

        if "ERROR" not in line:
            continue
        # Generator name: between "ERROR | " and " :"
        gen_name = re.search(r"\| ERROR \|\s*(\S+)\s*:", line)

        return gen_name.group(1) if gen_name else None


def find_name(gen_id: str, iidm_file: Path) -> str | None:
    """Find generator name based on id"""

    tree, root = parse_xml(iidm_file)
    generators = root.xpath(f"//*[local-name()='generator' and @id='{gen_id}']/@name")
    if not generators:
        return None

    gen = generators[0]
    return gen
