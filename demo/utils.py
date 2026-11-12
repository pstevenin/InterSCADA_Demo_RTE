import pypowsybl as pp
from demo.config import config
from lxml import etree
from pathlib import Path
from typing import Dict, Tuple, List

def get_line_params(static_id, iidm_file):
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

def get_network_files(network_dir: Path) -> Tuple[Path, Path, Path, Path]:
    """Returns file paths .iidm, .dyd, .jobs and .par in a folder."""
    iidm_file = next(network_dir.glob("*.iidm"), None)
    dyd_file = next(network_dir.glob("*.dyd"), None)
    jobs_file = next(network_dir.glob("*.jobs"), None)
    par_file = next(network_dir.glob("*.par"), None)

    if None in (iidm_file, dyd_file, jobs_file, par_file):
        raise FileNotFoundError(f"Files not found in {network_dir}")

    return iidm_file, dyd_file, jobs_file, par_file

def get_fault_files() -> List[Path]:
    """Returns the list of fault files in FAULT_DIR."""
    return list(config.FAULTS_DIR.glob("*.json"))

def parse_xml(xml_file: Path):
    """Parse xml file."""
    parser = etree.XMLParser(remove_blank_text=True)
    tree = etree.parse(xml_file, parser)
    root = tree.getroot()
    return tree, root

def set_files_compliant(fault_data, iidm_file: Path, dyd_file: Path, par_file: Path, jobs_file: Path) -> None:
    """Modify input files to be compliant with dynawo."""

    if fault_data["fault_type"] == "Line":
        line_fault_dynawo(fault_data, iidm_file, dyd_file, par_file)

    node_fault_dynawo(dyd_file, par_file, jobs_file)

def node_fault_dynawo(dyd_file: Path, par_file: Path, jobs_file: Path):
    """input files modifications for node fault"""

    tree_dyd, root_dyd = parse_xml(dyd_file)
    tree_par, root_par = parse_xml(par_file)
    tree_jobs, root_jobs = parse_xml(jobs_file)

    # get parId for network parameters
    ns = root_jobs.nsmap.get("dyn")
    if ns:
        network_par_id = root_jobs.xpath(f"//d:modeler/d:network/@parId", namespaces={"d": ns})[0]
    else:
        return None

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

def line_fault_dynawo(fault_data: Dict, iidm_file: Path, dyd_file: Path, par_file: Path):
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

def dyd_eeac(fault_data: Dict, dyd_file: Path, par_file: Path):
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

def par_eeac(fault_data: Dict, iidm_file: Path, par_file: Path):
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
