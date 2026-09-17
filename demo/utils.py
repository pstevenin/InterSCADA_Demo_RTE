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

def modify_dyd_file(fault_type: str, fault_data: Dict, output_dir: Path, iidm_file: Path):
    """Modify .dyd file for a given fault."""
    file_name = iidm_file.stem
    dyd_file = output_dir / f"{file_name}.dyd"
    parser = etree.XMLParser(remove_blank_text=True)
    tree = etree.parse(str(dyd_file), parser)
    root = tree.getroot()
    ns = root.nsmap.get("dyn")

    if fault_type == "LineFault":
        etree.SubElement(root, f"{{{ns}}}blackBoxModel", {
            "id": "LineFault",
            "lib": "LineFault",
            "parFile": f"{file_name}.par",
            "parId": "Fault",
            "staticId": fault_data["static_id"]
        })
        etree.SubElement(root, f"{{{ns}}}blackBoxModel", {
            "id": "DisconnectLine",
            "lib": "EventSetPointBoolean",
            "parFile": f"{file_name}.par",
            "parId": "Disconnect"
        })
        etree.SubElement(root, f"{{{ns}}}connect", {
            "id1": "DisconnectLine",
            "var1": "event_state1_value",
            "id2": "LineFault",
            "var2": "line_switchOffSignal1_value"
        })
        etree.SubElement(root, f"{{{ns}}}connect", {
            "id1": "LineFault",
            "var1": "line_terminal1",
            "id2": "NETWORK",
            "var2": f"{fault_data['terminal_1']}_ACPIN"
        })
        etree.SubElement(root, f"{{{ns}}}connect", {
            "id1": "LineFault",
            "var1": "line_terminal2",
            "id2": "NETWORK",
            "var2": f"{fault_data['terminal_2']}_ACPIN"
        })

    elif fault_type == "TransformerFault":
        etree.SubElement(root, f"{{{ns}}}blackBoxModel", {
            "id": "NodeFault",
            "lib": "NodeFault",
            "parFile": f"{file_name}.par",
            "parId": "Fault"
        })
        etree.SubElement(root, f"{{{ns}}}blackBoxModel", {
            "id": "DisconnectTransformer",
            "lib": "EventQuadripoleDisconnection",
            "parFile": f"{file_name}.par",
            "parId": "Disconnect"
        })
        etree.SubElement(root, f"{{{ns}}}connect", {
            "id1": "NodeFault",
            "var1": "fault_terminal",
            "id2": "NETWORK",
            "var2": f"{fault_data['terminal_1']}_ACPIN"
        })
        etree.SubElement(root, f"{{{ns}}}connect", {
            "id1": "DisconnectTransformer",
            "var1": "event_state1_value",
            "id2": "NETWORK",
            "var2": f"{fault_data['static_id']}_state_value"
        })

    elif fault_type == "NodeFault":
        etree.SubElement(root, f"{{{ns}}}blackBoxModel", {
            "id": "NodeFault",
            "lib": "NodeFault",
            "parFile": f"{file_name}.par",
            "parId": "Fault"
        })
        etree.SubElement(root, f"{{{ns}}}connect", {
            "id1": "NodeFault",
            "var1": "fault_terminal",
            "id2": "NETWORK",
            "var2": f"{fault_data['terminal']}_ACPIN"
        })

    etree.indent(root, space="  ")
    tree.write(str(dyd_file), encoding="UTF-8", xml_declaration=True, pretty_print=True)

def modify_par_file(fault_type: str, fault_data: Dict, output_dir: Path, iidm_file: Path):
    """Create .par file for a giver fault."""
    file_name = iidm_file.stem
    par_file = output_dir / f"{file_name}.par"
    parser = etree.XMLParser(remove_blank_text=True)
    tree = etree.parse(str(par_file), parser)
    root = tree.getroot()

    if fault_type == "LineFault":
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

    elif fault_type == "TransformerFault":
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

    elif fault_type == "NodeFault":
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
    parser = etree.XMLParser(remove_blank_text=True)
    tree = etree.parse(str(file_path), parser)
    root = tree.getroot()
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