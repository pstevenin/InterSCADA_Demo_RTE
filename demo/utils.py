from lxml import etree
from pathlib import Path
from typing import Tuple, List
from demo.config import config


def get_fault_files() -> List[Path]:
    """Returns the list of fault files in FAULT_DIR."""

    return list(config.FAULTS_DIR.glob("*.json"))


def get_network_files(network_dir: Path) -> Tuple[Path, Path, Path, Path]:
    """Returns file paths .iidm, .dyd, .jobs and .par in a folder."""

    iidm_file = next(network_dir.glob("*.iidm"), None)
    dyd_file = next(network_dir.glob("*.dyd"), None)
    jobs_file = next(network_dir.glob("*.jobs"), None)
    par_file = next(network_dir.glob("*.par"), None)

    if None in (iidm_file, dyd_file, jobs_file, par_file):
        raise FileNotFoundError(f"Files not found in {network_dir}")

    return iidm_file, dyd_file, jobs_file, par_file


def parse_xml(xml_file: Path) -> tuple[etree, etree]:
    """Parse xml file."""

    parser = etree.XMLParser(remove_blank_text=True)
    tree = etree.parse(xml_file, parser)
    root = tree.getroot()
    return tree, root
